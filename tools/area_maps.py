"""Generate web/src/lib/areas.json: zoomed maps of each region (with its districts) and of each district.

Same source and projection as tools/region_map.py (ČÚZK INSPIRE via siwekm/czech-geojson, CC BY 4.0).
Usage:  python tools/area_maps.py [kraje.json okresy.json]
"""

from __future__ import annotations

import json
import math
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from region_map import WIDTH, douglas_peucker, inside, label_point, slugify  # noqa: E402

BASE = "https://raw.githubusercontent.com/siwekm/czech-geojson/master/"
OUT = Path(__file__).resolve().parent.parent / "web/src/lib/areas.json"
MAX_W, MAX_H, PAD = 640, 460, 8
TOLERANCE = 0.8            # in local (zoomed) units
PRAGUE_OKRES = "území Hlavního města Prahy"


def load(name: str, path: str | None):
    return json.loads(Path(path).read_text() if path else urllib.request.urlopen(BASE + name, timeout=120).read())


def rings_of(f):
    g = f["geometry"]
    polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    return [r for p in polys for r in p]


def main() -> None:
    kraje = load("kraje.json", sys.argv[1] if len(sys.argv) > 2 else None)["features"]
    okresy = load("okresy.json", sys.argv[2] if len(sys.argv) > 2 else None)["features"]

    lons = [x for f in kraje for r in rings_of(f) for x, _ in r]
    lats = [y for f in kraje for r in rings_of(f) for _, y in r]
    lon0, lat1 = min(lons), max(lats)
    k = math.cos(math.radians((min(lats) + lat1) / 2))
    scale = WIDTH / ((max(lons) - lon0) * k)
    nat = lambda lon, lat: ((lon - lon0) * k * scale, (lat1 - lat) * scale)

    def frame(rings_nat):
        xs = [x for r in rings_nat for x, _ in r]
        ys = [y for r in rings_nat for _, y in r]
        bw, bh = max(xs) - min(xs), max(ys) - min(ys)
        z = min((MAX_W - 2 * PAD) / bw, (MAX_H - 2 * PAD) / bh)
        w, h = round(bw * z + 2 * PAD), round(bh * z + 2 * PAD)
        return {"x0": min(xs), "y0": min(ys), "z": z, "pad": PAD, "viewBox": f"0 0 {w} {h}"}

    def local(rings_nat, fr):
        out = []
        for r in rings_nat:
            pts = [((x - fr["x0"]) * fr["z"] + PAD, (y - fr["y0"]) * fr["z"] + PAD) for x, y in r]
            pts = douglas_peucker(pts, TOLERANCE)
            out.append(pts[:-1] if pts[0] == pts[-1] else pts)
        return out

    path_d = lambda rings: " ".join("M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in r) + " Z" for r in rings)

    kraj_nat = {f["name"]: [[nat(*p) for p in r] for r in rings_of(f)] for f in kraje}
    okres_nat = {f["name"]: [[nat(*p) for p in r] for r in rings_of(f)] for f in okresy}

    # Which region each district belongs to: the region containing the district's inner point.
    okres_kraj = {}
    for name, rings in okres_nat.items():
        coarse = douglas_peucker(rings[0], 0.3)
        px, py = label_point(coarse, [])
        okres_kraj[name] = next(kn for kn, kr in kraj_nat.items() if inside(px, py, kr[0]) and
                                not any(inside(px, py, h) for h in kr[1:]))

    result = {"proj": {"lon0": lon0, "lat1": lat1, "k": k, "scale": scale}, "kraje": {}, "okresy": {}}
    for kname, krings in kraj_nat.items():
        if kname == "Hlavní město Praha":
            continue
        fr = frame([krings[0]])
        areas = []
        for oname, orings in okres_nat.items():
            if okres_kraj[oname] != kname and not (oname == PRAGUE_OKRES and kname == "Středočeský kraj"):
                continue
            loc = local(orings, fr)               # with holes: Brno-venkov surrounds Brno-město
            lx, ly = label_point(loc[0], loc[1:])
            prague = oname == PRAGUE_OKRES
            areas.append({"name": "Praha" if prague else oname, "slug": "praha" if prague else slugify(oname),
                          "label": "Praha" if prague else oname.removesuffix("-město"),
                          "d": path_d(loc), "lx": round(lx, 1), "ly": round(ly, 1)})
        areas.sort(key=lambda a: a["slug"] == "praha")      # Prague on top
        result["kraje"][slugify(kname)] = {"name": kname, **fr, "okresy": areas}
        print(f"{kname:<22} {len(areas):>2} districts", file=sys.stderr)

    for oname, orings in okres_nat.items():
        if oname == PRAGUE_OKRES:
            continue
        fr = frame([orings[0]])
        result["okresy"][slugify(oname)] = {"name": oname, "kraj": slugify(okres_kraj[oname]),
                                            **fr, "d": path_d(local(orings, fr))}

    # Prague alone, for the Praha 1–22 map on the Prague page.
    fr = frame([okres_nat[PRAGUE_OKRES][0]])
    result["praha"] = {"name": "Praha", **fr, "d": path_d(local(okres_nat[PRAGUE_OKRES], fr))}

    OUT.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} kB)", file=sys.stderr)


if __name__ == "__main__":
    main()
