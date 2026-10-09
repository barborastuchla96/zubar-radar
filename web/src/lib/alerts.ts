import { randomBytes } from 'node:crypto';
import { sql } from './db';
import { formTokenOk } from './report';
import { specialty } from './site';
import { aDoctorEn } from './i18n';

export const RADII = [2, 5, 10, 20] as const;
const DAILY_LIMIT = Number(process.env.MAIL_DAILY_LIMIT ?? 450);   // shared with `radar alerts`
const PER_REQUESTER_DAY = 5;     // sign-ups per visitor per 24 h
const PER_EMAIL_DAY = 3;         // confirmation emails to one address per 24 h
const MAX_ACTIVE_PER_EMAIL = 10;
const MAX_RESENDS = 3;           // confirmation emails per sign-up, at least 10 minutes apart

export interface SignupInput { email: string; specialty: string; lat: number; lng: number; radiusKm: number; place: string; lang: 'cs' | 'en' }
export type SignupParsed = { ok: true; value: SignupInput; back: string } | { ok: false; reason: 'invalid' | 'bot'; back: string };

const EMAIL_RE = /^[^\s@<>()",;:]+@[^\s@<>()",;:]+\.[a-z]{2,}$/i;

/** Only same-site paths, never "//evil.example". */
export const safeBack = (v: string) => (/^\/(?![/\\])[\w\-./%]*$/.test(v) ? v : '/');

export function parseSignup(form: FormData, now = Date.now()): SignupParsed {
  const get = (k: string) => (form.get(k) ?? '').toString().trim();
  const back = safeBack(get('back'));
  if (get('web') !== '' || !formTokenOk(get('t'), now)) return { ok: false, reason: 'bot', back };
  const email = get('email').toLowerCase();
  const lat = Number(get('lat')), lng = Number(get('lng')), radiusKm = Number(get('radius'));
  const spec = specialty(get('s'));
  const place = get('place').replace(/\s+/g, ' ').slice(0, 80);
  // Rough box around Czechia: the alert job only knows Czech practices anyway.
  const inCz = lat > 48.4 && lat < 51.2 && lng > 12 && lng < 19;
  if (!spec || email.length > 254 || !EMAIL_RE.test(email) || !inCz || !place
      || !(RADII as readonly number[]).includes(radiusKm)) {
    return { ok: false, reason: 'invalid', back };
  }
  const lang = get('lang') === 'en' ? 'en' : 'cs';
  return { ok: true, value: { email, specialty: spec.slug, lat, lng, radiusKm, place, lang }, back };
}

export type SignupResult =
  | { kind: 'send'; token: string }   // new or still unconfirmed: send the confirmation email
  | { kind: 'already' }               // confirmed before, or a confirmation went out just now: nothing to send
  | { kind: 'too_many' };

export async function createSignup(v: SignupInput, requesterHash: string): Promise<SignupResult> {
  return sql.begin(async (tx) => {
    // Serialise sign-ups for one address so the limits below can't be raced.
    await tx`SELECT pg_advisory_xact_lock(hashtext(${v.email}))`;
    const [lim] = await tx`
      SELECT (SELECT count(*) FROM subscriptions WHERE requester_hash = ${requesterHash}
                 AND created_at > now() - interval '24 hours')::int AS by_requester,
             (SELECT count(*) FROM subscriptions WHERE lower(email) = ${v.email}
                 AND created_at > now() - interval '24 hours')::int AS by_email,
             (SELECT count(*) FROM subscriptions WHERE lower(email) = ${v.email}
                 AND unsubscribed_at IS NULL)::int AS active,
             (SELECT count(*) FROM mail_log WHERE sent_at > now() - interval '24 hours')::int AS mails`;
    const [same] = await tx`
      SELECT verify_token, verified_at, confirm_sends,
             created_at > now() - interval '10 minutes' AS recent
        FROM subscriptions
       WHERE lower(email) = ${v.email} AND specialty_slug = ${v.specialty} AND unsubscribed_at IS NULL
         AND radius_km = ${v.radiusKm}
         AND round(lat::numeric, 3) = round(${v.lat}::numeric, 3) AND round(lng::numeric, 3) = round(${v.lng}::numeric, 3)
       LIMIT 1`;
    if (same?.verified_at) return { kind: 'already' } as const;
    if (lim.by_requester >= PER_REQUESTER_DAY || lim.by_email >= PER_EMAIL_DAY
        || lim.active >= MAX_ACTIVE_PER_EMAIL || lim.mails >= DAILY_LIMIT) return { kind: 'too_many' } as const;
    if (same) {
      // Someone asked again before confirming: resend, but rarely, so nobody can flood an inbox.
      if (same.recent || same.confirm_sends >= MAX_RESENDS) return { kind: 'already' } as const;
      await tx`UPDATE subscriptions SET created_at = now(), confirm_sends = confirm_sends + 1
                WHERE verify_token = ${same.verify_token}`;
      return { kind: 'send', token: same.verify_token } as const;
    }
    const token = randomBytes(24).toString('base64url');
    await tx`
      INSERT INTO subscriptions (email, specialty_slug, lat, lng, radius_km, verify_token, place_label, requester_hash, lang)
      VALUES (${v.email}, ${v.specialty}, ${v.lat}, ${v.lng}, ${v.radiusKm}, ${token}, ${v.place}, ${requesterHash}, ${v.lang})`;
    return { kind: 'send', token } as const;
  });
}

/** The confirmation email didn't go out: don't count it, so trying again right away works. */
export const sendFailed = (token: string) => sql`
  UPDATE subscriptions SET confirm_sends = greatest(confirm_sends - 1, 0), created_at = now() - interval '11 minutes'
   WHERE verify_token = ${token} AND verified_at IS NULL`;

export const logMail = (kind: 'confirm' | 'alert') => sql`INSERT INTO mail_log (kind) VALUES (${kind})`;

export interface SubscriptionRow { specialty_slug: string; place_label: string | null; radius_km: number; verified_at: Date | null; lang: 'cs' | 'en' }

export async function findByToken(token: string): Promise<SubscriptionRow | undefined> {
  if (!/^[\w-]{20,64}$/.test(token)) return undefined;
  const [row] = await sql<SubscriptionRow[]>`
    SELECT specialty_slug, place_label, radius_km, verified_at, lang FROM subscriptions
     WHERE verify_token = ${token} AND unsubscribed_at IS NULL`;
  return row;
}

export async function confirm(token: string): Promise<boolean> {
  const r = await sql`UPDATE subscriptions SET verified_at = coalesce(verified_at, now())
                       WHERE verify_token = ${token} AND unsubscribed_at IS NULL`;
  return r.count > 0;
}

/** Unsubscribing deletes the row: we have no reason to keep the address. */
export async function unsubscribe(token: string): Promise<void> {
  await sql`DELETE FROM subscriptions WHERE verify_token = ${token}`;
}

export const confirmationSubject = (lang: 'cs' | 'en') =>
  lang === 'en' ? 'Please confirm your alert for practices accepting new patients' : 'Přihlášení k odběru e-mailových upozornění - prijimanovepacienty.cz';

/** "…až některá {FROM_PRACTICES} ve vzdálenosti do 5 km…" */
const FROM_PRACTICES: Record<string, string> = {
  zubar: 'ze zubařských ordinací', praktik: 'z ordinací praktického lékaře', pediatr: 'z ordinací dětského lékaře',
  gynekolog: 'z ordinací gynekologa', hygienistka: 'z ordinací dentální hygieny', ocni: 'z ordinací očního lékaře',
  orl: 'z ordinací ORL lékaře', kozni: 'z ordinací kožního lékaře', psychiatr: 'z ordinací psychiatra', neurolog: 'z ordinací neurologa',
};

export function confirmationText(siteUrl: string, token: string, v: Pick<SignupInput, 'specialty' | 'place' | 'radiusKm'> & { lang?: 'cs' | 'en' }) {
  const spec = specialty(v.specialty)!;
  if (v.lang === 'en') {
    const link = `${siteUrl.replace(/\/$/, '')}/en/alerts/confirm?t=${token}`;
    return [
      'Hello,',
      '',
      `would you like us to e-mail you when ${aDoctorEn(v.specialty)} within ${v.radiusKm} km of ${v.place} starts accepting new patients?`,
      '',
      'If so, please confirm here:',
      link,
      '',
      "If you didn't ask for this, just ignore this e-mail. Without confirmation we won't send anything else and will delete the address within a week.",
      '',
      'Best regards',
      'Accepting new patients? (prijimanovepacienty.cz)',
    ].join('\n');
  }
  const link = `${siteUrl.replace(/\/$/, '')}/upozorneni/potvrdit?t=${token}`;
  return [
    'Dobrý den,',
    '',
    `chcete, abychom vám dali vědět, až některá ${FROM_PRACTICES[v.specialty] ?? spec.some} ve vzdálenosti do ${v.radiusKm} km od místa ${v.place} začne přijímat nové pacienty?`,
    '',
    `Pokud ano, potvrďte to prosím na této adrese: ${link}`,
    '',
    'Pokud jste o nic nežádali, e-mail klidně ignorujte. Bez potvrzení vám už nic nepošleme a vaši e-mailovou adresu do týdne smažeme.',
    '',
    'Hezký den',
    'Barbora z Přijímá nové pacienty?',
    '(prijimanovepacienty.cz)',
  ].join('\n');
}
