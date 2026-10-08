"""CLI: python -m radar {initdb,inspect,import,near,report}"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import urllib.request
from collections import Counter
from pathlib import Path

from . import nrpzs


def _dsn(args) -> str:
    dsn = args.dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("set DATABASE_URL or pass --dsn")
    return dsn


def _parse_map(pairs: list[str]) -> dict[str, str]:
    out = {}
    for pair in pairs or []:
        fld, sep, header = pair.partition("=")
        if not sep:
            sys.exit(f"--map expects field=Header, got {pair!r}")
        out[fld.strip()] = header.strip()
    return out


def _fetch(src: str) -> Path:
    """Local path as-is; http(s) URL downloaded to a temp file."""
    if not src.startswith(("http://", "https://")):
        return Path(src)
    tmp = Path(tempfile.mkstemp(suffix=".csv", prefix="nrpzs-")[1])
    print(f"downloading {src} ...", file=sys.stderr)
    req = urllib.request.Request(src, headers={"User-Agent": "zubar-radar/0.1 (+contact@example.cz)"})
    with urllib.request.urlopen(req, timeout=120) as r, tmp.open("wb") as f:
        shutil.copyfileobj(r, f)
    return tmp


def cmd_inspect(args) -> None:
    reader, fh = nrpzs.open_csv(_fetch(args.source))
    with fh:
        headers = reader.fieldnames or []
        print(f"delimiter={reader.reader.dialect.delimiter!r}  columns={len(headers)}\n")
        try:
            cols = nrpzs.resolve_columns(headers, _parse_map(args.map))
        except ValueError as e:
            cols = {}
            print(f"!! {e}\n")
        used = set(cols.values())
        for fld in nrpzs.FIELD_ALIASES:
            print(f"  {fld:<14} <- {cols.get(fld, '(not found)')}")
        print("\nunmapped columns:", ", ".join(h for h in headers if h not in used) or "-")
        if not cols:
            return
        labels: Counter[str] = Counter()
        for row in reader:
            for lbl in (row.get(cols["specialties"]) or "").split(","):
                labels[lbl.strip().lower()] += 1
        print("\ntop 'obor péče' labels (tracked ones marked *):")
        for lbl, n in labels.most_common(args.top):
            mark = "*" if lbl in nrpzs.SPECIALTY_LABELS else " "
            print(f" {mark} {n:>7}  {lbl}")


def cmd_initdb(args) -> None:
    import psycopg
    from .db import init_schema
    with psycopg.connect(_dsn(args)) as conn:
        init_schema(conn)
    print("schema ready")


def cmd_import(args) -> None:
    import psycopg
    from .db import import_providers
    reader, fh = nrpzs.open_csv(_fetch(args.source))
    with fh:
        cols = nrpzs.resolve_columns(reader.fieldnames or [], _parse_map(args.map))
        provs = list(nrpzs.iter_providers(reader, cols))
    by_spec = Counter(s for p in provs for s in p.specialties)
    no_gps = sum(p.lat is None for p in provs)
    print(f"parsed {len(provs)} tracked places: {dict(by_spec)}; without GPS: {no_gps}")
    if args.dry_run:
        return
    with psycopg.connect(_dsn(args)) as conn:
        st = import_providers(conn, provs)
    print(f"upserted {st.upserted} (new {st.new}), deactivated {st.deactivated}")
    if st.skipped_deactivation:
        print("!! import much smaller than current data; deactivation skipped (truncated file?)")


def cmd_near(args) -> None:
    import psycopg
    from .db import near
    with psycopg.connect(_dsn(args)) as conn:
        rows = near(conn, args.specialty, args.lat, args.lng, args.km, args.limit)
    for r in rows:
        addr = " ".join(filter(None, [r["street"], r["house_no"], r["city"]]))
        print(f"{r['km']:>6} km  [{r['status']:<13}] {r['name']} — {addr}  {r['phone'] or ''}")
    if not rows:
        print("nothing found")


def cmd_report(args) -> None:
    """Record an availability signal (stand-in for the web form / clinic dashboard)."""
    import psycopg
    with psycopg.connect(_dsn(args)) as conn:
        conn.execute(
            "INSERT INTO availability_signals (provider_id, source, status, scope, note)"
            " VALUES (%s, %s, %s, %s, %s)",
            (args.provider_id, args.source, args.status, args.scope, args.note),
        )
    print("recorded")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="radar", description="Zubař radar data tools")
    ap.add_argument("--dsn", help="Postgres DSN (default: $DATABASE_URL)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("inspect", help="show column mapping + specialty labels of an NRPZS CSV")
    p.add_argument("source", help="path or URL")
    p.add_argument("--map", action="append", metavar="FIELD=Header")
    p.add_argument("--top", type=int, default=40)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("initdb", help="create/upgrade the schema")
    p.set_defaults(func=cmd_initdb)

    p = sub.add_parser("import", help="import an NRPZS CSV")
    p.add_argument("source", help="path or URL")
    p.add_argument("--map", action="append", metavar="FIELD=Header")
    p.add_argument("--dry-run", action="store_true", help="parse only, don't touch the DB")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("near", help="find providers near a point")
    p.add_argument("--specialty", default="zubar")
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lng", type=float, required=True)
    p.add_argument("--km", type=float, default=10)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_near)

    p = sub.add_parser("report", help="record an availability signal")
    p.add_argument("provider_id", type=int)
    p.add_argument("status", choices=["accepting", "not_accepting", "waitlist"])
    p.add_argument("--source", default="user", choices=["clinic", "region", "user", "web_crawl"])
    p.add_argument("--scope", default="all", choices=["adults", "children", "all"])
    p.add_argument("--note")
    p.set_defaults(func=cmd_report)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
