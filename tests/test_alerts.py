import os
import smtplib

import pytest

from radar import alerts

SITE = "https://prijimanovepacienty.cz"


def _alert(n=1):
    provs = [{"id": 10 + i, "name": f"Zubní ordinace {i}", "street": "Bělohorská", "house_no": "12",
              "city": "Praha 6", "phone": "+420 737351057", "km": 1.24} for i in range(n)]
    return alerts.Alert("sub-1", "pacient@example.cz", "tok_" + "x" * 28, "zubar", "Praha 6", 5, provs)


@pytest.mark.parametrize("n,subject_end,verb", [
    (1, "1 ordinace přijímá nové pacienty", "přijímá nové pacienty tato ordinace"),
    (3, "3 ordinace přijímají nové pacienty", "přijímají nové pacienty tyto ordinace"),
    (5, "5 ordinací přijímá nové pacienty", "přijímají nové pacienty tyto ordinace"),
])
def test_compose_czech_grammar(n, subject_end, verb):
    m = alerts.compose(_alert(n), SITE, "info@prijimanovepacienty.cz")
    assert m["Subject"] == f"Zubaři – Praha 6 a okolí: {subject_end}"
    body = m.get_content()
    assert verb in body
    assert "Bělohorská 12, Praha 6 (1,2 km)" in body and "Telefon: 737 351 057" in body
    assert f"{SITE}/lekar/10" in body


def test_compose_unsubscribe_header_is_plain():
    m = alerts.compose(_alert(), SITE, "info@prijimanovepacienty.cz")
    raw = m.as_string()
    want = f"List-Unsubscribe: <{SITE}/upozorneni/odhlasit?t=tok_{'x' * 28}>"
    assert want in raw
    assert want.split(": ", 1)[1].strip("<>") in m.get_content()


# ---------------------------------------------------------------------------
# DB: who gets told about what
# ---------------------------------------------------------------------------
psycopg = pytest.importorskip("psycopg")
DSN = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")


class FakeSender:
    def __init__(self, refuse=()):
        self.sent, self.refuse = [], set(refuse)

    def send(self, msg):
        if msg["To"] in self.refuse:
            raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no such user")})
        self.sent.append(msg)


@pytest.fixture
def conn():
    from radar import db
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        db.init_schema(c)
        # Two Brno dentists ~1 km apart, one Ostrava dentist far away
        for i, (lat, lng, city) in enumerate([(49.1951, 16.6068, "Brno"), (49.2040, 16.6068, "Brno"),
                                              (49.8209, 18.2625, "Ostrava")], start=1):
            c.execute("INSERT INTO providers (id, nrpzs_place_id, name, city, lat, lng)"
                      " VALUES (%s, %s, %s, %s, %s, %s)", (i, str(i), f"Ordinace {i}", city, lat, lng))
            c.execute("INSERT INTO provider_specialties VALUES (%s, 'zubar')", (i,))
        yield c


def subscribe(c, email="a@example.cz", verified="now() - interval '1 day'", radius=5):
    return c.execute(
        "INSERT INTO subscriptions (email, specialty_slug, lat, lng, radius_km, verify_token, place_label, verified_at)"
        f" VALUES (%s, 'zubar', 49.1951, 16.6068, %s, md5(random()::text), 'Brno', {verified}) RETURNING id",
        (email, radius)).fetchone()[0]


def accepting(c, provider_id, when="now()"):
    c.execute("INSERT INTO availability_signals (provider_id, source, status, observed_at, created_at)"
              f" VALUES (%s, 'clinic', 'accepting', {when}, {when})", (provider_id,))


@needs_db
def test_alert_only_for_news_within_radius_and_only_once(conn):
    subscribe(conn)
    accepting(conn, 1, "now() - interval '3 days'")   # accepting before sign-up: not news
    accepting(conn, 2)                                # new and 1 km away: news
    accepting(conn, 3)                                # new but in Ostrava
    s = FakeSender()
    st = alerts.run(conn, s, "https://x.cz", "info@x.cz")
    assert st["sent"] == 1 and len(s.sent) == 1
    assert "Ordinace 2" in s.sent[0].get_content() and "Ordinace 1" not in s.sent[0].get_content()
    assert alerts.run(conn, s, "https://x.cz", "info@x.cz")["sent"] == 0   # never twice
    assert conn.execute("SELECT count(*) FROM mail_log WHERE kind = 'alert'").fetchone()[0] == 1


@needs_db
def test_unconfirmed_get_nothing_and_are_deleted_after_a_week(conn):
    subscribe(conn, "new@example.cz", verified="NULL")
    old = subscribe(conn, "old@example.cz", verified="NULL")
    conn.execute("UPDATE subscriptions SET created_at = now() - interval '8 days' WHERE id = %s", (old,))
    accepting(conn, 2)
    s = FakeSender()
    st = alerts.run(conn, s, "https://x.cz", "info@x.cz")
    assert st["sent"] == 0 and st["removed_unconfirmed"] == 1
    assert conn.execute("SELECT email FROM subscriptions").fetchall() == [("new@example.cz",)]


@needs_db
def test_daily_limit_is_shared_and_bad_addresses_skipped(conn, monkeypatch):
    for e in ["bad@example.cz", "a@example.cz", "b@example.cz", "c@example.cz"]:
        subscribe(conn, e)
    accepting(conn, 2)
    conn.execute("INSERT INTO mail_log (kind) SELECT 'confirm' FROM generate_series(1, 8)")
    monkeypatch.setattr(alerts, "DAILY_LIMIT", 10)   # 8 used by confirmations -> 2 left
    s = FakeSender(refuse={"bad@example.cz"})
    st = alerts.run(conn, s, "https://x.cz", "info@x.cz")
    assert (st["sent"], st["failed"], st["deferred"]) == (2, 1, 1)
    assert [m["To"] for m in s.sent] == ["a@example.cz", "b@example.cz"]
