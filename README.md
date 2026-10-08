# Bere pacienty?

This site shows which Czech dentists, GPs, pediatricians and gynecologists are taking new patients.
The list of practices comes from the official NRPZS register. Each practice's status
(taking / not taking new patients) comes from reports by patients and by the practices themselves.

```
radar/        Python: NRPZS importer, Postgres schema, status scoring, CLI
web/          Astro (SSR, Node): public website + report API
tests/        pytest (parser + DB integration)
```

## Quick start

```bash
# 1. Database (Postgres 13+, needs the contrib extensions cube + earthdistance)
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:pass@127.0.0.1:5432/radar
python -m radar initdb

# 2. Data: the "Místa poskytování zdravotních služeb" CSV from nrpzs.uzis.cz / data.gov.cz
python -m radar inspect nrpzs.csv    # check the column mapping
python -m radar import  nrpzs.csv    # ~16k tracked practices, a few seconds

# 3. Website
cd web && npm ci
cp .env.example .env                 # set DATABASE_URL, REPORT_SALT, SITE_URL
npm run build
set -a; . ./.env; set +a; npm start  # http://localhost:4321
```

Use a TCP host in `DATABASE_URL` for the website (`@127.0.0.1:5432`). The Node client can't
handle a Unix-socket URL with an empty host.

## Importer

`radar/nrpzs.py` works out the encoding (UTF-8 or cp1250), the separator and the column names
itself. It understands the old column names (`MistoPoskytovaniId`, `OborPece`, `GPS`, …) and
the ones introduced in the Nov 2025 rename (`ZZ_*`). The Oct 2026 export uses the old names
and imports with no extra options. If an export ever uses names it doesn't know, use `--map field=Header`.

Re-imports update existing rows. A practice that has dropped out of the register is marked
inactive, unless the new file has fewer than 80% of the currently active practices
(that usually means a truncated download).

## Status logic (`provider_status` view in `radar/schema.sql`)

| Source | Weight |
|---|---|
| clinic | 1.0. An update from the clinic in the last 30 days is final. |
| region | 0.8 |
| user | 0.6 |
| web_crawl | 0.4 |

Each report loses half its weight every 30 days and is ignored after 90 days.
The score is the weighted mean of +1 (accepting), +0.3 (waitlist) and −1 (not accepting).
If the total weight is below 0.3 the status is `unknown`; a score ≥ 0.5 is `accepting`,
≤ −0.5 is `not_accepting`, and anything in between is `mixed`.

## Website

| Route | What |
|---|---|
| `/` | Homepage with the launch city (Brno) and "near me" (browser geolocation) |
| `/{obor}` | All towns for a specialty, grouped by region |
| `/{obor}/{mesto}` | List + map + filters. Pages with fewer than 3 practices are `noindex` |
| `/lekar/{id}-{slug}` | Practice detail page and the report form. Other slugs 301-redirect to the canonical URL |
| `/blizko` | Results near a location (`noindex`) |
| `POST /api/report` | Saves a user report |
| `/sitemap.xml`, `/robots.txt` | SEO |

**How reports are protected from spam and abuse**
- The form works without JavaScript.
- A hidden honeypot field and a signed timing token (3 s to 6 h) filter out bots. Bots get a fake "thanks" so they don't learn they were caught.
- Astro's built-in origin check blocks cross-site submissions.
- Each reporter is limited to one report per practice and 10 reports in 24 hours.
- Reporters are identified only by a salted hash of their IP (`REPORT_SALT`). Raw IPs are never stored.
- Behind a reverse proxy, set `TRUST_PROXY=1` so the real client IP is read from `X-Forwarded-For`.

**Ads.** `<AdSlot>` placeholders sit on the homepage, the city pages (after the 6th practice) and
the practice pages. They render only when `PUBLIC_ADS_ENABLED=1`. Add a consent banner (CMP)
before turning on Sklik or AdSense, because EU law requires consent before ad cookies.

**Maps.** Leaflet with OpenStreetMap tiles. That's fine at launch traffic; move to Mapy.com
or a paid tile provider before traffic grows (see OSM's tile usage policy).

## Deploying (suggested)

- One small VPS (Hetzner, or a Czech provider): Postgres, `node web/dist/server/entry.mjs` under systemd,
  and Caddy in front for HTTPS (with `TRUST_PROXY=1`).
- Monthly cron on the 2nd of the month (NRPZS updates on the 1st): download the CSV, then run `python -m radar import`.
- Put Cloudflare (or Caddy caching) in front. City pages send `Cache-Control: public, max-age=120`.

## Tests

```bash
pytest                                                # parser tests
TEST_DATABASE_URL=postgresql://.../empty_db pytest    # + DB tests (WIPES that database)
cd web && npx astro check                             # typecheck
```
