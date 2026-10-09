"""Email alerts: tell subscribers when a practice near them starts accepting new patients.

Run daily (`python -m radar alerts`). Subscriptions are created and confirmed on the website;
this job only sends the alert emails and tidies up.
"""

from __future__ import annotations

import os
import re
import smtplib
import ssl
import sys
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from email.utils import formataddr, formatdate, make_msgid

import psycopg
from psycopg.rows import dict_row

SITE_NAME = "Přijímá nové pacienty?"
MAIL_FROM_NAME = "Barbora z prijimanovepacienty.cz"   # sender on Czech e-mails
DAILY_LIMIT = int(os.environ.get("MAIL_DAILY_LIMIT", "450"))   # Wedos: 500/day, keep a margin
RENOTIFY_DAYS = 60       # the same practice may be announced again after this long
PENDING_DAYS = 7         # unconfirmed sign-ups are deleted after this long
MAX_PER_EMAIL = 10       # practices listed in one alert

# Nominative plural, matches SPECIALTIES in web/src/lib/site.ts
SPECIALTY_PLURAL = {   # start of the Czech subject line
    "zubar": "Zubař", "praktik": "Praktický lékař", "pediatr": "Dětský lékař",
    "gynekolog": "Gynekologie", "hygienistka": "Dentální hygiena",
    "ocni": "Oční lékař", "orl": "ORL lékař", "kozni": "Kožní lékař",
    "psychiatr": "Psychiatrie", "neurolog": "Neurologie",
}
# English, matches SPECIALTY_EN in web/src/lib/i18n.ts
SPECIALTY_PLURAL_EN = {
    "zubar": "Dentists", "praktik": "GPs", "pediatr": "Pediatricians", "gynekolog": "Gynecologists",
    "hygienistka": "Dental hygienists", "ocni": "Eye doctors", "orl": "ENT doctors", "kozni": "Dermatologists",
    "psychiatr": "Psychiatrists", "neurolog": "Neurologists",
}


def _plural(n: int, one: str, few: str, many: str) -> str:
    return one if n == 1 else few if 2 <= n <= 4 else many


def _first_email(v: str | None) -> str | None:
    """The register sometimes lists several addresses in one field."""
    return next((e for e in re.split(r"[\s,;]+", v or "") if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", e, re.I)), None)


def _first_web(v: str | None) -> str | None:
    w = next((w for w in re.split(r"[\s,;]+", v or "") if "." in w and "@" not in w), None)
    return None if not w else w if re.match(r"https?://", w, re.I) else f"https://{w}"


def _km(km: float) -> str:
    return f"{km:.1f}".replace(".", ",") + " km"


def _phone(p: str | None) -> str | None:
    if not p:
        return None
    digits = "".join(c for c in p.split(",")[0] if c.isdigit())
    if len(digits) == 12 and digits.startswith("420"):
        digits = digits[3:]
    return " ".join([digits[0:3], digits[3:6], digits[6:9]]) if len(digits) == 9 else p.strip()


@dataclass
class Alert:
    subscription_id: str
    email: str
    token: str
    specialty: str
    place: str
    radius_km: int
    providers: list[dict] = field(default_factory=list)
    lang: str = "cs"


def compose(a: Alert, site_url: str, sender: str) -> EmailMessage:
    """Build the alert email (plain text: readable everywhere, hard to mark as spam)."""
    if a.lang == "en":
        return _compose_en(a, site_url, sender)
    site = site_url.rstrip("/")
    n = len(a.providers)
    spec = SPECIALTY_PLURAL.get(a.specialty, a.specialty)
    unsubscribe = f"{site}/upozorneni/odhlasit?t={a.token}"
    verb = _plural(n, "přijímá", "přijímají", "přijímá")
    noun = _plural(n, "ordinace", "ordinace", "ordinací")

    these = "tato ordinace" if n == 1 else "tyto ordinace"
    lines = [
        "Dobrý den,",
        "",
        f"máme pro vás dobrou zprávu: v lokalitě {a.place} a okolí podle posledních informací"
        f" {'přijímá' if n == 1 else 'přijímají'} nové pacienty {these}:",
        "",
    ]
    for p in a.providers:
        addr = ", ".join(filter(None, [" ".join(filter(None, [p.get("street"), p.get("house_no")])), p.get("city")]))
        lines.append(f"• {p['name']}")
        lines.append(f"  {addr} ({_km(p['km'])})" if addr else f"  {_km(p['km'])}")
        if tel := _phone(p.get("phone")):
            lines.append(f"  Telefon: {tel}")
        if mail := _first_email(p.get("practice_email")):
            lines.append(f"  E-mail: {mail}")
        if web := _first_web(p.get("practice_web")):
            lines.append(f"  Web: {web}")
        lines.append(f"  Na našem webu: {site}/lekar/{p['id']}")
        lines.append("")
    lines += [
        "Než ordinaci navštívíte, raději si to u nich ověřte telefonicky nebo e-mailem. Informace máme od pacientů,"
        " od samotných ordinací a z jejich webů, takže nemusí být vždy aktuální.",
        "",
        ("Až zjistíte, jak to dopadlo, dejte nám prosím vědět na stránce ordinace"
         f" {site}/lekar/{a.providers[0]['id']}. Pomůžete tím dalším pacientům." if n == 1 else
         "Až zjistíte, jak to dopadlo, dejte nám prosím vědět na stránce ordinace (odkazy „Na našem webu“ výše)."
         " Pomůžete tím dalším pacientům."),
        "",
        "Hezký den!",
        "",
        "Barbora",
        site.split("//")[-1],
        "",
        "--",
        f"Už nechcete dostávat upozornění? Odhlásit se můžete zde: {unsubscribe}",
    ]

    # long lines allowed in headers, so the unsubscribe URL isn't encoded into gibberish
    msg = EmailMessage(policy=SMTP_POLICY.clone(max_line_length=998))
    msg["Subject"] = f"{spec} – {a.place} a okolí: {n} {noun} {verb} nové pacienty"
    msg["From"] = formataddr((MAIL_FROM_NAME, sender))
    msg["To"] = a.email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1])
    msg["List-Unsubscribe"] = f"<{unsubscribe}>"
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content("\n".join(lines))
    return msg


def _compose_en(a: Alert, site_url: str, sender: str) -> EmailMessage:
    site = site_url.rstrip("/")
    n = len(a.providers)
    spec = SPECIALTY_PLURAL_EN.get(a.specialty, a.specialty)
    unsubscribe = f"{site}/en/alerts/unsubscribe?t={a.token}"
    lines = [
        "Hello,",
        "",
        f"good news. According to recent reports, {'this practice' if n == 1 else 'these practices'} within"
        f" {a.radius_km} km of {a.place} now {'accepts' if n == 1 else 'accept'} new patients:",
        "",
    ]
    for p in a.providers:
        addr = ", ".join(filter(None, [" ".join(filter(None, [p.get("street"), p.get("house_no")])), p.get("city")]))
        lines.append(f"• {p['name']}")
        lines.append(f"  {addr} ({_km(p['km']).replace(',', '.')})" if addr else f"  {_km(p['km']).replace(',', '.')}")
        if tel := _phone(p.get("phone")):
            lines.append(f"  Phone: +420 {tel}")
        if mail := _first_email(p.get("practice_email")):
            lines.append(f"  E-mail: {mail}")
        if web := _first_web(p.get("practice_web")):
            lines.append(f"  Website: {web}")
        lines.append(f"  On our site: {site}/en/doctor/{p['id']}")
        lines.append("")
    lines += [
        "Please contact the practice before you go. The reports come from patients, the practices"
        " themselves and their websites, and may not be completely up to date. Many practices answer e-mails more easily than calls.",
        "",
        "Once you know, please mark the result on the practice page. It helps the next person.",
        "",
        "Best regards",
        "Accepting new patients? (prijimanovepacienty.cz)",
        "",
        "--",
        f"Don't want these alerts any more? Unsubscribe here: {unsubscribe}",
    ]
    msg = EmailMessage(policy=SMTP_POLICY.clone(max_line_length=998))
    msg["Subject"] = f"{spec} near {a.place}: {n} {'practice accepts' if n == 1 else 'practices accept'} new patients"
    msg["From"] = formataddr(("Accepting new patients?", sender))
    msg["To"] = a.email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1])
    msg["List-Unsubscribe"] = f"<{unsubscribe}>"
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content("\n".join(lines))
    return msg


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
PENDING_SQL = """
SELECT s.id::text AS subscription_id, s.email, s.verify_token, s.specialty_slug, s.lang,
       coalesce(s.place_label, 'vybrané místo') AS place, s.radius_km,
       p.id, p.name, p.street, p.house_no, p.city, p.phone,
       p.email AS practice_email, p.web AS practice_web,   -- not "email": that is the subscriber's
       distance_km(s.lat, s.lng, p.lat, p.lng) AS km
  FROM subscriptions s
  JOIN provider_specialties ps ON ps.specialty_slug = s.specialty_slug
  JOIN providers p ON p.id = ps.provider_id AND p.active AND p.lat IS NOT NULL
  JOIN provider_status st ON st.provider_id = p.id AND st.status = 'accepting'
 WHERE s.verified_at IS NOT NULL AND s.unsubscribed_at IS NULL
   AND earth_box(ll_to_earth(s.lat, s.lng), s.radius_km * 1000) @> ll_to_earth(p.lat, p.lng)
   AND earth_distance(ll_to_earth(s.lat, s.lng), ll_to_earth(p.lat, p.lng)) <= s.radius_km * 1000
   -- news only: an "accepting" report that arrived after the person signed up
   AND EXISTS (SELECT 1 FROM availability_signals a
                WHERE a.provider_id = p.id AND a.status = 'accepting'
                  AND a.created_at > s.verified_at)
   AND NOT EXISTS (SELECT 1 FROM subscription_notifications n
                    WHERE n.subscription_id = s.id AND n.provider_id = p.id
                      AND n.sent_at > now() - make_interval(days => %(renotify)s))
 ORDER BY s.created_at, s.id, km
"""


def pending_alerts(conn: psycopg.Connection) -> list[Alert]:
    alerts: dict[str, Alert] = {}
    with conn.cursor(row_factory=dict_row) as cur:
        for r in cur.execute(PENDING_SQL, {"renotify": RENOTIFY_DAYS}):
            a = alerts.get(r["subscription_id"])
            if a is None:
                a = alerts[r["subscription_id"]] = Alert(
                    r["subscription_id"], r["email"], r["verify_token"], r["specialty_slug"],
                    r["place"], r["radius_km"], lang=r["lang"] or "cs")
            if len(a.providers) < MAX_PER_EMAIL:
                a.providers.append(r)
    return list(alerts.values())


def sent_last_24h(conn: psycopg.Connection) -> int:
    return conn.execute("SELECT count(*) FROM mail_log WHERE sent_at > now() - interval '24 hours'").fetchone()[0]


def mark_sent(conn: psycopg.Connection, a: Alert) -> None:
    with conn.transaction():
        conn.execute("INSERT INTO mail_log (kind) VALUES ('alert')")
        conn.execute("UPDATE subscriptions SET last_notified_at = now() WHERE id = %s", (a.subscription_id,))
        for p in a.providers:
            conn.execute(
                "INSERT INTO subscription_notifications (subscription_id, provider_id) VALUES (%s, %s)"
                " ON CONFLICT (subscription_id, provider_id) DO UPDATE SET sent_at = now()",
                (a.subscription_id, p["id"]))


def cleanup(conn: psycopg.Connection) -> int:
    """Forget unconfirmed sign-ups and old mail log entries."""
    with conn.transaction():
        n = conn.execute(
            "DELETE FROM subscriptions WHERE verified_at IS NULL AND created_at < now() - make_interval(days => %s)",
            (PENDING_DAYS,)).rowcount
        conn.execute("DELETE FROM mail_log WHERE sent_at < now() - interval '30 days'")
    return n


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------
class SMTPSender:
    """One SMTP connection for the whole run. Port 465 = SSL, anything else = STARTTLS."""

    def __init__(self, host: str, port: int, user: str, password: str):
        ctx = ssl.create_default_context()
        if port == 465:
            self.smtp = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30)
        else:
            self.smtp = smtplib.SMTP(host, port, timeout=30)
            if host not in ("localhost", "127.0.0.1"):   # local test servers speak plain SMTP
                self.smtp.starttls(context=ctx)
        if user:
            self.smtp.login(user, password)

    def send(self, msg: EmailMessage) -> None:
        self.smtp.send_message(msg)

    def close(self) -> None:
        try:
            self.smtp.quit()
        except smtplib.SMTPException:
            pass


def run(conn: psycopg.Connection, sender, site_url: str, mail_from: str, dry_run: bool = False) -> dict:
    removed = 0 if dry_run else cleanup(conn)
    alerts = pending_alerts(conn)
    budget = max(0, DAILY_LIMIT - sent_last_24h(conn))
    sent = failed = 0
    for a in alerts:
        if sent >= budget:
            break
        msg = compose(a, site_url, mail_from)
        if dry_run:
            print(msg, "\n" + "=" * 70)
            sent += 1
            continue
        try:
            sender.send(msg)
        except smtplib.SMTPRecipientsRefused:
            failed += 1          # bad address: skip it, keep going
            continue
        mark_sent(conn, a)
        sent += 1
    return {"due": len(alerts), "sent": sent, "failed": failed, "deferred": max(0, len(alerts) - sent - failed),
            "removed_unconfirmed": removed}


def send_test(conn: psycopg.Connection, sender, site_url: str, mail_from: str, to: str) -> None:
    """Send a sample alert so the real mail path can be checked without touching any data."""
    lat, lng = 50.0971, 14.3741          # Praha 6
    with conn.cursor(row_factory=dict_row) as cur:
        provs = cur.execute(
            "SELECT p.id, p.name, p.street, p.house_no, p.city, p.phone,"
            "       distance_km(%s, %s, p.lat, p.lng) AS km"
            "  FROM providers p JOIN provider_specialties ps ON ps.provider_id = p.id AND ps.specialty_slug = 'zubar'"
            " WHERE p.active AND p.lat IS NOT NULL ORDER BY km LIMIT 2", (lat, lng)).fetchall()
    msg = compose(Alert("test", to, "test-only-not-a-real-token", "zubar", "Praha 6", 5, provs), site_url, mail_from)
    msg.replace_header("Subject", "[TEST] " + msg["Subject"])
    sender.send(msg)


def sender_from_env() -> SMTPSender:
    host = os.environ.get("SMTP_HOST")
    if not host:
        sys.exit("SMTP_HOST is not set; email alerts are off")
    return SMTPSender(host, int(os.environ.get("SMTP_PORT", "465")),
                      os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASSWORD", ""))
