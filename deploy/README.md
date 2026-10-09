# Going live

The whole site runs on one small server, with everything in Docker:

```
Internet ─▶ Caddy (HTTPS, automatic certificates) ─▶ web (Astro/Node) ─▶ Postgres
                                                       importer (runs once a month)
```

Expect about **€5–8/month** for the server, plus a few hundred Kč a year for a `.cz` domain.
Check current prices; they change.

## 1. Buy the pieces (about 20 min)

1. **A domain.** Use any Czech registrar (Wedos, Forpsi, Active24, Subreg…). Check first that the
   name is free, e.g. `prijimanovepacienty.cz`.
2. **A server.** Hetzner Cloud is cheap and close to CZ (Falkenstein or Nuremberg). Pick the smallest
   x86 plan with **2 vCPU / 4 GB RAM** and the **Ubuntu 24.04** image, and add your SSH key while creating it.
   Any VPS with Ubuntu 24.04 works the same way.
3. **DNS.** In the registrar's DNS settings, add two `A` records pointing at the server's IPv4 address:
   `@` → IP and `www` → IP. If the server also has an IPv6 address, add matching `AAAA` records.
   Wait until `ping yourdomain.cz` answers from that IP. This can take anywhere from minutes to a few hours.

## 2. Set up the server (about 10 min)

```bash
ssh root@YOUR_SERVER_IP
git clone https://github.com/barborastuchla96/zubar-radar.git /opt/zubar-radar
cd /opt/zubar-radar
./deploy/setup-server.sh        # updates, Docker, firewall, secrets, cron jobs
nano deploy/.env                # set DOMAIN, ACME_EMAIL (NRPZS_URL: see step 4)
./deploy/deploy.sh              # builds and starts everything, ~3 min the first time
```

Open `https://yourdomain.cz`. Caddy gets the HTTPS certificate on the first visit.
If the page doesn't load, DNS usually isn't ready yet (see step 1).

The repo is public, so the plain `https://` clone above needs no keys. If you ever make it
private, add a **deploy key** instead: run `ssh-keygen -t ed25519` on the server, add
`~/.ssh/id_ed25519.pub` under the repo's Settings → Deploy keys, and clone with
`git@github.com:barborastuchla96/zubar-radar.git`.

Because the code is public, never commit `deploy/.env`, the NRPZS CSV or backups. `.gitignore`
already excludes them, but check `git status` before you commit.

## 3. Load the data

Copy the NRPZS CSV from your computer:

```bash
scp nrpzs.csv root@YOUR_SERVER_IP:/opt/zubar-radar/data/nrpzs.csv
ssh root@YOUR_SERVER_IP 'cd /opt/zubar-radar/deploy && docker compose run --rm importer import /data/nrpzs.csv'
```

## 4. Automatic jobs (set up by setup-server.sh, or `./deploy/cron.sh` to update them)

| When | What | Log |
|---|---|---|
| Daily at 03:17 | `backup.sh`: database dump into `backups/`, last 14 days kept | `/var/log/berepacienty/backup.log` |
| Daily at 07:41 | `alerts`: emails subscribers about practices near them that started accepting (only when `SMTP_HOST` is set) | `/var/log/berepacienty/alerts.log` |
| 2nd of each month at 04:23 | `import-monthly.sh`: downloads `NRPZS_URL` and imports it | `/var/log/berepacienty/import.log` |
| Sundays at 05:11 | `crawl`: checks clinic websites nationwide for new-patient notices (~1 h) | `/var/log/berepacienty/crawl.log` |

For the monthly import, put the direct CSV download link (from nrpzs.uzis.cz or data.gov.cz) into
`NRPZS_URL` in `deploy/.env`. Until you do, the job does nothing and logs a message saying so.

**Off-site backups.** Backups on the same server don't help if the server dies. Order a Hetzner
Storage Box (the smallest is plenty), turn on SSH access in its settings, then run once:
`./deploy/setup-offsite-backup.sh u123456@u123456.your-storagebox.de`. It creates a backup-only key,
installs it on the box (asks for the box password once) and from then on `backup.sh` mirrors
`backups/` to the box every night. To restore:
`docker compose exec -T db pg_restore -U radar -d radar --clean < backups/radar-YYYY-MM-DD.dump`.

**Encrypted backups.** Backups contain subscribers' e-mail addresses. Run
`./deploy/setup-backup-encryption.sh` once: it creates a key pair, keeps only the public key on the server
(`BACKUP_AGE_RECIPIENT` in `.env`) and prints the private key once. Save it in a password manager: the
server can't read its own backups, so a leaked backup or off-site copy is useless without it. To restore,
put the key in a file and run
`age -d -i key.txt backups/radar-YYYY-MM-DD.dump.age | docker compose exec -T db pg_restore -U radar -d radar --clean`.
The server disk itself is not encrypted: full-disk encryption on a VPS means typing a password over a
rescue console after every reboot, and the site stays down until you do.

## Clinic website checker

`docker compose run --rm importer crawl --dry-run` checks the clinic websites of all practices
in the country and prints what it finds, without writing anything. Drop `--dry-run` to save the
findings (saved every 300 sites, so an interrupted run keeps its progress). They're stored as
low-weight `web_crawl` signals and shown on the site as "podle webu ordinace" (based on the
clinic's website). Options: `--specialty zubar`, `--limit 50`, `--city '^brno$'`.

Skipped on purpose: social networks, business directories and e-mail addresses in the web field
(not the practice's own site), and any site listed by more than 5 practices (a hospital or chain
homepage can't be pinned to one practice). On shared portals like `gynekolog.cz/novak/` the checker
stays inside that doctor's pages.

The checker identifies itself as `PrijimaNovePacientyBot`, honours robots.txt, waits between requests
to the same site, and refuses private network addresses.

## Everyday commands (run in `deploy/`)

```bash
git pull && ./deploy.sh                       # ship a new version
docker compose logs -f web                    # watch the app logs
docker compose ps                             # is everything healthy?
docker compose run --rm importer sponsor list # sponsored listings (see below)
```

## Email alerts

Visitors can ask to be emailed when a practice near them starts accepting new patients. The form
appears on city pages, Prague pages and practice pages, **only once `SMTP_HOST` is set** in `deploy/.env`:

```
SMTP_HOST=wes1-smtp.wedos.net
SMTP_PORT=465
SMTP_USER=info@prijimanovepacienty.cz
SMTP_PASSWORD=...      # the mailbox password
```

Then run `./deploy.sh`. How it works:
- Sign-up is double opt-in. The email link opens a page with a confirm button, so mail scanners can't confirm for someone else.
- Unconfirmed sign-ups are deleted after 7 days. Unsubscribing deletes the row.
- The daily job sends one email per subscriber, listing practices within their radius that got a *new*
  "accepting" report after they signed up. The same practice isn't announced to them again for 60 days.
- All mail (confirmations and alerts) shares `MAIL_DAILY_LIMIT` (default 450; Wedos allows 500 a day).
  Alerts that don't fit wait for the next day.
- Limits against abuse: 5 sign-ups per visitor and 3 per address in 24 h, 10 active per address, and at most 3 confirmation emails per sign-up, sent at least 10 minutes apart.

Try it without sending anything: `docker compose run --rm importer alerts --dry-run`.

## Ads & cookies

**The site sets no cookies at all**, so with `ADS_PROVIDER=none` or `direct` it needs **no cookie banner**.
That is the cleanest launch. The `/cookies` page explains this to visitors, and its text changes with the mode.

| `ADS_PROVIDER` | What shows | Cookie banner? |
|---|---|---|
| `none` | Nothing | No |
| `direct` | Sponsored listings you sell to clinics yourself, labelled "Reklama" | No (no tracking) |
| `sklik` | Direct listings plus Sklik ad zones | **Yes**: a TCF consent platform, required for Sklik/Seznam SSP |

**Direct sponsored listings** (start here, no cookies needed):

```bash
docker compose run --rm importer sponsor add 1234 --specialty zubar --tagline "Volné termíny do 14 dnů" --end 2026-12-31
docker compose run --rm importer sponsor list
docker compose run --rm importer sponsor end 7
```

A listing shows at the top of `/<specialty>/<city>`. By default that's the clinic's own city;
use `--city praha-6` for another one. At most 2 listings show per page, in random order.

**Sklik.** Network ads need consent under Czech law, which since 2022 has required opt-in for
non-essential cookies. Sklik and Seznam SSP use the IAB TCF standard for this, and Seznam offers
its partners a free TCF consent platform (CMP). In your Seznam partner admin:
1. Get the CMP code and save it as `web/src/ads/cmp-head.html`.
2. Create the ad zones and save each zone's code as `web/src/ads/<slot>.html`. The slot names are listed in `web/src/ads/README.md`.
3. Set `ADS_PROVIDER=sklik` in `deploy/.env` and run `./deploy.sh`.

The CMP handles the banner, the "reject" button and the consent signal. The site only loads its
code in `sklik` mode.

*Google AdSense* in the EU also requires a Google-certified TCF CMP. Google's own one is free in
the AdSense dashboard under Privacy & messaging. For a Czech site, Sklik is the more natural first network.

**Analytics without cookies (optional).** Umami doesn't use cookies, so it needs no consent.
Use Umami Cloud or self-host it, then set `ANALYTICS_SRC` (the script URL) and
`ANALYTICS_ID` (the website ID) in `.env` and run `./deploy.sh`. Avoid Google Analytics: it would
bring the cookie banner back.

## Security notes

- **Firewall:** only ports 22, 80 and 443 are open. Postgres is never exposed outside Docker.
- **Server updates:** security updates install automatically (unattended-upgrades), and fail2ban blocks SSH brute-forcing.
- **Secrets:** they live only in `deploy/.env` (permissions 600, not in git). If you change `REPORT_SALT`, anti-spam starts from zero.
- **SSH:** consider turning off password login. Set `PasswordAuthentication no` in `/etc/ssh/sshd_config`, after confirming your SSH key works.
