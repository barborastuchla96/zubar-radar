"""CLI: python -m radar {initdb,inspect,import,near,report,sponsor,crawl}"""

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


def cmd_sponsor(args) -> None:
    """Manage directly-sold sponsored listings."""
    import psycopg
    with psycopg.connect(_dsn(args)) as conn:
        if args.action == "add":
            row = conn.execute(
                "INSERT INTO sponsored_listings (provider_id, specialty_slug, city_slug, tagline, starts_on, ends_on)"
                " SELECT p.id, %s, coalesce(%s, p.city_slug), %s, coalesce(%s::date, current_date), %s::date"
                "   FROM providers p WHERE p.id = %s RETURNING id, city_slug",
                (args.specialty, args.city, args.tagline, args.start, args.end, args.provider_id),
            ).fetchone()
            if row is None:
                sys.exit(f"no provider {args.provider_id}")
            print(f"sponsor #{row[0]} on /{args.specialty}/{row[1]} until {args.end}")
        elif args.action == "end":
            cur = conn.execute(
                "UPDATE sponsored_listings"
                "   SET ends_on = current_date - 1, starts_on = least(starts_on, current_date - 1)"
                " WHERE id = %s", (args.id,))
            print("ended" if cur.rowcount else f"no sponsor #{args.id}")
        else:
            for r in conn.execute(
                "SELECT s.id, p.name, s.specialty_slug, s.city_slug, s.starts_on, s.ends_on"
                "  FROM sponsored_listings s JOIN providers p ON p.id = s.provider_id"
                " WHERE s.ends_on >= current_date ORDER BY s.ends_on"
            ):
                print(f"#{r[0]:<4} {r[2]}/{r[3]:<20} {r[4]} → {r[5]}  {r[1]}")


def cmd_crawl(args) -> None:
    """Check clinic websites for "přijímáme / nepřijímáme nové pacienty"."""
    import psycopg
    from . import crawler
    from .db import crawl_targets, record_crawl

    with psycopg.connect(_dsn(args)) as conn:
        sites = crawl_targets(conn, args.city, args.specialty, args.limit, forget_dropped=not args.dry_run)
    n_prov = sum(len(v) for v in sites.values())
    print(f"checking {len(sites)} websites ({n_prov} practices), {args.workers} at a time…", flush=True)

    stats: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    done = 0

    def progress(r):
        nonlocal done
        done += 1
        v = r.verdict
        key = r.error and "error" or (v.status if v and v.status else ("conflicting" if v and v.conflicting else "nothing"))
        stats[key] += 1
        if r.error:
            errors[crawler.error_kind(r.error)] += 1
        if key in ("accepting", "not_accepting", "waitlist", "conflicting") or (args.verbose and r.error):
            detail = r.error or f"[{v.scope}] {v.snippet[:110]}"
            print(f"  {key:<13} {r.page_url or r.url}\n                {detail}", flush=True)
        elif done % 50 == 0:
            print(f"  … {done}/{len(sites)}", flush=True)
        ins = sorted(f[3:] for f in r.flags if f.startswith("ins"))
        for flag, snippet in r.flags.items():
            if flag.startswith("ins"):
                continue
            stats[flag] += 1
            print(f"  {flag:<13} {r.page_url or r.url}\n                {snippet[:110]}", flush=True)
        if ins:
            stats["insurers"] += 1

    # Save in batches, so a crash or restart halfway through a long run keeps what was found.
    written = 0
    items = list(sites.items())
    for start in range(0, len(items), 300):
        results = crawler.crawl(dict(items[start:start + 300]), workers=args.workers, progress=progress)
        if not args.dry_run:
            with psycopg.connect(_dsn(args)) as conn:
                written += record_crawl(conn, results)
    print("\nsummary: " + ", ".join(f"{k} {stats[k]}" for k in
          ("accepting", "waitlist", "not_accepting", "conflicting", "nothing", "error", "self_pay", "english", "insurers")))
    if errors:
        print("errors by type: " + ", ".join(f"{k} {n}" for k, n in errors.most_common(10)))
    print("dry run: nothing written" if args.dry_run else f"wrote {written} web_crawl signals")


def cmd_discover_web(args) -> None:
    """Find practice websites the register doesn't list (own e-mail domains, visitors' suggestions)."""
    import psycopg
    from . import discover

    with psycopg.connect(_dsn(args)) as conn:
        cands = discover.candidates(conn, args.limit)
    n = sum(len(c.providers) for c in cands)
    print(f"trying {len(cands)} addresses for {n} practices without a website…", flush=True)
    stats: Counter[str] = Counter()

    def progress(r):
        key = "found" if r.matched else ("error" if r.error else "not theirs")
        stats[key] += 1
        stats[f"{key} ({r.candidate.source})"] += 1
        if r.matched:
            names = ", ".join(p[1] for p in r.candidate.providers if p[0] in r.matched)[:90]
            print(f"  found  {r.url}  ← {names}", flush=True)
        elif args.verbose:
            print(f"  {key:<10} {r.candidate.url}  {r.error or ''}", flush=True)

    results = discover.run(cands, workers=args.workers, progress=progress)
    print("summary: " + ", ".join(f"{k} {v}" for k, v in sorted(stats.items())))
    if args.dry_run:
        print("dry run: nothing written")
        return
    with psycopg.connect(_dsn(args)) as conn:
        print(f"saved websites for {discover.record(conn, results)} practices")


def cmd_digest(args) -> None:
    """Evening summary for the site owner: today's visitor reports."""
    import psycopg
    from . import alerts, digest
    site = os.environ.get("SITE_URL", "https://prijimanovepacienty.cz")
    mail_from = os.environ.get("MAIL_FROM") or os.environ.get("SMTP_USER") or "info@prijimanovepacienty.cz"
    to = args.to or os.environ.get("DIGEST_TO") or mail_from
    with psycopg.connect(_dsn(args)) as conn:
        d = digest.gather(conn, args.hours)
    if digest.is_empty(d) and not args.always:
        print("nothing new: no e-mail")
        return
    msg = digest.compose(d, site, mail_from, to)
    if args.dry_run:
        print(msg)
        return
    sender = alerts.sender_from_env()
    try:
        sender.send(msg)
    finally:
        sender.close()
    print(f"digest sent to {to}: {len(d['reports'])} reports")


def cmd_alerts(args) -> None:
    """Email subscribers about practices near them that started accepting."""
    import psycopg
    from . import alerts
    site = os.environ.get("SITE_URL", "https://prijimanovepacienty.cz")
    mail_from = os.environ.get("MAIL_FROM") or os.environ.get("SMTP_USER") or ""
    if not args.dry_run and not mail_from:
        sys.exit("set MAIL_FROM (or SMTP_USER)")
    with psycopg.connect(_dsn(args), autocommit=True) as conn:
        if args.test_to:
            sender = alerts.sender_from_env()
            try:
                alerts.send_test(conn, sender, site, mail_from, args.test_to)
            finally:
                sender.close()
            print(f"test alert sent to {args.test_to}")
            return
        sender = None if args.dry_run else alerts.sender_from_env()
        try:
            st = alerts.run(conn, sender, site, mail_from or "info@example.cz", dry_run=args.dry_run)
        finally:
            if sender:
                sender.close()
    print(" ".join(f"{k}={v}" for k, v in st.items()))


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

    p = sub.add_parser("sponsor", help="sponsored listings: add / list / end")
    sp = p.add_subparsers(dest="action", required=True)
    pa = sp.add_parser("add")
    pa.add_argument("provider_id", type=int)
    pa.add_argument("--specialty", required=True)
    pa.add_argument("--city", help="city slug (default: the provider's own city)")
    pa.add_argument("--tagline")
    pa.add_argument("--start", help="YYYY-MM-DD (default today)")
    pa.add_argument("--end", required=True, help="YYYY-MM-DD, last day shown")
    sp.add_parser("list")
    pe = sp.add_parser("end")
    pe.add_argument("id", type=int)
    p.set_defaults(func=cmd_sponsor)

    p = sub.add_parser("crawl", help="check clinic websites for new-patient notices")
    p.add_argument("--city", default=".", help=r"regex on city slug (default: everywhere; '^praha(-[0-9]+)?$' = Prague)")
    p.add_argument("--specialty", action="append", help="limit to a specialty (repeatable)")
    p.add_argument("--limit", type=int, help="max websites to check")
    p.add_argument("--workers", type=int, default=16)
    p.add_argument("--dry-run", action="store_true", help="print findings, write nothing")
    p.add_argument("--verbose", action="store_true", help="also print fetch errors")
    p.set_defaults(func=cmd_crawl)

    p = sub.add_parser("discover-web", help="find practice websites the register doesn't list")
    p.add_argument("--limit", type=int, help="max addresses to try")
    p.add_argument("--workers", type=int, default=16)
    p.add_argument("--dry-run", action="store_true", help="print findings, write nothing")
    p.add_argument("--verbose", action="store_true", help="also print misses")
    p.set_defaults(func=cmd_discover_web)

    p = sub.add_parser("digest", help="e-mail the site owner today's visitor reports (run daily)")
    p.add_argument("--to", help="recipient (default: $DIGEST_TO, else $MAIL_FROM)")
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--always", action="store_true", help="send even when nothing happened")
    p.add_argument("--dry-run", action="store_true", help="print the e-mail instead of sending it")
    p.set_defaults(func=cmd_digest)

    p = sub.add_parser("alerts", help="send email alerts to subscribers (run daily)")
    p.add_argument("--dry-run", action="store_true", help="print the emails instead of sending them")
    p.add_argument("--test-to", metavar="EMAIL", help="send one sample alert (real Praha 6 dentists) to EMAIL; changes no data")
    p.set_defaults(func=cmd_alerts)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
