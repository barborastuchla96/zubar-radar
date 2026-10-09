import postgres from 'postgres';
import { placeLabel } from './site';

const url = process.env.DATABASE_URL;
if (!url) throw new Error('DATABASE_URL is not set');

export const sql = postgres(url, { max: 5, idle_timeout: 30 });

export type Status = 'accepting' | 'not_accepting' | 'waitlist' | 'mixed' | 'unknown';

export interface ProviderRow {
  id: number;
  name: string;
  street: string | null;
  house_no: string | null;
  city: string | null;
  city_slug: string | null;
  postcode: string | null;
  district: string | null;
  region: string | null;
  lat: number | null;
  lng: number | null;
  phone: string | null;
  email: string | null;
  web: string | null;
  facility_type: string | null;
  status: Status;
  last_signal_at: Date | null;
  last_source: 'clinic' | 'region' | 'user' | 'web_crawl' | null;
  self_pay: boolean;      // no contract with any health insurer
  english: boolean;       // someone there speaks English
  specialties: string[];
}

const STATUS_ORDER = sql`CASE coalesce(s.status, 'unknown')
  WHEN 'accepting' THEN 0 WHEN 'waitlist' THEN 1 WHEN 'mixed' THEN 2
  WHEN 'unknown' THEN 3 ELSE 4 END`;

const PROVIDER_COLS = sql`
  p.id, p.name, p.street, p.house_no, p.city, p.city_slug, p.postcode, p.district, p.region,
  p.lat, p.lng, p.phone, p.email, p.web, p.facility_type,
  coalesce(s.status, 'unknown') AS status, s.last_signal_at, s.last_source,
  EXISTS (SELECT 1 FROM provider_flags f WHERE f.provider_id = p.id AND f.flag = 'self_pay'
           AND (f.source <> 'user' OR f.observed_at > now() - interval '1 year')) AS self_pay,
  EXISTS (SELECT 1 FROM provider_flags f WHERE f.provider_id = p.id AND f.flag = 'english'
           AND (f.source <> 'user' OR f.observed_at > now() - interval '1 year')) AS english,
  (SELECT array_agg(specialty_slug ORDER BY specialty_slug)
     FROM provider_specialties WHERE provider_id = p.id) AS specialties`;

export async function specialtyExists(slug: string): Promise<boolean> {
  const r = await sql`SELECT 1 FROM specialties WHERE slug = ${slug}`;
  return r.length > 0;
}

/** Providers of one specialty in one city (or Prague district), best status first. */
export async function cityProviders(specialty: string, citySlug: string): Promise<ProviderRow[]> {
  return sql<ProviderRow[]>`
    SELECT ${PROVIDER_COLS}
      FROM providers p
      JOIN provider_specialties ps ON ps.provider_id = p.id AND ps.specialty_slug = ${specialty}
      LEFT JOIN provider_status s ON s.provider_id = p.id
     WHERE p.active AND p.city_slug = ${citySlug}
     ORDER BY ${STATUS_ORDER}, self_pay, s.last_signal_at DESC NULLS LAST, p.name`;
}

export async function provider(id: number): Promise<ProviderRow | undefined> {
  const [row] = await sql<ProviderRow[]>`
    SELECT ${PROVIDER_COLS}
      FROM providers p LEFT JOIN provider_status s ON s.provider_id = p.id
     WHERE p.id = ${id} AND p.active`;
  return row;
}

export async function nearby(
  specialty: string, lat: number, lng: number, km: number, limit = 30,
): Promise<(ProviderRow & { km: number })[]> {
  const m = km * 1000;
  return sql<(ProviderRow & { km: number })[]>`
    SELECT ${PROVIDER_COLS},
           round((earth_distance(ll_to_earth(${lat}, ${lng}), ll_to_earth(p.lat, p.lng)) / 1000)::numeric, 1)::float AS km
      FROM providers p
      JOIN provider_specialties ps ON ps.provider_id = p.id AND ps.specialty_slug = ${specialty}
      LEFT JOIN provider_status s ON s.provider_id = p.id
     WHERE p.active AND p.lat IS NOT NULL
       AND earth_box(ll_to_earth(${lat}, ${lng}), ${m}) @> ll_to_earth(p.lat, p.lng)
       AND earth_distance(ll_to_earth(${lat}, ${lng}), ll_to_earth(p.lat, p.lng)) <= ${m}
     ORDER BY ${STATUS_ORDER}, km
     LIMIT ${limit}`;
}

/** Other providers of the same specialty close to a given one (for the provider page). */
export async function alternatives(p: ProviderRow, specialty: string, limit = 5) {
  if (p.lat == null || p.lng == null) return [];
  const rows = await nearby(specialty, p.lat, p.lng, 5, limit + 1);
  return rows.filter((r) => r.id !== p.id).slice(0, limit);
}

export interface CityCount {
  city_slug: string; city: string; region: string | null; district: string | null;
  n: number; accepting: number; lat: number | null; lng: number | null;
}

const labelled = (rows: CityCount[]): CityCount[] =>
  rows.map((c) => ({ ...c, city: placeLabel(c.city, c.city_slug, c.district) }));

/** Cities ranked by number of providers for a specialty. */
export async function cities(specialty: string, minProviders = 1, limit = 10000): Promise<CityCount[]> {
  return labelled(await sql<CityCount[]>`
    SELECT p.city_slug, min(p.city) AS city, min(p.region) AS region, min(p.district) AS district, count(*)::int AS n,
           avg(p.lat) AS lat, avg(p.lng) AS lng,
           count(*) FILTER (WHERE s.status = 'accepting')::int AS accepting
      FROM providers p
      JOIN provider_specialties ps ON ps.provider_id = p.id AND ps.specialty_slug = ${specialty}
      LEFT JOIN provider_status s ON s.provider_id = p.id
     WHERE p.active AND p.city_slug IS NOT NULL
     GROUP BY p.city_slug
    HAVING count(*) >= ${minProviders}
     ORDER BY count(*) DESC, min(p.city)
     LIMIT ${limit}`);
}

export interface ReportInput {
  providerId: number;
  status: 'accepting' | 'not_accepting' | 'waitlist';
  scope: 'adults' | 'children' | 'all';
  observedAt: Date;
  note: string | null;
  selfPay?: boolean;      // "no contract with insurers, everything is paid"
  english?: boolean;      // "they speak English"
  reporterHash: string;
}

export type ReportResult = 'ok' | 'duplicate' | 'too_many' | 'no_provider';

/** Insert a user report, with per-provider and per-day limits per reporter. */
export async function addReport(r: ReportInput): Promise<ReportResult> {
  return sql.begin(async (tx) => {
    // Serialize reports from the same reporter so the limits can't be raced.
    await tx`SELECT pg_advisory_xact_lock(hashtext(${r.reporterHash}))`;
    const [exists] = await tx`SELECT 1 FROM providers WHERE id = ${r.providerId} AND active`;
    if (!exists) return 'no_provider';
    const [{ same, today }] = await tx`
      SELECT count(*) FILTER (WHERE provider_id = ${r.providerId})::int AS same,
             count(*)::int AS today
        FROM availability_signals
       WHERE reporter_hash = ${r.reporterHash} AND created_at > now() - interval '24 hours'`;
    if (same > 0) return 'duplicate';
    if (today >= 10) return 'too_many';
    await tx`
      INSERT INTO availability_signals (provider_id, source, status, scope, observed_at, note, reporter_hash)
      VALUES (${r.providerId}, 'user', ${r.status}, ${r.scope}, ${r.observedAt}, ${r.note}, ${r.reporterHash})`;
    for (const [flag, on] of [['self_pay', r.selfPay], ['english', r.english]] as const) {
      if (!on) continue;
      await tx`
        INSERT INTO provider_flags (provider_id, flag, source, observed_at)
        VALUES (${r.providerId}, ${flag}, 'user', ${r.observedAt})
        ON CONFLICT (provider_id, flag, source) DO UPDATE SET observed_at = greatest(provider_flags.observed_at, EXCLUDED.observed_at)`;
    }
    return 'ok';
  });
}

export async function sitemapEntries() {
  return sql<{ kind: 'city' | 'provider'; specialty: string | null; slug: string; name: string }[]>`
    SELECT 'city' AS kind, ps.specialty_slug AS specialty, p.city_slug AS slug, '' AS name
      FROM providers p JOIN provider_specialties ps ON ps.provider_id = p.id
     WHERE p.active AND p.city_slug IS NOT NULL
     GROUP BY ps.specialty_slug, p.city_slug HAVING count(*) >= 3
    UNION ALL
    SELECT 'provider', NULL, p.id::text, p.name FROM providers p WHERE p.active`;
}

export interface SponsoredRow extends ProviderRow { tagline: string | null }

/** Active directly-sold sponsored listings for one specialty + city page. */
export async function sponsored(specialty: string, citySlug: string, limit = 2): Promise<SponsoredRow[]> {
  return sql<SponsoredRow[]>`
    SELECT ${PROVIDER_COLS}, sl.tagline
      FROM sponsored_listings sl
      JOIN providers p ON p.id = sl.provider_id AND p.active
      LEFT JOIN provider_status s ON s.provider_id = p.id
     WHERE sl.specialty_slug = ${specialty} AND sl.city_slug = ${citySlug}
       AND current_date BETWEEN sl.starts_on AND sl.ends_on
     ORDER BY random()
     LIMIT ${limit}`;
}

const PRAGUE_SLUG = '^praha(-[0-9]+)?$';

/** Practices in all of Prague that are (or may be) taking patients, freshest first. */
export async function pragueAccepting(specialty: string, limit = 24): Promise<ProviderRow[]> {
  return sql<ProviderRow[]>`
    SELECT ${PROVIDER_COLS}
      FROM providers p
      JOIN provider_specialties ps ON ps.provider_id = p.id AND ps.specialty_slug = ${specialty}
      JOIN provider_status s ON s.provider_id = p.id AND s.status IN ('accepting', 'waitlist')
     WHERE p.active AND p.city_slug ~ ${PRAGUE_SLUG}
     ORDER BY ${STATUS_ORDER}, s.last_signal_at DESC
     LIMIT ${limit}`;
}

/** Practices in one region (kraj) that are (or may be) taking patients, freshest first. */
export async function regionAccepting(specialty: string, region: string, limit = 12): Promise<ProviderRow[]> {
  return sql<ProviderRow[]>`
    SELECT ${PROVIDER_COLS}
      FROM providers p
      JOIN provider_specialties ps ON ps.provider_id = p.id AND ps.specialty_slug = ${specialty}
      JOIN provider_status s ON s.provider_id = p.id AND s.status IN ('accepting', 'waitlist')
     WHERE p.active AND p.region = ${region}
     ORDER BY ${STATUS_ORDER}, s.last_signal_at DESC
     LIMIT ${limit}`;
}

export interface AvailabilityRow { region: string | null; district: string | null; status: Status; n: number }

/** Practices per district and status, for the availability overview. */
export async function availability(specialty: string): Promise<AvailabilityRow[]> {
  return sql<AvailabilityRow[]>`
    SELECT p.region, p.district, coalesce(s.status, 'unknown') AS status, count(*)::int AS n
      FROM providers p
      JOIN provider_specialties ps ON ps.provider_id = p.id AND ps.specialty_slug = ${specialty}
      LEFT JOIN provider_status s ON s.provider_id = p.id
     WHERE p.active
     GROUP BY 1, 2, 3`;
}

/** The latest note from the website checker, e.g. „přijímáme nové pacienty“ — URL. */
export async function latestCrawlNote(providerId: number): Promise<{ note: string; observed_at: Date } | undefined> {
  const [row] = await sql<{ note: string; observed_at: Date }[]>`
    SELECT note, observed_at FROM availability_signals
     WHERE provider_id = ${providerId} AND source = 'web_crawl' AND note IS NOT NULL
     ORDER BY observed_at DESC LIMIT 1`;
  return row;
}
