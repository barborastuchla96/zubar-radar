import os

import psycopg
import pytest

from radar import db, digest

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


def test_digest_lists_todays_reports_but_no_subscriber_addresses():
    with psycopg.connect(DSN) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
        db.init_schema(conn)
        pid = conn.execute("INSERT INTO providers (nrpzs_place_id, name, city) VALUES ('1', 'MUDr. Jana Nováková', 'Litoměřice')"
                           " RETURNING id").fetchone()[0]
        assert digest.is_empty(digest.gather(conn))
        conn.execute("INSERT INTO availability_signals (provider_id, source, status, scope, observed_at, note, reporter_hash)"
                     " VALUES (%s, 'user', 'accepting', 'children', now(), 'jen děti', 'h')", (pid,))
        conn.execute("INSERT INTO provider_flags (provider_id, flag, source) VALUES (%s, 'english', 'user')", (pid,))
        conn.execute("INSERT INTO availability_signals (provider_id, source, status, scope, observed_at, reporter_hash, created_at)"
                     " VALUES (%s, 'user', 'not_accepting', 'all', now(), 'old', now() - interval '3 days')", (pid,))
        conn.execute("INSERT INTO subscriptions (email, specialty_slug, lat, lng, verify_token, verified_at)"
                     " VALUES ('secret@example.cz', 'zubar', 50, 14, 't', now())")
        d = digest.gather(conn)
        text = digest.compose(d, "https://x.cz/", "info@x.cz", "me@x.cz").get_content()
        assert "dnes 1 hlášení" in digest.compose(d, "https://x.cz/", "info@x.cz", "me@x.cz")["Subject"]
        assert "✓ přijímá (jen děti)  MUDr. Jana Nováková, Litoměřice" in text
        assert "„jen děti“" in text and f"https://x.cz/lekar/{pid}" in text
        assert "mluví anglicky" in text
        assert "nepřijímá" not in text                       # the 3-day-old report is not today's
        assert "secret@example.cz" not in text and "1 potvrzených" in text
