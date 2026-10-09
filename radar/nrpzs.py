"""Parse the NRPZS open-data CSV ("Místa poskytování zdravotních služeb").

ÚZIS renamed the columns in a late-2025 update (e.g. ZdravotnickeZarizeniId
-> ZZ_ID, OborPece -> ZZ_obor_pece), so headers are resolved through a list of
known aliases instead of being hard-coded. Run `python -m radar inspect FILE`
to see which header each field resolved to, and pass `--map field=Header` to
override anything that didn't match.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

# Canonical field -> header aliases, old names first. Matching ignores case,
# diacritics, underscores and spaces. Only ZZ_ID and ZZ_obor_pece are
# confirmed new-format names; the other ZZ_* aliases are educated guesses.
FIELD_ALIASES: dict[str, list[str]] = {
    "place_id":      ["MistoPoskytovaniId", "MP_ID", "MistoPoskytovani_ID"],
    "facility_id":   ["ZdravotnickeZarizeniId", "ZZ_ID"],
    "name":          ["NazevCely", "NazevZarizeni", "ZZ_nazev", "ZZ_nazev_cely", "Nazev"],
    "facility_type": ["DruhZarizeni", "ZZ_druh", "ZZ_druh_zarizeni"],
    "city":          ["Obec", "ZZ_obec", "MP_obec"],
    "postcode":      ["Psc", "ZZ_psc", "MP_psc"],
    "street":        ["Ulice", "ZZ_ulice", "MP_ulice"],
    "house_no":      ["CisloDomovniOrientacni", "ZZ_cislo_domovni_orientacni", "MP_cislo"],
    "district":      ["Okres", "ZZ_okres", "MP_okres"],
    "region":        ["Kraj", "ZZ_kraj", "MP_kraj"],
    "phone":         ["PoskytovatelTelefon", "ZZ_telefon", "Telefon"],
    "email":         ["PoskytovatelEmail", "ZZ_email", "Email"],
    "web":           ["PoskytovatelWeb", "ZZ_web", "Web"],
    "ico":           ["Ico", "ICO", "Poskytovatel_ICO", "P_ico"],
    "specialties":   ["OborPece", "ZZ_obor_pece"],
    "care_form":     ["FormaPece", "ZZ_forma_pece"],
    "lat":           ["Lat", "GPS_lat", "ZZ_lat", "Latitude"],
    "lng":           ["Lng", "Lon", "GPS_lng", "ZZ_lng", "Longitude"],
    "gps":           ["GPS", "ZZ_GPS"],  # combined "50.08 14.42" fallback
}
REQUIRED = ("name", "specialties")

# NRPZS "obor péče" label (lower-case) -> our specialty slug. Keep in sync
# with specialties.nrpzs_labels in schema.sql.
SPECIALTY_LABELS: dict[str, str] = {
    "zubní lékařství": "zubar",
    "všeobecné praktické lékařství": "praktik",
    "praktické lékařství pro děti a dorost": "pediatr",
    "gynekologie a porodnictví": "gynekolog",
    "dentální hygiena": "hygienistka",
    "dentální hygienistka": "hygienistka",
}

# Rough bounding box of Czechia, to drop garbage coordinates.
CZ_LAT = (48.5, 51.1)
CZ_LNG = (12.0, 18.9)


def _norm_header(h: str) -> str:
    h = unicodedata.normalize("NFKD", h).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", h.lower())


def slugify(text: str) -> str:
    """'Praha 6' -> 'praha-6', 'Ústí nad Labem' -> 'usti-nad-labem'."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def resolve_columns(headers: list[str], overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Map canonical field -> actual header present in the file."""
    by_norm = {_norm_header(h): h for h in headers}
    resolved: dict[str, str] = {}
    for fld, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if (hit := by_norm.get(_norm_header(alias))) is not None:
                resolved[fld] = hit
                break
    for fld, header in (overrides or {}).items():
        if fld not in FIELD_ALIASES:
            raise ValueError(f"unknown field {fld!r}; known: {', '.join(FIELD_ALIASES)}")
        if header not in headers:
            raise ValueError(f"--map {fld}={header}: no such column in file")
        resolved[fld] = header
    missing = [f for f in REQUIRED if f not in resolved]
    if missing:
        raise ValueError(
            f"could not find column(s) for {missing}. Run `inspect` and pass --map field=Header."
        )
    return resolved


def parse_specialties(raw: str) -> set[str]:
    """'Zubní lékařství, ortodoncie' -> {'zubar'}."""
    labels = (p.strip().lower() for p in re.split(r"[,;|]", raw or ""))
    return {SPECIALTY_LABELS[l] for l in labels if l in SPECIALTY_LABELS}


def _parse_float(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(s.strip().replace(",", "."))
    except ValueError:
        return None


def parse_coords(lat: str | None, lng: str | None, gps: str | None) -> tuple[float | None, float | None]:
    la, ln = _parse_float(lat), _parse_float(lng)
    if (la is None or ln is None) and gps:
        parts = re.split(r"[\s;]+|,\s+", gps.strip())
        if len(parts) == 2:
            la, ln = _parse_float(parts[0]), _parse_float(parts[1])
    if la is None or ln is None:
        return None, None
    if not (CZ_LAT[0] <= la <= CZ_LAT[1] and CZ_LNG[0] <= ln <= CZ_LNG[1]):
        # Some exports swap lat/lng; accept the swap if it lands inside CZ.
        if CZ_LAT[0] <= ln <= CZ_LAT[1] and CZ_LNG[0] <= la <= CZ_LNG[1]:
            return ln, la
        return None, None
    return la, ln


@dataclass
class Provider:
    place_id: str
    name: str
    specialties: set[str]
    raw_specialties: str
    facility_id: str | None = None
    ico: str | None = None
    facility_type: str | None = None
    street: str | None = None
    house_no: str | None = None
    city: str | None = None
    city_slug: str | None = None
    postcode: str | None = None
    district: str | None = None
    region: str | None = None
    lat: float | None = None
    lng: float | None = None
    phone: str | None = None
    email: str | None = None
    web: str | None = None
    care_form: str | None = None
    _merged_labels: list[str] = field(default_factory=list, repr=False)


def disambiguate_slugs(provs: list[Provider]) -> list[Provider]:
    """Towns that share a name ("Benešov" in two regions) must not share a page.

    The district with the most practices keeps the plain slug, so existing URLs stay;
    the others get the district appended: benesov-blansko.
    """
    by_slug: dict[str, Counter] = defaultdict(Counter)
    for p in provs:
        if p.city_slug and p.district:
            by_slug[p.city_slug][p.district] += 1
    for p in provs:
        districts = by_slug.get(p.city_slug or "")
        if districts and len(districts) > 1:
            main = max(districts.items(), key=lambda kv: (kv[1], kv[0]))[0]
            if p.district and p.district != main:
                p.city_slug = f"{p.city_slug}-{slugify(p.district)}"
    return provs


def open_csv(path: Path) -> tuple[csv.DictReader, io.TextIOBase]:
    """Open with encoding + delimiter sniffing (UTF-8 with/without BOM, or cp1250)."""
    raw = path.read_bytes()[:256_000]
    for enc in ("utf-8-sig", "cp1250"):
        try:
            sample = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("file is neither UTF-8 nor cp1250")
    try:
        delim = csv.Sniffer().sniff(sample.split("\n", 1)[0], delimiters=",;\t|").delimiter
    except csv.Error:
        delim = ","
    fh = path.open(encoding=enc, newline="")
    return csv.DictReader(fh, delimiter=delim), fh


def _clean(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


def iter_providers(
    rows: Iterable[dict[str, str]], cols: dict[str, str], only_tracked: bool = True
) -> Iterator[Provider]:
    """Yield one Provider per place. Rows for the same place are merged."""
    by_place: dict[str, Provider] = {}
    for row in rows:
        g = lambda f: _clean(row.get(cols[f])) if f in cols else None  # noqa: E731
        name = g("name")
        raw_spec = g("specialties") or ""
        if not name:
            continue
        # No place id column in some exports: fall back to facility + address.
        place_id = g("place_id") or "|".join(
            filter(None, [g("facility_id") or g("ico") or name, g("city"), g("street"), g("house_no")])
        )
        if (p := by_place.get(place_id)) is not None:
            p.specialties |= parse_specialties(raw_spec)
            if raw_spec and raw_spec not in p._merged_labels:
                p._merged_labels.append(raw_spec)
                p.raw_specialties = ", ".join(p._merged_labels)
            continue
        lat, lng = parse_coords(g("lat"), g("lng"), g("gps"))
        city = g("city")
        p = Provider(
            place_id=place_id, name=name,
            specialties=parse_specialties(raw_spec), raw_specialties=raw_spec,
            facility_id=g("facility_id"), ico=g("ico"), facility_type=g("facility_type"),
            street=g("street"), house_no=g("house_no"),
            city=city, city_slug=slugify(city) if city else None,
            postcode=(g("postcode") or "").replace(" ", "") or None,
            district=g("district"), region=g("region"), lat=lat, lng=lng,
            phone=g("phone"), email=g("email"), web=g("web"), care_form=g("care_form"),
            _merged_labels=[raw_spec] if raw_spec else [],
        )
        by_place[place_id] = p
    for p in by_place.values():
        if p.specialties or not only_tracked:
            yield p
