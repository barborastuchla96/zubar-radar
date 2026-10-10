import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
import pytest

from radar import db, discover

DSN = os.environ.get("TEST_DATABASE_URL")

PAGES = {
    "/novakova/": "<h1>Ordinace praktického lékaře</h1><p>MUDr. Jana Nováková, ordinační hodiny Po–Pá</p>",
    "/shop/": "<h1>Novák a syn</h1><p>Prodej nábytku, Nováková kuchyně</p>",             # names her, not care
    "/brand/": "<h1>Vítejte</h1><p>Zubní péče pro celou rodinu.</p><a href='/brand/kontakt'>Kontakt</a>",
    "/brand/kontakt": "<p>Amodent s.r.o., IČO 12345678, objednání pacientů</p>",
    "/other/": "<h1>Ordinace</h1><p>MUDr. Petr Svoboda přijímá pacienty.</p>",
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_own_domain():
    assert discover.own_domain("ordinace@zubarnovak.cz") == "zubarnovak.cz"
    assert discover.own_domain("MUDr.Novak@Seznam.cz") is None
    assert discover.own_domain("x@gmail.com") is None
    assert discover.own_domain("x@o2active.cz") is None
    assert discover.own_domain("") is None


def test_name_tokens_keep_surname_or_brand():
    assert discover.name_tokens("MUDr. Anna Trnečková s.r.o.") == {"trneckova"}
    assert discover.name_tokens("Zubní ordinace MUDr. Petr Novák a MUDr. Eva Malá") == {"novak"}
    assert discover.name_tokens("ROSEADENT s.r.o.") == {"roseadent"}
    assert discover.name_tokens("Zubní ordinace Praha s.r.o.", "Praha") == set()


def test_looks_like_practice():
    ok = discover.looks_like_practice
    assert ok("MUDr. Jana Nováková, ordinační hodiny", "MUDr. Jana Nováková", None, None)
    assert ok("Ordinace MUDr. Janu Novákovou najdete…", "MUDr. Jana Nováková", None, None)   # declined
    assert not ok("Nováková kuchyně, prodej nábytku", "MUDr. Jana Nováková", None, None)     # not care
    assert not ok("MUDr. Petr Svoboda, ordinace", "MUDr. Jana Nováková", None, None)        # someone else
    assert ok("Ordinace, IČO 12345678", "Zubní ordinace s.r.o.", "12345678", None)
    assert ok("Zubní péče pro celou rodinu", "Mini Care s.r.o.", None, None, host="minicare.cz")


def test_check_candidate(site):
    c = lambda path, name, ico=None: discover.Candidate(f"{site}{path}", "email", [(1, name, ico, "Praha")])
    r = discover.check_candidate(c("/novakova/", "MUDr. Jana Nováková"), allow_private=True, delay=0)
    assert r.matched == [1] and r.url == f"{site}/"
    assert discover.check_candidate(c("/shop/", "MUDr. Jana Nováková"), allow_private=True, delay=0).matched == []
    assert discover.check_candidate(c("/other/", "MUDr. Jana Nováková"), allow_private=True, delay=0).matched == []
    # named only on the contact page
    r = discover.check_candidate(c("/brand/", "Amodent s.r.o.", "12345678"), allow_private=True, delay=0)
    assert r.matched == [1]
    assert discover.check_candidate(c("/missing/", "X"), allow_private=True, delay=0).error


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not set")
def test_candidates_and_record():
    with psycopg.connect(DSN) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
        db.init_schema(conn)
        ids = {}
        rows = [("1", "MUDr. Jana Nováková", "ordinace@novakova.cz", None),
                ("2", "MUDr. Petr Kos", "kos@seznam.cz", None),                 # mailbox provider: skip
                ("3", "MUDr. Eva Malá", "mala@mala.cz", "http://mala.cz"),     # register has a site: skip
                ("4", "Amodent s.r.o.", None, None)]
        for n in range(5, 10):                                                # many on one domain: a chain
            rows.append((str(n), f"MUDr. Lékař {n}", f"l{n}@nemocnice-x.cz", None))
        for place, name, email, web in rows:
            ids[place] = conn.execute(
                "INSERT INTO providers (nrpzs_place_id, name, city, email, web) VALUES (%s, %s, 'Praha', %s, %s) RETURNING id",
                (place, name, email, web)).fetchone()[0]
        conn.execute("UPDATE providers SET web_suggested = 'www.amodent.cz' WHERE id = %s", (ids["4"],))
        cands = discover.candidates(conn)
        assert [(c.url, c.source) for c in cands] == [("http://www.amodent.cz/", "user"), ("http://novakova.cz/", "email")]

        found = discover.Found(cands[1], url="https://novakova.cz/", matched=[ids["1"]])
        missed = discover.Found(cands[0], error="HTTP 404")
        assert discover.record(conn, [found, missed]) == 1
        web = lambda p: conn.execute("SELECT web_found, web_found_source, web_suggested FROM providers WHERE id = %s",
                                     (ids[p],)).fetchone()
        assert web("1") == ("https://novakova.cz/", "email", None)
        assert web("4") == (None, None, None)              # a suggestion that didn't hold up is dropped
        assert discover.candidates(conn) == []              # both looked at recently: not retried

        # The crawler now checks the found site
        conn.execute("INSERT INTO provider_specialties VALUES (%s, 'praktik')", (ids["1"],))
        conn.execute("UPDATE providers SET city_slug = 'praha'")
        assert db.crawl_targets(conn, ".") == {"https://novakova.cz/": [ids["1"]]}
