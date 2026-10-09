import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from radar import crawler
from radar.crawler import classify


@pytest.mark.parametrize("text,status,scope", [
    ("Přijímáme nové pacienty!", "accepting", "all"),
    ("Ordinace přijímá nové klienty všech pojišťoven.", "accepting", "all"),
    ("Registrujeme nové pacienty.", "accepting", "all"),
    ("Máme volnou kapacitu pro nové pacienty.", "accepting", "all"),
    ("Přijímáme nové pacienty – pouze děti do 18 let.", "accepting", "children"),
    ("Momentálně NEPŘIJÍMÁME nové pacienty.", "not_accepting", "all"),
    ("Bohužel nepřijímáme žádné další pacienty.", "not_accepting", "all"),
    ("Kapacita ordinace je naplněna.", "not_accepting", "all"),
    ("Bohužel nemáme volnou kapacitu.", "not_accepting", "all"),
    ("STOP STAV pro nové pacienty", "not_accepting", "all"),
    ("Nepřijímáme nové dospělé pacienty.", "not_accepting", "adults"),
    ("Nové pacienty zapisujeme do pořadníku.", "waitlist", "all"),
])
def test_classify(text, status, scope):
    v = classify(f"Vítejte v naší ordinaci.\n{text}\nOrdinační hodiny: Po–Pá 8–16.")
    assert (v.status, v.scope) == (status, scope)
    assert v.snippet


@pytest.mark.parametrize("text", [
    "Ordinační hodiny Po–Pá 8–16.",
    "Přijímáme pacienty všech pojišťoven.",          # no "nové"
    "Přijímáme platební karty. Nové pacienty prosíme o objednání.",  # different sentences
    "Naši pacienti jsou pro nás na prvním místě.",
    "Přijímáme nové klienty na ordinační bělení zubů.",              # a paid extra, not registration
    "Nové pacienty přijímáme pouze jako samoplátce.",
])
def test_classify_nothing(text):
    assert classify(text).status is None


def test_negative_wins_and_is_not_double_counted():
    v = classify("Nepřijímáme nové pacienty. Stávající pacienty ošetříme vždy.")
    assert v.status == "not_accepting" and not v.conflicting


def test_conflicting_page_gives_no_status():
    v = classify("Nepřijímáme nové pacienty. Od ledna opět přijímáme nové pacienty.")
    assert v.status is None and v.conflicting


def test_pick_links_prefers_patient_pages_same_site():
    links = [("/kontakt", "Kontakt"), ("/novi-pacienti", "Noví pacienti"), ("https://other.cz/pacienti", "x"),
             ("/cenik.pdf", "Ceník"), ("#top", "nahoru"), ("mailto:a@b.cz", "mail"), ("/galerie", "Galerie")]
    assert crawler.pick_links("https://zubar.cz/", links) == ["https://zubar.cz/novi-pacienti", "https://zubar.cz/kontakt"]


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://10.0.0.5/", "http://localhost:8080/", "file:///etc/passwd",
                                 "http://[::1]/", "http://169.254.169.254/latest/meta-data"])
def test_blocks_non_public_targets(url):
    with pytest.raises(crawler.BlockedURL):
        crawler.check_url(url)


def test_redirect_to_private_address_is_blocked():
    handler = crawler._SafeRedirect(allow_private=False)
    with pytest.raises(crawler.BlockedURL):
        handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1:5432/")


@pytest.mark.parametrize("web,expected", [
    ("www.zubar.cz", "http://www.zubar.cz/"),
    ("https://Zubar.CZ/praha", "https://zubar.cz/praha"),
    ("http://www.a.cz/ http://www.b.cz", "http://www.a.cz/"),
    ("", None), ("není", None),
])
def test_normalize_site(web, expected):
    assert crawler.normalize_site(web) == expected


# ---------------------------------------------------------------------------
# End-to-end against a local HTTP server
# ---------------------------------------------------------------------------
PAGES = {
    "/": ("text/html; charset=utf-8", '<html><head><title>Zubní ordinace</title><style>.x{}</style></head><body>'
          '<script>var t="Přijímáme nové pacienty"</script><h1>Vítejte</h1><nav><a href="/kontakt">Kontakt</a> '
          '<a href="/novi-pacienti">Noví pacienti</a></nav></body></html>'),
    "/novi-pacienti": ("text/html; charset=windows-1250",
                       "<p>Od září opět přijímáme nové pacienty, prosíme volejte.</p>".encode("cp1250")),
    "/kontakt": ("text/html", "<p>Tel. 123</p>"),
    "/robots.txt": ("text/plain", "User-agent: *\nDisallow: /tajne\n"),
    "/tajne": ("text/html", "<p>Nepřijímáme nové pacienty.</p>"),
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        page = PAGES.get(self.path)
        if not page:
            self.send_error(404)
            return
        ctype, body = page
        body = body if isinstance(body, bytes) else body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_check_site_follows_patient_link_and_ignores_scripts(site):
    r = crawler.check_site(site + "/", allow_private=True, delay=0)
    assert r.error is None
    assert r.verdict.status == "accepting"           # found on the subpage, decoded from cp1250
    assert r.page_url == site + "/novi-pacienti"


def test_check_site_respects_robots(site):
    r = crawler.check_site(site + "/tajne", allow_private=True, delay=0)
    assert r.error == "blocked by robots.txt" and r.verdict is None


def test_check_site_reports_http_errors(site):
    assert crawler.check_site(site + "/nope", allow_private=True, delay=0).error == "HTTP 404"


def test_private_server_blocked_without_override(site):
    r = crawler.check_site(site + "/", delay=0)
    assert r.error and "non-public" in r.error


def test_crawl_groups_and_maps_providers(site):
    results = crawler.crawl({site + "/": [1, 2], site + "/kontakt": [3]}, workers=2, allow_private=True, delay=0)
    by_url = {r.url: r for r in results}
    assert by_url[site + "/"].provider_ids == [1, 2] and by_url[site + "/"].verdict.status == "accepting"
    assert by_url[site + "/kontakt"].verdict.status is None


def test_snippet_keeps_original_diacritics():
    v = classify("Vítejte.\nOd září opět PŘIJÍMÁME nové pacienty, prosíme volejte.\nTel. 123")
    assert v.status == "accepting"
    assert "Od září opět PŘIJÍMÁME nové pacienty" in v.snippet


@pytest.mark.parametrize("web", ["ondrej.vacha@seznam.cz", "http://facebook.com/ordinace", "https://www.firmy.cz/detail/1.html",
                                 "www.google.com/maps/place/x", "mailto:x@y.cz"])
def test_not_the_practices_own_site(web):
    assert crawler.normalize_site(web) is None


@pytest.mark.parametrize("base", ["http://www.gynekolog.cz/novak/", "http://www.gynekolog.cz/novak", "http://www.gynekolog.cz/novak/index.html"])
def test_portal_pages_stay_with_their_doctor(base):
    links = [("/novak/kontakt", "Kontakt"), ("/svoboda/novi-pacienti", "Noví pacienti"), ("/kontakt", "Kontakt")]
    assert crawler.pick_links(base, links) == ["http://www.gynekolog.cz/novak/kontakt"]


def test_variants_try_the_usual_fixes():
    assert crawler._variants("http://zubar.cz/o-nas") == [
        "http://zubar.cz/o-nas", "https://zubar.cz/o-nas", "http://www.zubar.cz/o-nas", "https://www.zubar.cz/o-nas"]


def test_falls_back_to_http_when_https_is_broken(site):
    https = site.replace("http://", "https://")          # the test server speaks plain http only
    r = crawler.check_site(https + "/", allow_private=True, delay=0)
    assert r.error is None and r.verdict.status == "accepting"


@pytest.mark.parametrize("err,kind", [("HTTP 404", "HTTP 404"), ("URLError: <urlopen error [Errno -2] Name or service not known>", "DNS: domain not found"),
                                      ("URLError: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED]>", "TLS/certificate"), ("TimeoutError: timed out", "timeout"),
                                      ("BlockedURL: DNS failed for www.example.cz", "DNS: domain not found")])
def test_error_kinds(err, kind):
    assert crawler.error_kind(err) == kind


@pytest.mark.parametrize("snippet,specs,ok", [
    ("Přijímáme nové pacienty do Diabetologické ambulance.", {"neurolog"}, False),
    ("Naše ordinace praktické lékařky pro děti přijímá nové pacienty.", {"ocni"}, False),
    ("Naše ordinace praktické lékařky pro děti přijímá nové pacienty.", {"pediatr"}, True),
    ("Oční ambulance přijímá nové pacienty.", {"ocni"}, True),
    ("Přijímáme nové pacienty do neurologické a diabetologické ambulance.", {"neurolog"}, True),
    ("Aktuálně přijímáme nové pacienty.", {"psychiatr"}, True),
    ("Psychologická poradna přijímá nové klienty.", {"psychiatr"}, False),
    ("ORL ambulance nepřijímá nové pacienty.", {"orl"}, True),
])
def test_fits_specialty(snippet, specs, ok):
    assert crawler.fits_specialty(snippet, specs) is ok


def test_directory_sites_are_not_own_sites():
    assert crawler.normalize_site("https://www.zdravotniregistr.cz/lekar/123") is None


@pytest.mark.parametrize("text,yes", [
    ("Nemáme smlouvy se zdravotními pojišťovnami, veškerá péče je hrazena přímo.", True),
    ("Nemáme uzavřenou smlouvu s žádnou zdravotní pojišťovnou.", True),
    ("Jsme nesmluvní ordinace.", True),
    ("Ošetřujeme pouze samoplátce.", True),
    ("Přijímáme i samoplátce.", False),
    ("Nemáme smlouvu s pojišťovnou 211.", False),
    ("Nemáme smlouvu s pojišťovnami kromě VZP.", False),
    ("Nemáme smlouvy se všemi pojišťovnami.", False),
    ("Nemáme smlouvu se Zdravotní pojišťovnou ministerstva vnitra.", False),
    ("Dentální hygiena je pouze pro samoplátce.", False),
    ("Bělení zubů: tato péče není hrazena ze zdravotního pojištění.", False),
    ("Jsme nesmluvní lékař VZP.", False),
])
def test_self_pay(text, yes):
    assert bool(crawler.self_pay(text)) is yes


@pytest.mark.parametrize("text,yes", [
    ("V ordinaci mluvíme anglicky a německy.", True),
    ("Domluvíte se u nás i anglicky.", True),
    ("Anglicky mluvící praktický lékař v Praze 6.", True),
    ("We speak English.", True),
    ("English-speaking GP in Prague.", True),
    ("Nemluvíme anglicky.", False),
    ("Kurzy angličtiny pro děti.", False),
])
def test_english(text, yes):
    assert bool(crawler.english(text)) is yes


def test_find_flags_collects_both():
    assert set(crawler.find_flags("Nemáme smlouvy se zdravotními pojišťovnami. We speak English.")) == {"self_pay", "english"}
