-- Zubař radar: database schema (PostgreSQL 13+)
--
-- Geo search uses the contrib extensions cube + earthdistance, which ship with
-- stock Postgres and every major managed host. Swap for PostGIS later if you
-- need polygons (districts, catchment areas).

CREATE EXTENSION IF NOT EXISTS cube;
CREATE EXTENSION IF NOT EXISTS earthdistance;

-- ---------------------------------------------------------------------------
-- Specialties we track, and the NRPZS "obor péče" labels that map to them.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS specialties (
    slug         text PRIMARY KEY,          -- used in URLs: /zubar/brno/
    name_cs      text NOT NULL,
    nrpzs_labels text[] NOT NULL            -- lower-case, exact label match
);

INSERT INTO specialties (slug, name_cs, nrpzs_labels) VALUES
    ('zubar',     'Zubař',                        ARRAY['zubní lékařství']),
    ('praktik',   'Praktický lékař',              ARRAY['všeobecné praktické lékařství']),
    ('pediatr',   'Praktický lékař pro děti',     ARRAY['praktické lékařství pro děti a dorost']),
    ('gynekolog', 'Gynekolog',                    ARRAY['gynekologie a porodnictví']),
    ('hygienistka','Dentální hygiena',            ARRAY['dentální hygiena', 'dentální hygienistka'])
ON CONFLICT (slug) DO UPDATE
    SET name_cs = EXCLUDED.name_cs, nrpzs_labels = EXCLUDED.nrpzs_labels;

-- ---------------------------------------------------------------------------
-- Providers: one row per NRPZS "místo poskytování" (place where care is given).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS providers (
    id                bigserial PRIMARY KEY,
    nrpzs_place_id    text NOT NULL UNIQUE,
    facility_id       text,
    ico               text,
    name              text NOT NULL,
    facility_type     text,
    street            text,
    house_no          text,
    city              text,
    city_slug         text,
    postcode          text,
    district          text,
    region            text,
    lat               double precision,
    lng               double precision,
    phone             text,
    email             text,
    web               text,
    care_form         text,
    raw_specialties   text,
    active            boolean NOT NULL DEFAULT true,
    first_seen_at     timestamptz NOT NULL DEFAULT now(),
    last_seen_at      timestamptz NOT NULL DEFAULT now(),
    CHECK ((lat IS NULL) = (lng IS NULL))
);

CREATE INDEX IF NOT EXISTS providers_city_slug_idx ON providers (city_slug) WHERE active;
CREATE INDEX IF NOT EXISTS providers_earth_idx
    ON providers USING gist (ll_to_earth(lat, lng)) WHERE lat IS NOT NULL;

CREATE TABLE IF NOT EXISTS provider_specialties (
    provider_id    bigint NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    specialty_slug text   NOT NULL REFERENCES specialties(slug),
    PRIMARY KEY (provider_id, specialty_slug)
);
CREATE INDEX IF NOT EXISTS provider_specialties_slug_idx ON provider_specialties (specialty_slug);

-- ---------------------------------------------------------------------------
-- Availability signals: every "they took me" / "not accepting" observation.
-- Never updated in place; status is derived (see provider_status below).
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE availability_status AS ENUM ('accepting', 'not_accepting', 'waitlist');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE signal_source AS ENUM ('clinic', 'region', 'user', 'web_crawl');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS availability_signals (
    id            bigserial PRIMARY KEY,
    provider_id   bigint NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    source        signal_source NOT NULL,
    status        availability_status NOT NULL,
    scope         text NOT NULL DEFAULT 'all' CHECK (scope IN ('adults', 'children', 'all')),
    observed_at   timestamptz NOT NULL DEFAULT now(),
    note          text CHECK (length(note) <= 500),
    -- Salted hash of IP/email for rate limiting & dedup. Never store raw values.
    reporter_hash text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS availability_signals_provider_idx
    ON availability_signals (provider_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS availability_signals_reporter_idx
    ON availability_signals (reporter_hash, created_at) WHERE reporter_hash IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Alert subscriptions ("email me when a dentist near Praha 6 opens up").
-- Store only what the alert needs: no names, no health details.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS subscriptions (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email            text NOT NULL,
    specialty_slug   text NOT NULL REFERENCES specialties(slug),
    lat              double precision NOT NULL,
    lng              double precision NOT NULL,
    radius_km        integer NOT NULL DEFAULT 10 CHECK (radius_km BETWEEN 1 AND 100),
    verify_token     text NOT NULL,
    verified_at      timestamptz,             -- double opt-in
    unsubscribed_at  timestamptz,
    last_notified_at timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS subscriptions_active_idx
    ON subscriptions (specialty_slug) WHERE verified_at IS NOT NULL AND unsubscribed_at IS NULL;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS place_label text;     -- "Praha 6", shown in emails
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS requester_hash text;  -- salted IP hash, for rate limits
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS confirm_sends smallint NOT NULL DEFAULT 1;  -- confirmation emails sent
CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_token_idx ON subscriptions (verify_token);
CREATE INDEX IF NOT EXISTS subscriptions_email_idx ON subscriptions (lower(email));

-- Which practice a subscriber has already been told about (so nobody gets the same news twice).
CREATE TABLE IF NOT EXISTS subscription_notifications (
    subscription_id uuid   NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    provider_id     bigint NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    sent_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (subscription_id, provider_id)
);

-- Every email we send, so the web app and the alert job share one daily limit
-- (Wedos allows 500 a day). Nothing personal is stored here.
CREATE TABLE IF NOT EXISTS mail_log (
    id      bigserial PRIMARY KEY,
    kind    text NOT NULL CHECK (kind IN ('confirm', 'alert')),
    sent_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS mail_log_sent_idx ON mail_log (sent_at);

-- ---------------------------------------------------------------------------
-- Derived status.
--
-- Each signal gets weight = source_weight * 0.5^(age_days / 30), so a report
-- loses half its weight every 30 days and is ignored after 90. The score is
-- the weighted mean of +1 (accepting), +0.3 (waitlist), -1 (not accepting).
-- Too little total weight => 'unknown' rather than a confident guess.
-- Exception: the clinic's own update from the last 30 days is authoritative.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION signal_source_weight(s signal_source) RETURNS double precision
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE s
        WHEN 'clinic'    THEN 1.0
        WHEN 'region'    THEN 0.8
        WHEN 'user'      THEN 0.6
        WHEN 'web_crawl' THEN 0.4
    END
$$;

CREATE OR REPLACE VIEW provider_status AS
WITH weighted AS (
    SELECT provider_id,
           observed_at,
           source,
           signal_source_weight(source)
             * power(0.5, extract(epoch FROM now() - observed_at) / 86400.0 / 30.0) AS w,
           CASE status WHEN 'accepting' THEN 1.0 WHEN 'waitlist' THEN 0.3 ELSE -1.0 END AS v
    FROM availability_signals
    WHERE observed_at > now() - interval '90 days'
      AND observed_at <= now() + interval '1 hour'
), agg AS (
    SELECT provider_id,
           sum(w)                AS confidence,
           sum(w * v) / sum(w)   AS score,
           max(observed_at)      AS last_signal_at,
           (array_agg(source ORDER BY observed_at DESC))[1] AS last_source
    FROM weighted
    GROUP BY provider_id
), clinic AS (
    SELECT DISTINCT ON (provider_id) provider_id, status
    FROM availability_signals
    WHERE source = 'clinic'
      AND observed_at > now() - interval '30 days'
      AND observed_at <= now() + interval '1 hour'
    ORDER BY provider_id, observed_at DESC
)
SELECT agg.provider_id,
       round(confidence::numeric, 3) AS confidence,
       round(score::numeric, 3)      AS score,
       last_signal_at,
       CASE
           WHEN clinic.status IS NOT NULL THEN clinic.status::text
           WHEN confidence < 0.3 THEN 'unknown'
           WHEN score >=  0.5    THEN 'accepting'
           WHEN score <= -0.5    THEN 'not_accepting'
           ELSE 'mixed'
       END AS status,
       last_source
FROM agg LEFT JOIN clinic USING (provider_id);

-- Distance in km between two points (earthdistance returns metres).
CREATE OR REPLACE FUNCTION distance_km(lat1 float8, lng1 float8, lat2 float8, lng2 float8)
RETURNS float8 LANGUAGE sql IMMUTABLE AS $$
    SELECT earth_distance(ll_to_earth(lat1, lng1), ll_to_earth(lat2, lng2)) / 1000.0
$$;

-- ---------------------------------------------------------------------------
-- Sponsored listings sold directly to clinics. First-party, no cookies or
-- tracking, so they need no consent banner. Always labelled "Reklama".
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sponsored_listings (
    id             bigserial PRIMARY KEY,
    provider_id    bigint NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    specialty_slug text   NOT NULL REFERENCES specialties(slug),
    city_slug      text   NOT NULL,
    tagline        text   CHECK (length(tagline) <= 140),
    starts_on      date   NOT NULL DEFAULT current_date,
    ends_on        date   NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_on >= starts_on)
);
CREATE INDEX IF NOT EXISTS sponsored_listings_page_idx
    ON sponsored_listings (specialty_slug, city_slug, ends_on);
