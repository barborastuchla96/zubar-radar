"""Clinic website checker.

Fetches the website listed in the register for each practice, looks for Czech
phrases like "přijímáme nové pacienty" / "nepřijímáme nové pacienty", and turns
what it finds into low-weight `web_crawl` availability signals.

Polite by design: identifies itself, honours robots.txt, one request at a time
per host with a pause, small page budget per site. Refuses private/loopback
addresses (also on redirects) so a planted URL can't make the server probe
its own network.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser

USER_AGENT = "PrijimaNovePacientyBot/1.0 (+https://prijimanovepacienty.cz/o-projektu)"
TIMEOUT = 10
MAX_BYTES = 1_500_000
MAX_EXTRA_PAGES = 2
HOST_DELAY = 1.0  # seconds between requests to the same host

# --------------------------------------------------------------------------
# Phrase detection. Text is lower-cased and stripped of diacritics first, so
# patterns are plain ASCII ("prijimame" matches "přijímáme").
# --------------------------------------------------------------------------
_GAP = r"[^.!?\n]{0,40}?"  # stay within one sentence
_VERB = r"(?:prijimame|prijima|bereme|bere|registrujeme|registruje|nabirame|nabira)"
_WHO = r"(?:pacient\w*|klient\w*)"

NEGATIVE = [
    re.compile(rf"\bne{_VERB}\b{_GAP}\b(?:nov\w*|dalsi\w*|zadn\w*)\b{_GAP}\b{_WHO}"),
    re.compile(rf"\bkapacit\w*\b{_GAP}\b(?:naplnen\w*|plna|vycerpan\w*|zaplnen\w*)"),
    re.compile(rf"\bnemame\b{_GAP}\bkapacit\w*"),     # "nemáme (volnou) kapacitu"
    re.compile(r"\bstop\s?stav\w*"),
    # "Pozastavujeme / ukončili jsme příjem nových pacientů", "kapacity nedostačují"
    re.compile(rf"\b(?:pozastav\w*|zastav\w*|ukoncil\w*|ukoncujeme|uzavrel\w*|uzavirame|preruseni|prerusujeme|pozastaveni|zastaveni|ukonceni)\b{_GAP}\b(?:prijem\w*|prijimani|registrac\w*|nabor\w*)\b{_GAP}\bnov\w*\b{_GAP}\b{_WHO}"),
    re.compile(rf"\b(?:prijem|prijimani|registrac\w*)\b{_GAP}\bnov\w*\b{_GAP}\b{_WHO}\b{_GAP}\b(?:pozastav\w*|zastav\w*|ukoncen\w*|uzavren\w*|preruse\w*)"),
    re.compile(rf"\bkapacit\w*\b{_GAP}\b(?:nedostacuj\w*|nestaci|nepostacuj\w*)"),
]
WAITLIST = [
    re.compile(rf"\b(?:zapisujeme|zapiseme|zapis)\b{_GAP}\b(?:poradnik\w*|cekaci\w*)"),
    re.compile(rf"\bnov\w* {_WHO}{_GAP}\b(?:poradnik\w*|cekaci (?:listin|seznam)\w*)"),
]
POSITIVE = [
    re.compile(rf"\b{_VERB}\b{_GAP}\bnov\w*\b{_GAP}\b{_WHO}"),
    re.compile(rf"\bvoln\w*\b{_GAP}\bkapacit\w*{_GAP}\b(?:nov\w*|{_WHO})"),
    re.compile(rf"\bnovi {_WHO} (?:jsou )?vitani\b"),
]

# "Přijímáme nové klienty na bělení zubů": that's about a paid extra, not registering as a patient.
NOT_REGISTRATION = re.compile(r"\b(?:belen\w*|beleni|estetick\w*|kosmetick\w*|samoplat\w*)")


# "Nemáme smlouvy se zdravotními pojišťovnami", "pouze pro samoplátce": care isn't covered by insurance.
# Only "no insurer at all": not when one is named ("…s pojišťovnou 211", "…kromě VZP").
_NOT_ALL = r"(?!\s*(?:\d|vzp|cpzp|ozp|rbp|vozp|zpmv|zps|ministerstva|skoda|vojensk|oborov|ceska|ceske|vseobecn|krome|mimo|s vyjimkou))"
_INSURERS = r"(?:zadnou\b{g}pojistovn\w*|(?:zdravotnimi )?pojistovnami|(?:zdravotni )?pojistovnou)" + _NOT_ALL
SELF_PAY = [
    re.compile(rf"\bnemame\b{_GAP}\bsmlouv\w*\b{_GAP}" + _INSURERS.format(g=_GAP)),
    re.compile(rf"\bbez smlouv\w* (?:se?|s) " + _INSURERS.format(g=_GAP)),
    re.compile(r"\bnesmluvn\w* (?:ordinac\w*|lekar\w*|zarizen\w*|pracovist\w*|poskytovatel\w*|ambulanc\w*)\b(?![^.!?\n]{0,30}(?:vzp|cpzp|ozp|rbp|vozp|zpmv|\b2\d\d\b))"),
    re.compile(r"\b(?:pouze|jen|vyhradne|vylucne) (?:pro |pro nase )?samoplatc\w*"),
]
# …unless the sentence is about one paid extra (whitening, hygiene, implants) or "not with all of them".
SELF_PAY_EXTRA = re.compile(r"\b(?:vsemi|vsech|nekter\w*|hygien\w*|belen\w*|estetick\w*|kosmetick\w*|implant\w*|botox\w*|laser\w*|nadstandard\w*|ortodont\w*|rovnatk\w*)")


# "Mluvíme anglicky", "domluvíte se i anglicky", "we speak English": someone there speaks English.
ENGLISH = [
    re.compile(rf"\b(?:mluvime|hovorime|domluvite se|domluvime se|komunikujeme|dorozumite se|mluvi|hovori|ovladame|ovlada)\b{_GAP}\banglick\w*"),
    re.compile(r"\banglick\w* (?:mluvic\w*|hovoric\w*)"),
    re.compile(r"\b(?:we (?:also )?speak|english[- ]speaking|speaks? english|english (?:is )?spoken)\b"),
    re.compile(r"\b(?:communicate|consultations?|treatment|care is provided) (?:also |fully )?in english\b"),
]


def english(raw_text: str) -> str | None:
    """Snippet saying someone at the practice speaks English, or None."""
    text, src = _normalize_with_map(raw_text)
    for p in ENGLISH:
        if m := p.search(text):
            return _snippet(text, m, original=raw_text, src=src)
    return None


# Czech health insurers: code → how practices write the name (normalized text).
INSURERS = {
    "111": r"\bvzp\b|vseobecn\w* zdravotni\w* pojistovn\w*",
    "201": r"\bvozp\b|vojensk\w* zdravotni\w* pojistovn\w*",
    "205": r"\bcpzp\b|ceska prumyslova",
    "207": r"\bozp\b|oborov\w* zdravotni\w* pojistovn\w*",
    "209": r"\bzps\b|zamestnaneck\w* pojistovn\w* skoda|pojistovn\w* skoda",
    "211": r"\bzp ?mv\b|\bzpmv\b|ministerstva vnitra",
    "213": r"\brbp\b|revirni bratrsk\w*",
}
_CODES = re.compile(r"\b(111|201|205|207|209|211|213)\b")
_ALL_INSURERS = re.compile(r"\b(?:vsemi|vsech(?:ny)?) (?:zdravotnimi |zdravotnich |zdravotni )?pojistovn\w*")
_NO_CONTRACT = re.compile(r"\b(?:nemame|nema|bez) smlouv\w*|\bnesmluvn\w*|\bkrome\b|\bmimo\b")


def insurers(raw_text: str) -> tuple[set[str], str | None]:
    """Insurers a page says the practice has contracts with ("Smluvní pojišťovny: 111, 201, 207",
    "spolupracujeme se všemi zdravotními pojišťovnami"), and the snippet that says so."""
    text, src = _normalize_with_map(raw_text)
    found: set[str] = set()
    first = None
    for m in re.finditer(r"pojistov\w*|smluvn\w*", text):
        a, b = max(0, m.start() - 120), min(len(text), m.end() + 160)
        window = text[a:b]
        if _NO_CONTRACT.search(window):
            continue                       # "nemáme smlouvu s VZP": the opposite
        hits = {code for code, name in INSURERS.items() if re.search(name, window)}
        codes = set(_CODES.findall(window))
        if len(codes) >= 2 or (codes and hits):      # a lone "201" is more likely a price or a house number
            hits |= codes
        if _ALL_INSURERS.search(window):
            hits |= set(INSURERS)
        if hits:
            found |= hits
            first = first or _snippet(text, m, pad=90, original=raw_text, src=src)
    return found, first


# A link to the site's English version: hreflang="en", "EN" / "English" / "Anglicky" / 🇬🇧 as the
# label, an /en/ path or ?lang=en. That's only a hint: an English page doesn't mean the staff speak English.
HREFLANG_EN = "\x00hreflang=en"     # added to a link's label by extract() when the tag says hreflang="en"
EN_LABELS = {"en", "eng", "english", "in english", "english version", "anglicky", "anglictina", "anglicka verze"}
EN_FLAGS = ("\U0001F1EC\U0001F1E7", "\U0001F1FA\U0001F1F8")    # 🇬🇧 🇺🇸
_EN_PATH = re.compile(r"(?:^|/)(?:en|eng|english|en[-_](?:gb|us))(?:/|\.html?$|\.php$|$)", re.I)
_EN_QUERY = re.compile(r"(?:^|&)(?:lang|language|lng|hl|locale)=en(?:[-_]\w+)?(?:&|$)", re.I)


def is_english_link(url: str, label: str = "") -> bool:
    """Does this link (absolute URL and its label) point to an English version of the site?"""
    if HREFLANG_EN in label or any(f in label for f in EN_FLAGS):
        return True
    u = urllib.parse.urlsplit(url)
    if _EN_PATH.search(u.path) or _EN_QUERY.search(u.query):
        return True
    return re.sub(r"[^a-z ]", "", normalize(label)).strip() in EN_LABELS


def _same_site(host: str | None, base_host: str | None) -> bool:
    strip = lambda h: (h or "").lower().removeprefix("www.")
    return bool(host) and strip(host) in (strip(base_host), "en." + strip(base_host))


def _folder(path: str) -> str:
    """/novak/ and /novak are a folder; /novak/index.html is a page inside /novak/"""
    last = path.rsplit("/", 1)[-1]
    return path if path.endswith("/") else (path.rsplit("/", 1)[0] + "/" if "." in last else path + "/")


def english_version(base_url: str, links: list[tuple[str, str]]) -> str | None:
    """URL of the site's English version (on the same site), or None. On a doctor's page on a
    shared portal (gynekolog.cz/novak/), only inside that page: the portal's own "EN" isn't theirs."""
    base = urllib.parse.urlsplit(base_url)
    scope = _folder(base.path)
    for href, label in links:
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urllib.parse.urljoin(base_url, href).split("#")[0]
        u = urllib.parse.urlsplit(url)
        if scope != "/" and not (u.hostname == base.hostname and u.path.startswith(scope)):
            continue
        if u.scheme in ("http", "https") and _same_site(u.hostname, base.hostname) and is_english_link(url, label):
            return url
    return None


def find_flags(raw_text: str) -> dict[str, str]:
    """Facts beyond "accepting?" that a page states, as {flag: snippet}."""
    found = {"self_pay": self_pay(raw_text), "english": english(raw_text)}
    if not found["self_pay"]:
        codes, snippet = insurers(raw_text)
        found.update({f"ins{c}": snippet for c in codes})
    return {k: v for k, v in found.items() if v}


def self_pay(raw_text: str) -> str | None:
    """Snippet saying the practice has no contract with health insurers, or None."""
    text, src = _normalize_with_map(raw_text)
    for p in SELF_PAY:
        for m in p.finditer(text):
            if not SELF_PAY_EXTRA.search(_sentence(text, m)):
                return _snippet(text, m, original=raw_text, src=src)
    return None


def _stale(sentence: str) -> bool:
    """A sentence dated only with years before last year ("Od ledna 2019 přijímáme nové pacienty")
    is an old news item, not today's state."""
    years = [int(y) for y in re.findall(r"\b(20[0-4]\d)\b", sentence)]
    return bool(years) and max(years) < date.today().year - 1


def _sentence(text: str, m: re.Match) -> str:
    start = max(text.rfind(c, 0, m.start()) for c in ".!?\n") + 1
    ends = [i for i in (text.find(c, m.end()) for c in ".!?\n") if i != -1]
    return text[start:min(ends) if ends else len(text)]


def normalize(text: str) -> str:
    return _normalize_with_map(text)[0]


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Lower-case ASCII version of text, plus, for every output character, the
    index of the original character it came from (so snippets can be quoted
    from the original text with diacritics)."""
    out: list[str] = []
    src: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        a = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode().lower()
        for c in a:
            if c in " \t\r\f\v":
                if prev_space:
                    continue
                c, prev_space = " ", True
            else:
                prev_space = False
            out.append(c)
            src.append(i)
    return "".join(out), src


@dataclass
class Verdict:
    status: str | None          # accepting / not_accepting / waitlist / None (nothing or conflicting)
    scope: str = "all"          # all / children / adults
    snippet: str = ""
    conflicting: bool = False


def _snippet(text: str, m: re.Match, pad: int = 50, original: str | None = None,
             src: list[int] | None = None) -> str:
    """Text around a match, taken from the original (with diacritics) when given."""
    a, b = max(0, m.start() - pad), min(len(text), m.end() + pad)
    # Whole words only: "…tní stomatologickou péči" reads like a typo.
    while 0 < a < m.start() and text[a - 1].isalnum():
        a += 1
    while m.end() < b < len(text) and text[b].isalnum():
        b -= 1
    if original is not None and src:
        s = original[src[a]: src[b - 1] + 1]
    else:
        s = text[a:b]
    s = re.sub(r"\s+", " ", s).strip()
    return ("…" if a > 0 else "") + s + ("…" if b < len(text) else "")


def _scope(window: str) -> str:
    kids = re.search(r"\b(?:det\w*|dorost\w*|pediatr\w*)", window)
    adults = re.search(r"\bdospel\w*", window)
    if kids and not adults:
        return "children"
    if adults and not kids:
        return "adults"
    return "all"


def classify(raw_text: str) -> Verdict:
    """Decide what a page says about taking new patients."""
    text, src = _normalize_with_map(raw_text)
    neg = next((m for p in NEGATIVE for m in p.finditer(text)), None)
    wait = next((m for p in WAITLIST for m in p.finditer(text) if not _stale(_sentence(text, m))), None)
    # Blank out negative matches so "nepřijímáme" text can't also count as positive.
    masked = text
    for p in NEGATIVE:
        masked = p.sub(lambda m: " " * len(m.group(0)), masked)
    pos = next((m for p in POSITIVE for m in p.finditer(masked)
                if not NOT_REGISTRATION.search(_sentence(masked, m)) and not _stale(_sentence(masked, m))), None)

    found = [(s, m) for s, m in (("not_accepting", neg), ("waitlist", wait), ("accepting", pos)) if m]
    if not found:
        return Verdict(None)
    if neg and pos:
        return Verdict(None, snippet=_snippet(text, neg, original=raw_text, src=src), conflicting=True)
    status, m = found[0]
    return Verdict(status, _scope(_snippet(text, m, 80)), _snippet(text, m, original=raw_text, src=src))


# --------------------------------------------------------------------------
# HTML → text + links
# --------------------------------------------------------------------------
class _Extractor(HTMLParser):
    BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "header", "footer"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._skip = 0
        self._href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        elif tag == "a":
            a = dict(attrs)
            self._href = a.get("href")
            self._link_text = [HREFLANG_EN] if (a.get("hreflang") or "").lower().startswith("en") else []
        elif tag == "link":
            a = dict(attrs)
            if (a.get("hreflang") or "").lower().startswith("en") and a.get("href"):
                self.links.append((a["href"], HREFLANG_EN))
        elif tag == "img" and self._href is not None and dict(attrs).get("alt"):
            self._link_text.append(dict(attrs)["alt"])      # a flag picture: alt="English"
        elif tag == "meta":
            a = dict(attrs)
            if (a.get("name") or "").lower() == "description" and a.get("content"):
                self.parts.append("\n" + a["content"] + "\n")
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1
        elif tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._link_text).strip()))
            self._href = None
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._link_text.append(data)


def extract(html: str) -> tuple[str, list[tuple[str, str]]]:
    p = _Extractor()
    try:
        p.feed(html)
        p.close()
    except Exception:  # malformed HTML: keep whatever was parsed
        pass
    return "".join(p.parts), p.links


LINK_HINTS = ["pacient", "registrac", "objedn", "kontakt", "ordinac", "o-nas", "onas", "aktual", "novinky"]
# English pages are where "we speak English" is written. Their own words, since "o-nas" is "about-us" there.
EN_LINK_HINTS = ["about", "contact", "team", "patient"]


def pick_links(base_url: str, links: list[tuple[str, str]], limit: int = MAX_EXTRA_PAGES) -> list[str]:
    """Same-site links most likely to mention new patients, best first. When the site has an
    English version, its best page takes the last slot (within the same budget), so the
    "we speak English" check gets to read it."""
    base = urllib.parse.urlsplit(base_url)
    scope = _folder(base.path)
    scored: dict[str, int] = {}
    english: dict[str, int] = {}
    for href, label in links:
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urllib.parse.urljoin(base_url, href).split("#")[0]
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("http", "https") or u.hostname != base.hostname or url.rstrip("/") == base_url.rstrip("/"):
            continue
        # A doctor's page on a shared portal (gynekolog.cz/novak/): stay inside it, the rest is other doctors.
        if scope != "/" and not u.path.startswith(scope):
            continue
        if re.search(r"\.(?:pdf|jpe?g|png|gif|webp|docx?|xlsx?|zip)$", u.path, re.I):
            continue
        hay = normalize(f"{u.path} {label}")
        if is_english_link(url, label):
            # The English homepage first, then its about/contact pages.
            score = next((len(EN_LINK_HINTS) - i for i, h in enumerate(EN_LINK_HINTS) if h in hay), len(EN_LINK_HINTS) + 1)
            english[url] = max(score, english.get(url, 0))
            continue
        score = next((len(LINK_HINTS) - i for i, h in enumerate(LINK_HINTS) if h in hay), 0)
        if score:
            scored[url] = max(score, scored.get(url, 0))
    best = lambda d: [u for u, _ in sorted(d.items(), key=lambda kv: -kv[1])]
    czech, en = best(scored), best(english)
    if en and limit > 0:
        return czech[:limit - 1] + en[:1]
    return czech[:limit]


# --------------------------------------------------------------------------
# Safe fetching
# --------------------------------------------------------------------------
class BlockedURL(Exception):
    pass


def check_url(url: str, allow_private: bool = False) -> None:
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise BlockedURL(f"unsupported URL: {url}")
    if allow_private:
        return
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    except socket.gaierror as e:
        raise BlockedURL(f"DNS failed for {u.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise BlockedURL(f"{u.hostname} resolves to non-public {ip}")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_private: bool):
        self.allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl, self.allow_private)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _decode(body: bytes, content_type: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type, re.I) or re.search(
        rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4096], re.I)
    charset = m.group(1) if m else None
    if isinstance(charset, bytes):
        charset = charset.decode("ascii", "ignore")
    for enc in filter(None, [charset, "utf-8", "cp1250"]):
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", "replace")


def fetch(url: str, allow_private: bool = False, html_only: bool = True) -> tuple[str, str]:
    """GET a page (HTML unless html_only=False). Returns (final_url, text)."""
    check_url(url, allow_private)
    opener = urllib.request.build_opener(_SafeRedirect(allow_private))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.5",
                                               "Accept-Language": "cs,en;q=0.5"})
    with opener.open(req, timeout=TIMEOUT) as r:
        ctype = r.headers.get("Content-Type", "")
        if html_only and ctype and "html" not in ctype.lower():
            raise ValueError(f"not HTML ({ctype.split(';')[0]})")
        return r.geturl(), _decode(r.read(MAX_BYTES), ctype)


# Not the practice's own website: social networks, business directories, webmail.
NOT_OWN_SITE = re.compile(
    r"(^|\.)(facebook\.com|fb\.com|instagram\.com|linkedin\.com|twitter\.com|x\.com|youtube\.com|"
    r"firmy\.cz|seznam\.cz|google\.[a-z.]+|goo\.gl|mapy\.cz|mapy\.com|zlatestranky\.cz|najisto\.centrum\.cz|"
    r"zdravotniregistr\.cz)$")


# Words that name a field of care. A sentence naming only other fields ("přijímáme do
# diabetologické ambulance" on a hospital site) says nothing about this practice.
FIELD_WORDS = {   # matched against normalize()d text: lower-case, no diacritics
    "zubar": r"zub|stomato|dent", "hygienistka": r"hygien",
    "praktik": r"praktick|vseobecn", "pediatr": r"detsk|pediatr|dorost|praktick",
    "gynekolog": r"gynekolog|porodn", "ocni": r"\bocni|\bocn|oftalmolog",
    "orl": r"\borl\b|\busni|\bkrcni|otorinolaryng", "kozni": r"\bkozni|dermatolog",
    "psychiatr": r"psychiatr", "neurolog": r"neurolog",
}
OTHER_FIELDS = (r"diabetolog|kardiolog|\binterni|internist|ortoped|chirurg|urolog|endokrinolog|gastroenterolog|"
                r"revmatolog|alergolog|\bplicni|pneumolog|onkolog|nefrolog|rehabilita|psycholog|logoped|"
                r"fyzioterap|mamolog|\bcevni|hematolog|angiolog|geriatr|sexuolog|algeziolog")


def fits_specialty(snippet: str, specialties: set[str]) -> bool:
    """False when the sentence names fields of care, none of them this practice's."""
    s = normalize(snippet)
    own = [FIELD_WORDS[x] for x in specialties if x in FIELD_WORDS]
    if any(re.search(w, s) for w in own):
        return True
    others = [w for k, w in FIELD_WORDS.items() if k not in specialties] + [OTHER_FIELDS]
    return not any(re.search(w, s) for w in others)


def normalize_site(web: str) -> str | None:
    parts = (web or "").replace(",", " ").replace(";", " ").split()
    if not parts:
        return None
    w = parts[0]
    if "@" in w.split("/")[0 if "://" not in w else 2]:      # an e-mail address in the web field
        return None
    if not re.match(r"^https?://", w, re.I):
        w = "http://" + w
    u = urllib.parse.urlsplit(w)
    if not u.hostname or "." not in u.hostname or NOT_OWN_SITE.search(u.hostname):
        return None
    return urllib.parse.urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path or "/", u.query, ""))


# --------------------------------------------------------------------------
# Crawl
# --------------------------------------------------------------------------
@dataclass
class SiteResult:
    url: str
    verdict: Verdict | None = None
    page_url: str | None = None
    flags: dict[str, str] = field(default_factory=dict)   # e.g. {"self_pay": snippet}, from any page we read
    error: str | None = None
    provider_ids: list[int] = field(default_factory=list)


def _variants(url: str) -> list[str]:
    """The address as listed, then the usual fixes: other scheme, with/without www."""
    u = urllib.parse.urlsplit(url)
    host = u.netloc
    try:
        ipaddress.ip_address(u.hostname or "")
        hosts_differ = False                    # no "www." for a bare IP address
    except ValueError:
        hosts_differ = True
    other_host = host[4:] if host.startswith("www.") else "www." + host
    other_scheme = "https" if u.scheme == "http" else "http"
    seen, out = set(), []
    combos = [(u.scheme, host), (other_scheme, host)]
    if hosts_differ:
        combos += [(u.scheme, other_host), (other_scheme, other_host)]
    for scheme, h in combos:
        v = urllib.parse.urlunsplit((scheme, h, u.path, u.query, ""))
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _fetch_any(url: str, allow_private: bool) -> tuple[str, str]:
    """Fetch, retrying the usual address fixes when the site can't be reached as listed
    (dead http, broken certificate, missing www). Slow sites (timeouts) are not retried."""
    first: Exception | None = None             # if nothing works, report what the listed address said
    for cand in _variants(url):
        try:
            return fetch(cand, allow_private)
        except BlockedURL:
            raise
        except Exception as e:
            first = first or e
            if isinstance(e, urllib.error.HTTPError) and e.code < 500 and e.code not in (403, 404):
                break                           # the server answered clearly; another address won't help
            if isinstance(e, (TimeoutError, socket.timeout)) or "timed out" in str(e):
                break                           # slow site: retrying only doubles the wait
    assert first is not None
    raise first


def error_kind(error: str) -> str:
    """Group crawl errors for the run summary."""
    e = error.lower()
    for needle, kind in (("http 4", error.split(":")[0]), ("http 5", "HTTP 5xx"), ("timed out", "timeout"),
                         ("timeout", "timeout"), ("certificate", "TLS/certificate"), ("ssl", "TLS/certificate"),
                         ("dns failed", "DNS: domain not found"), ("name or service", "DNS: domain not found"), ("nodename", "DNS: domain not found"),
                         ("getaddrinfo", "DNS: domain not found"), ("no address", "DNS: domain not found"),
                         ("refused", "connection refused"), ("reset", "connection reset"),
                         ("not html", "not HTML"), ("robots", "blocked by robots.txt"), ("non-public", "non-public address")):
        if needle in e:
            return kind
    return error.split(":")[0][:40]


def check_site(url: str, allow_private: bool = False, delay: float = HOST_DELAY) -> SiteResult:
    res = SiteResult(url)
    robots = urllib.robotparser.RobotFileParser()
    try:
        _, robots_txt = fetch(urllib.parse.urljoin(url, "/robots.txt"), allow_private, html_only=False)
        robots.parse(robots_txt.splitlines())
    except Exception:
        robots.parse([])  # missing/broken robots.txt: allowed
    try:
        if not robots.can_fetch(USER_AGENT, url):
            res.error = "blocked by robots.txt"
            return res
        final_url, html = _fetch_any(url, allow_private)
        text, links = extract(html)
        best = classify(text)
        best_page = final_url
        res.flags = find_flags(text)
        if en_url := english_version(final_url, links):
            res.flags["en_site"] = en_url
        labels: dict[str, str] = defaultdict(str)
        for href, label in links:
            labels[urllib.parse.urljoin(final_url, href or "").split("#")[0]] += " " + label
        for extra in pick_links(final_url, links):
            # Czech pages only while we still don't know about new patients; the English
            # page only while we still don't know whether they speak English.
            is_en = is_english_link(extra, labels[extra])
            if (best.status or best.conflicting) and not (is_en and "english" not in res.flags):
                continue
            if not robots.can_fetch(USER_AGENT, extra):
                continue
            time.sleep(delay)
            try:
                page_url, page_html = fetch(extra, allow_private)
            except Exception:
                continue
            page_text = extract(page_html)[0]
            res.flags = {**find_flags(page_text), **res.flags}
            if best.status is None and not best.conflicting:
                v = classify(page_text)
                if v.status or v.conflicting:
                    best, best_page = v, page_url
        res.verdict, res.page_url = best, best_page
    except BlockedURL as e:
        res.error = str(e)
    except urllib.error.HTTPError as e:
        res.error = f"HTTP {e.code}"
    except Exception as e:  # timeouts, TLS, DNS, connection resets…
        res.error = type(e).__name__ + (f": {e}" if str(e) else "")
    return res


def crawl(sites: dict[str, list[int]], workers: int = 8, allow_private: bool = False,
          delay: float = HOST_DELAY, progress=None) -> list[SiteResult]:
    """Check each site; sites on the same host are checked one after another."""
    by_host: dict[str, list[str]] = defaultdict(list)
    for url in sites:
        by_host[urllib.parse.urlsplit(url).hostname or url].append(url)

    def run_host(urls: list[str]) -> list[SiteResult]:
        out = []
        for i, u in enumerate(urls):
            if i:
                time.sleep(delay)
            r = check_site(u, allow_private, delay)
            r.provider_ids = sites[u]
            out.append(r)
        return out

    results: list[SiteResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(run_host, urls) for urls in by_host.values()]):
            for r in fut.result():
                results.append(r)
                if progress:
                    progress(r)
    return results
