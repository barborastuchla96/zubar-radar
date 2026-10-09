"""Database side: schema setup, NRPZS upsert, and the 'near me' query."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Iterable

import psycopg
from psycopg.rows import dict_row

from .nrpzs import Provider, disambiguate_slugs

PROVIDER_COLS = (
    "nrpzs_place_id", "facility_id", "ico", "name", "facility_type", "street", "house_no",
    "city", "city_slug", "postcode", "district", "region", "lat", "lng",
    "phone", "email", "web", "care_form", "raw_specialties",
)

# Refuse to deactivate providers if this import has fewer than this share of the
# currently-active rows: protects against a truncated download wiping the site.
MIN_IMPORT_RATIO = 0.8


def init_schema(conn: psycopg.Connection) -> None:
    conn.execute(resources.files("radar").joinpath("schema.sql").read_text(encoding="utf-8"))
    conn.commit()


@dataclass
class ImportStats:
    upserted: int
    new: int
    deactivated: int
    skipped_deactivation: bool


def import_providers(conn: psycopg.Connection, providers: Iterable[Provider]) -> ImportStats:
    """Upsert providers in one transaction and deactivate ones no longer listed."""
    provs = disambiguate_slugs(list(providers))
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "CREATE TEMP TABLE stage (LIKE providers INCLUDING DEFAULTS) ON COMMIT DROP;"
            "ALTER TABLE stage DROP COLUMN id;"
            "CREATE TEMP TABLE stage_spec (nrpzs_place_id text, specialty_slug text) ON COMMIT DROP;"
        )
        with cur.copy(f"COPY stage ({', '.join(PROVIDER_COLS)}) FROM STDIN") as cp:
            for p in provs:
                cp.write_row((
                    p.place_id, p.facility_id, p.ico, p.name, p.facility_type, p.street,
                    p.house_no, p.city, p.city_slug, p.postcode, p.district, p.region,
                    p.lat, p.lng, p.phone, p.email, p.web, p.care_form, p.raw_specialties,
                ))
        with cur.copy("COPY stage_spec FROM STDIN") as cp:
            for p in provs:
                for s in sorted(p.specialties):
                    cp.write_row((p.place_id, s))

        cur.execute("SELECT count(*) FROM providers WHERE active")
        (active_before,) = cur.fetchone()

        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in PROVIDER_COLS if c != "nrpzs_place_id")
        cur.execute(
            f"""
            INSERT INTO providers ({', '.join(PROVIDER_COLS)})
            SELECT {', '.join(PROVIDER_COLS)} FROM stage
            ON CONFLICT (nrpzs_place_id) DO UPDATE
               SET {updates}, active = true, last_seen_at = now()
            RETURNING (xmax = 0) AS inserted
            """
        )
        flags = [r[0] for r in cur.fetchall()]

        cur.execute(
            """
            DELETE FROM provider_specialties ps USING providers p, stage s
             WHERE ps.provider_id = p.id AND p.nrpzs_place_id = s.nrpzs_place_id;
            INSERT INTO provider_specialties (provider_id, specialty_slug)
            SELECT p.id, ss.specialty_slug
              FROM stage_spec ss JOIN providers p USING (nrpzs_place_id)
            ON CONFLICT DO NOTHING;
            """
        )

        skip = active_before > 0 and len(provs) < MIN_IMPORT_RATIO * active_before
        deactivated = 0
        if not skip:
            cur.execute(
                """
                UPDATE providers SET active = false
                 WHERE active AND nrpzs_place_id NOT IN (SELECT nrpzs_place_id FROM stage)
                """
            )
            deactivated = cur.rowcount
    return ImportStats(len(flags), sum(flags), deactivated, skip)


def near(
    conn: psycopg.Connection, specialty: str, lat: float, lng: float, km: float = 10, limit: int = 20
) -> list[dict]:
    """Active providers of a specialty within `km`, accepting ones first, then by distance."""
    sql = """
        SELECT p.id, p.name, p.street, p.house_no, p.city, p.phone, p.web,
               round(distance_km(%(lat)s, %(lng)s, p.lat, p.lng)::numeric, 2) AS km,
               coalesce(s.status, 'unknown') AS status, s.last_signal_at
          FROM providers p
          JOIN provider_specialties ps ON ps.provider_id = p.id AND ps.specialty_slug = %(spec)s
          LEFT JOIN provider_status s ON s.provider_id = p.id
         WHERE p.active AND p.lat IS NOT NULL
           AND earth_box(ll_to_earth(%(lat)s, %(lng)s), %(m)s) @> ll_to_earth(p.lat, p.lng)
           AND earth_distance(ll_to_earth(%(lat)s, %(lng)s), ll_to_earth(p.lat, p.lng)) <= %(m)s
         ORDER BY CASE coalesce(s.status, 'unknown')
                    WHEN 'accepting' THEN 0 WHEN 'mixed' THEN 1
                    WHEN 'unknown' THEN 2 ELSE 3 END,
                  km
         LIMIT %(limit)s
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"spec": specialty, "lat": lat, "lng": lng, "m": km * 1000, "limit": limit})
        return cur.fetchall()


def crawl_targets(
    conn: psycopg.Connection, city_regex: str, specialties: list[str] | None = None, limit: int | None = None
) -> dict[str, list[int]]:
    """Normalized clinic website URL -> ids of active providers listing it."""
    from .crawler import normalize_site

    rows = conn.execute(
        """
        SELECT DISTINCT p.id, p.web
          FROM providers p JOIN provider_specialties ps ON ps.provider_id = p.id
         WHERE p.active AND p.web IS NOT NULL AND p.city_slug ~ %s
           AND (%s::text[] IS NULL OR ps.specialty_slug = ANY(%s::text[]))
         ORDER BY p.id
        """,
        (city_regex, specialties, specialties),
    ).fetchall()
    sites: dict[str, list[int]] = {}
    for pid, web in rows:
        url = normalize_site(web)
        if url:
            sites.setdefault(url, []).append(pid)
    # One site for many practices is a hospital or chain homepage; what it says can't be
    # pinned to one practice, so leave those to patients' reports.
    sites = {u: ids for u, ids in sites.items() if len(ids) <= MAX_PRACTICES_PER_SITE}
    if limit is not None:
        sites = dict(list(sites.items())[:limit])
    return sites


RECHECK_DAYS = 6
MAX_PRACTICES_PER_SITE = 5


def record_crawl(conn: psycopg.Connection, results) -> int:
    """Store crawl verdicts as web_crawl signals. Skips repeats of an unchanged
    verdict within RECHECK_DAYS so weekly runs don't pile up duplicates."""
    written = 0
    with conn.transaction():
        for r in results:
            v = r.verdict
            if not v or not v.status:
                continue
            note = f"„{v.snippet[:300]}“ — {r.page_url}"[:500]
            for pid in r.provider_ids:
                # Same verdict seen recently: just refresh its quote (e.g. after a wording fix).
                conn.execute(
                    """
                    UPDATE availability_signals SET note = %(note)s, scope = %(scope)s
                     WHERE provider_id = %(pid)s AND source = 'web_crawl' AND status = %(status)s
                       AND observed_at > now() - make_interval(days => %(days)s)
                    """,
                    {"pid": pid, "status": v.status, "scope": v.scope, "note": note, "days": RECHECK_DAYS},
                )
                cur = conn.execute(
                    """
                    INSERT INTO availability_signals (provider_id, source, status, scope, note)
                    SELECT %(pid)s, 'web_crawl', %(status)s, %(scope)s, %(note)s
                     WHERE NOT EXISTS (
                           SELECT 1 FROM availability_signals
                            WHERE provider_id = %(pid)s AND source = 'web_crawl' AND status = %(status)s
                              AND observed_at > now() - make_interval(days => %(days)s))
                    """,
                    {"pid": pid, "status": v.status, "scope": v.scope, "note": note, "days": RECHECK_DAYS},
                )
                written += cur.rowcount
    return written
