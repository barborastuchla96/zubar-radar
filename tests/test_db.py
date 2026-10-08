"""Integration tests. Need an empty throwaway database in $TEST_DATABASE_URL."""

import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from radar import db, nrpzs  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn():
    with psycopg.connect(DSN) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        c.commit()
        db.init_schema(c)
        yield c


def providers(name="old_format.csv"):
    reader, fh = nrpzs.open_csv(FIX / name)
    with fh:
        return list(nrpzs.iter_providers(reader, nrpzs.resolve_columns(reader.fieldnames)))


def pid(conn, place):
    return conn.execute("SELECT id FROM providers WHERE nrpzs_place_id = %s", (place,)).fetchone()[0]


def test_import_is_idempotent(conn):
    st = db.import_providers(conn, providers())
    assert (st.upserted, st.new, st.deactivated) == (4, 4, 0)
    st = db.import_providers(conn, providers())
    assert (st.upserted, st.new, st.deactivated) == (4, 0, 0)
    specs = conn.execute(
        "SELECT array_agg(specialty_slug ORDER BY specialty_slug) FROM provider_specialties"
        " WHERE provider_id = %s", (pid(conn, "1001"),)
    ).fetchone()[0]
    assert specs == ["hygienistka", "zubar"]


def test_missing_providers_deactivated_with_truncation_guard(conn):
    provs = providers()
    db.import_providers(conn, provs)
    st = db.import_providers(conn, provs[:1])  # 1 of 4: looks truncated
    assert st.skipped_deactivation and st.deactivated == 0
    db.MIN_IMPORT_RATIO, old = 0.5, db.MIN_IMPORT_RATIO
    try:
        st = db.import_providers(conn, [p for p in provs if p.place_id != "1002"])
    finally:
        db.MIN_IMPORT_RATIO = old
    assert st.deactivated == 1
    assert conn.execute("SELECT active FROM providers WHERE nrpzs_place_id='1002'").fetchone()[0] is False


def add(conn, provider_id, source, status, days_ago):
    conn.execute(
        "INSERT INTO availability_signals (provider_id, source, status, observed_at)"
        " VALUES (%s, %s, %s, now() - make_interval(days => %s))",
        (provider_id, source, status, days_ago),
    )


def status(conn, provider_id):
    row = conn.execute("SELECT status FROM provider_status WHERE provider_id=%s", (provider_id,)).fetchone()
    return row[0] if row else "unknown"


def test_status_scoring(conn):
    db.import_providers(conn, providers())
    a, b, c, d = (pid(conn, x) for x in ("1001", "1002", "1004", "1005"))

    add(conn, a, "user", "accepting", 1)
    assert status(conn, a) == "accepting"

    # A fresh clinic update outweighs two older user reports.
    add(conn, b, "user", "accepting", 20)
    add(conn, b, "user", "accepting", 25)
    add(conn, b, "clinic", "not_accepting", 0)
    assert status(conn, b) == "not_accepting"

    # One old crawl hit has decayed below the confidence threshold.
    add(conn, c, "web_crawl", "accepting", 45)
    assert status(conn, c) == "unknown"

    # Older than 90 days is ignored entirely.
    add(conn, d, "clinic", "accepting", 120)
    assert status(conn, d) == "unknown"


def test_near_ranks_accepting_first(conn):
    db.import_providers(conn, providers())
    praktik, zubar = pid(conn, "1002"), pid(conn, "1001")
    conn.execute("INSERT INTO provider_specialties VALUES (%s, 'zubar')", (praktik,))
    add(conn, praktik, "clinic", "accepting", 0)

    rows = db.near(conn, "zubar", 49.1951, 16.6068, km=5)
    assert [r["id"] for r in rows] == [praktik, zubar]  # accepting beats closer-but-unknown
    assert rows[1]["km"] == 0
    assert db.near(conn, "zubar", 50.08, 14.42, km=5) == []  # Prague: only the no-GPS one


def test_sponsor_cli(conn, capsys, monkeypatch):
    from radar.__main__ import main
    db.import_providers(conn, providers())
    p = pid(conn, "1001")
    monkeypatch.setenv("DATABASE_URL", DSN)
    main(["sponsor", "add", str(p), "--specialty", "zubar", "--tagline", "Volné termíny", "--end", "2099-01-01"])
    assert "/zubar/brno until 2099-01-01" in capsys.readouterr().out
    main(["sponsor", "list"])
    assert "zubar/brno" in capsys.readouterr().out
    sid = conn.execute("SELECT id FROM sponsored_listings").fetchone()[0]
    main(["sponsor", "end", str(sid)])
    main(["sponsor", "list"])
    assert capsys.readouterr().out.strip() == "ended"
    with pytest.raises(SystemExit, match="no provider"):
        main(["sponsor", "add", "999999", "--specialty", "zubar", "--end", "2099-01-01"])


def test_crawl_targets_and_record(conn):
    from radar.crawler import SiteResult, Verdict
    db.import_providers(conn, providers())
    a, b = pid(conn, "1001"), pid(conn, "1002")
    conn.execute("UPDATE providers SET web = 'www.zubar-test.cz' WHERE id = %s", (a,))
    conn.execute("UPDATE providers SET web = 'http://WWW.zubar-test.cz' WHERE id = %s", (b,))
    sites = db.crawl_targets(conn, "^brno$")
    assert sites == {"http://www.zubar-test.cz/": [a, b]}
    assert db.crawl_targets(conn, "^brno$", ["pediatr"]) == {}

    res = [SiteResult("http://www.zubar-test.cz/", Verdict("accepting", "all", "přijímáme nové pacienty"),
                      "http://www.zubar-test.cz/", provider_ids=[a, b])]
    assert db.record_crawl(conn, res) == 2
    assert db.record_crawl(conn, res) == 0          # same verdict within a week: no duplicates
    res[0].verdict = Verdict("not_accepting", "all", "nepřijímáme")
    assert db.record_crawl(conn, res) == 2          # a changed verdict is recorded
    note = conn.execute("SELECT note FROM availability_signals WHERE provider_id=%s ORDER BY id LIMIT 1", (a,)).fetchone()[0]
    assert note.startswith("„přijímáme nové pacienty“ — http://www.zubar-test.cz/")
