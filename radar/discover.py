"""Find practice websites the register doesn't list.

Two kinds of candidates:
  - the practice's e-mail is on its own domain (ordinace@zubarnovak.cz): try zubarnovak.cz;
  - a visitor typed the address into the report form ("Znáte web ordinace?").

A candidate is kept only when its homepage (or its contact page) reads like this practice's
own site: it names the practice (company ID, or a distinctive word of its name such as the
doctor's surname) and talks about care. Same politeness as the crawler: robots.txt, one
request at a time per host, a pause between hosts' pages.
"""
from __future__ import annotations

import re
import time
import urllib.parse
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from .crawler import (HOST_DELAY, USER_AGENT, BlockedURL, _fetch_any, error_kind, extract, fetch,
                      normalize, normalize_site)

# Mailbox providers and ISPs: the domain is theirs, not the practice's.
FREEMAIL = re.compile(
    r"^(?:seznam|email|post|centrum|volny|atlas|tiscali|quick|iol|gmail|googlemail|outlook|hotmail|live|msn|"
    r"yahoo|ymail|icloud|me|mac|mybox|razdva|raz-dva|cbox|c-box|o2active|chello|upcmail|tlapnet|cmail|unet|iex|"
    r"hbnet|worldonline|gmx|proton|protonmail|pm|azet|zoznam|mujmail|inmail|bluetone|t-email|vodafonemail|"
    r"wo|ktknet|kabel1|lpnet|netbox|onet|seznam-mail|telecom|o2|vodafone|tmobile|t-mobile)\.[a-z.]+$")

# Words in a practice's name that don't identify it ("MUDr.", "Zubní ordinace", "s.r.o.").
_GENERIC = set("""
mudr mddr mvdr phdr rndr ing mgr bc doc prof csc phd dr med dent sro spol as os ops zs vos
ordinace ordinaci ordinacni zubni zubar zubari stomatologie stomatologicka stomatologicke stomatologicky
stomatolog lekar lekarka lekari lekarska lekarske praktick prakticka prakticky prakticke detsk detska
detsky detske dorost dorostu pro deti dospele ambulance ambulantni centrum center klinika kliniky
poliklinika zdravotni zdravotnicke zdravotnicka zdravi sluzby sluzba pece medical medicine medic
dental dentist clinic health care family studio praxe privatni soukroma soukrome nemocnice
gynekologie gynekologicka gynekologicke porodnictvi pediatrie pediatricka ocni ocniho kozni neurologie
neurologicka psychiatrie psychiatricka urologie urologicka chirurgie chirurgicka ortopedie ortopedicka
orl ambulantni hygiena dentalni hygienistka the and und von van
""".split())
# Something a doctor's site says; an unrelated company sharing a surname rarely does.
_CARE = re.compile(r"ordinac|lekar|mudr|mddr|pacient|zubn|ambulanc|klinik|stomatolog|ordinacni hodiny|"
                   r"zdravotn|pojistovn|ockovan|prohlidk|vysetren")
# A domain shared by more practices than this is a hospital or a chain: the crawler skips it anyway.
MAX_PER_DOMAIN = 3
RETRY_DAYS = 90


def own_domain(email: str | None) -> str | None:
    """'ordinace@zubarnovak.cz' -> 'zubarnovak.cz'; None for a mailbox provider."""
    m = re.search(r"@([a-z0-9.-]+\.[a-z]{2,})\b", (email or "").lower())
    if not m:
        return None
    host = m.group(1).strip(".")
    host = host[4:] if host.startswith("www.") else host
    if FREEMAIL.match(host) or not normalize_site(host):
        return None
    return host


_TITLE = re.compile(r"\b(?:mudr|mddr|mvdr|phdr|mgr|bc|doc|prof|ing)\b")


def name_tokens(name: str, city: str | None = None) -> set[str]:
    """Distinctive words of a practice's name: the doctor's surname, a brand ('Amodent').
    For "MUDr. Anna Trnečková s.r.o." only the surname: a first name is on half the web."""
    skip = _GENERIC | set(normalize(city or "").split())
    n = normalize(name)
    words = [w for w in re.findall(r"[a-z0-9]+", n) if len(w) >= 5 and w not in skip]
    if _TITLE.search(n) and words:
        # "MUDr. Jana Nováková" / "MUDr. Jana Nováková, s.r.o.": the surname is the last word
        # of the first person named (before a comma or a second doctor).
        person = re.split(r"[,;]|\ba\b|\bmudr\b(?=.*\bmudr\b)", n)[0]
        own = [w for w in re.findall(r"[a-z0-9]+", person) if len(w) >= 5 and w not in skip]
        return {own[-1]} if own else {words[-1]}
    return set(words)


def _names_it(text: str, tokens: set[str], ico: str | None) -> bool:
    if ico and len(ico.lstrip("0")) >= 6 and re.search(rf"\b0*{ico.lstrip('0')}\b", text):
        return True
    for t in tokens:
        # Czech names decline: "Nováková" / "Novákové", "Amodent" / "Amodentu". Allow a changed ending.
        stem = t[:-2] if len(t) >= 6 else t
        if re.search(rf"\b{re.escape(stem)}[a-z]{{0,3}}\b", text):
            return True
    return False


def _in_domain(host: str, name: str) -> bool:
    """'minicare.cz' for "Mini Care s.r.o.", 'mudrsanda.cz' for "MUDr. Matěj Šanda"."""
    label = re.sub(r"[^a-z0-9]", "", normalize(host.split(".")[-2] if host.count(".") else host))
    words = [w for w in re.findall(r"[a-z0-9]+", normalize(name)) if len(w) >= 4 and w not in _GENERIC]
    return any(w in label for w in words)


def looks_like_practice(raw_text: str, name: str, ico: str | None, city: str | None, host: str = "") -> bool:
    text = normalize(raw_text)
    if not _CARE.search(text):
        return False
    return _names_it(text, name_tokens(name, city), ico) or (bool(host) and _in_domain(host, name))


@dataclass
class Candidate:
    url: str                        # normalized address to try
    source: str                     # 'email' | 'user'
    providers: list[tuple[int, str, str | None, str | None]]   # (id, name, ico, city)


@dataclass
class Found:
    candidate: Candidate
    url: str | None = None          # the site's address, when it is theirs
    matched: list[int] | None = None
    error: str | None = None


def _robots(url: str, allow_private: bool) -> urllib.robotparser.RobotFileParser:
    rp = urllib.robotparser.RobotFileParser()
    try:
        _, txt = fetch(urllib.parse.urljoin(url, "/robots.txt"), allow_private, html_only=False)
        rp.parse(txt.splitlines())
    except BlockedURL:
        raise
    except Exception:
        rp.parse([])
    return rp


def check_candidate(c: Candidate, allow_private: bool = False, delay: float = HOST_DELAY) -> Found:
    res = Found(c)
    try:
        rp = _robots(c.url, allow_private)
        if not rp.can_fetch(USER_AGENT, c.url):
            res.error = "blocked by robots.txt"
            return res
        final, html = _fetch_any(c.url, allow_private)
        text, links = extract(html)
        pages = [text]
        # Not named on the homepage: the contact page usually names the doctor and the company ID.
        contact = next((urllib.parse.urljoin(final, h) for h, label in links
                        if re.search(r"kontakt|o-nas|onas|o nas|about", normalize(f"{h} {label}"))), None)
        if contact and urllib.parse.urlsplit(contact).hostname == urllib.parse.urlsplit(final).hostname \
                and rp.can_fetch(USER_AGENT, contact):
            host = urllib.parse.urlsplit(final).hostname or ""
            unmatched = [p for p in c.providers if not looks_like_practice(text, p[1], p[2], p[3], host)]
            if unmatched:
                time.sleep(delay)
                try:
                    pages.append(extract(fetch(contact, allow_private)[1])[0])
                except BlockedURL:
                    raise
                except Exception:
                    pass
        site = "\n".join(pages)
        host = urllib.parse.urlsplit(final).hostname or ""
        res.matched = [pid for pid, name, ico, city in c.providers if looks_like_practice(site, name, ico, city, host)]
        if res.matched:
            u = urllib.parse.urlsplit(final)
            res.url = urllib.parse.urlunsplit((u.scheme, u.netloc, "/", "", ""))
    except Exception as e:  # noqa: BLE001 - a dead or odd site is a normal outcome here
        res.error = f"{type(e).__name__}: {e}"[:200]
    return res


def candidates(conn, limit: int | None = None) -> list[Candidate]:
    """Practices without a known website: visitors' suggestions first, then own-domain e-mails."""
    rows = conn.execute(
        """
        SELECT id, name, ico, city, email, web_suggested FROM providers
         WHERE active AND coalesce(web, '') = '' AND web_found IS NULL
           AND (web_suggested IS NOT NULL
                OR (email LIKE '%%@%%' AND (web_search_at IS NULL OR web_search_at < now() - make_interval(days => %s))))
         ORDER BY web_suggested IS NULL, id
        """, (RETRY_DAYS,)).fetchall()
    by_url: dict[tuple[str, str], list] = {}
    for pid, name, ico, city, email, suggested in rows:
        if suggested and (url := normalize_site(suggested)):
            by_url.setdefault((url, "user"), []).append((pid, name, ico, city))
        elif (host := own_domain(email)) and (url := normalize_site(host)):
            by_url.setdefault((url, "email"), []).append((pid, name, ico, city))
    # A domain many practices share is a hospital or chain e-mail (…@nemocnice.cz): not one practice's site.
    shared = {u for (u, s), ps in by_url.items() if s == "email" and len(ps) > MAX_PER_DOMAIN}
    out = [Candidate(u, s, ps) for (u, s), ps in by_url.items() if u not in shared]
    return out[:limit] if limit is not None else out


def run(cands: list[Candidate], workers: int = 8, allow_private: bool = False, delay: float = HOST_DELAY,
        progress=None) -> list[Found]:
    results: list[Found] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(check_candidate, c, allow_private, delay) for c in cands]):
            r = fut.result()
            results.append(r)
            if progress:
                progress(r)
    return results


def record(conn, results: list[Found]) -> int:
    """Store found sites; remember we looked, so a dead domain isn't retried every week."""
    found = 0
    with conn.transaction():
        for r in results:
            ids = [p[0] for p in r.candidate.providers]
            conn.execute("UPDATE providers SET web_search_at = now() WHERE id = ANY(%s)", (ids,))
            if r.candidate.source == "user":     # checked: keep it only if it held up
                conn.execute("UPDATE providers SET web_suggested = NULL WHERE id = ANY(%s)", (ids,))
            if r.url and r.matched:
                conn.execute("UPDATE providers SET web_found = %s, web_found_source = %s "
                             "WHERE id = ANY(%s) AND coalesce(web, '') = ''", (r.url, r.candidate.source, r.matched))
                found += len(r.matched)
    return found


__all__ = ["own_domain", "name_tokens", "looks_like_practice", "candidates", "check_candidate", "run", "record",
           "error_kind"]
