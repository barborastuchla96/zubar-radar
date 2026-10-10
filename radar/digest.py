"""Evening e-mail to the site owner: what visitors reported in the last 24 hours.

Reports go live on the site straight away; this is so a human sees them the same day
(and can remove spam or a wrong note). Only counts for alert sign-ups: subscribers'
addresses never go into this e-mail.
"""
from __future__ import annotations

from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

STATUS = {"accepting": "✓ přijímá", "waitlist": "⏳ pořadník", "not_accepting": "✗ nepřijímá"}
SCOPE = {"adults": " (jen dospělé)", "children": " (jen děti)", "all": ""}
FLAG = {"self_pay": "bez smlouvy s pojišťovnou", "english": "mluví anglicky"}


def gather(conn: psycopg.Connection, hours: int = 24) -> dict:
    since = f"{int(hours)} hours"
    with conn.cursor(row_factory=dict_row) as cur:
        reports = cur.execute(
            """
            SELECT a.created_at, a.status, a.scope, a.note, p.id, p.name, p.city
              FROM availability_signals a JOIN providers p ON p.id = a.provider_id
             WHERE a.source = 'user' AND a.created_at > now() - %s::interval
             ORDER BY a.created_at
            """, (since,)).fetchall()
        # Flags come with a report ("they speak English", insurers): show those of today's reports.
        flags = cur.execute(
            """
            SELECT f.flag, p.id, p.name, p.city FROM provider_flags f JOIN providers p ON p.id = f.provider_id
             WHERE f.source = 'user' AND f.provider_id IN (
                   SELECT provider_id FROM availability_signals WHERE source = 'user' AND created_at > now() - %s::interval)
             ORDER BY p.name, f.flag
            """, (since,)).fetchall()
        sites = cur.execute(
            "SELECT id, name, city, web_suggested FROM providers WHERE web_suggested IS NOT NULL ORDER BY name"
        ).fetchall()
        subs = cur.execute(
            """
            SELECT count(*) FILTER (WHERE created_at > now() - %s::interval) AS new,
                   count(*) FILTER (WHERE verified_at > now() - %s::interval) AS confirmed,
                   count(*) FILTER (WHERE verified_at IS NOT NULL AND unsubscribed_at IS NULL) AS active
              FROM subscriptions
            """, (since, since)).fetchone()
    return {"reports": reports, "flags": flags, "sites": sites, "subs": subs}


def is_empty(d: dict) -> bool:
    return not (d["reports"] or d["flags"] or d["sites"] or d["subs"]["new"] or d["subs"]["confirmed"])


def compose(d: dict, site_url: str, mail_from: str, to: str) -> EmailMessage:
    site = site_url.rstrip("/")
    n = len(d["reports"])
    lines = [f"Za posledních 24 hodin: {n} hlášení od návštěvníků.", ""]
    for r in d["reports"]:
        when = r["created_at"].astimezone(ZoneInfo("Europe/Prague")).strftime("%H:%M")
        lines.append(f"{when}  {STATUS.get(r['status'], r['status'])}{SCOPE.get(r['scope'], '')}  "
                     f"{r['name']}, {r['city'] or ''}")
        if r["note"]:
            lines.append(f"       Poznámka: „{r['note']}“")
        lines.append(f"       {site}/lekar/{r['id']}")
    if d["flags"]:
        lines += ["", "Další údaje od návštěvníků:"]
        for f in d["flags"]:
            what = FLAG.get(f["flag"]) or (f"smlouva s pojišťovnou {f['flag'][3:]}" if f["flag"].startswith("ins") else f["flag"])
            lines.append(f"  {f['name']}, {f['city'] or ''}: {what}  {site}/lekar/{f['id']}")
    if d["sites"]:
        lines += ["", "Navržené weby ordinací (robot je v neděli ověří):"]
        for s in d["sites"]:
            lines.append(f"  {s['name']}, {s['city'] or ''}: {s['web_suggested']}")
    s = d["subs"]
    lines += ["", f"Upozornění e-mailem: {s['new']} nových přihlášení, {s['confirmed']} potvrzených, "
                  f"celkem aktivních {s['active']}."]
    lines += ["", "Hlášení jsou na webu hned. Kdyby něco z toho byl spam nebo nesmysl, napiš a smažeme to.", ""]

    msg = EmailMessage()
    msg["Subject"] = f"Přijímá nové pacienty? – dnes {n} hlášení" if n else "Přijímá nové pacienty? – denní přehled"
    msg["From"] = formataddr(("Přijímá nové pacienty?", mail_from))
    msg["To"] = to
    msg["Message-ID"] = make_msgid(domain=mail_from.split("@")[-1] or None)
    msg.set_content("\n".join(lines))
    return msg
