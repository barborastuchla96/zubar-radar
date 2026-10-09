import { createHash, createHmac, timingSafeEqual } from 'node:crypto';
import type { ReportInput } from './db';

const SALT = process.env.REPORT_SALT ?? '';
if (!SALT && import.meta.env.PROD) throw new Error('REPORT_SALT must be set in production');
const KEY = SALT || 'dev-only-salt';

const MIN_FILL_MS = 3_000;            // faster than this = bot
const MAX_FILL_MS = 6 * 3_600_000;    // stale form
const MAX_AGE_DAYS = 60;              // reports about visits older than this aren't useful

const sign = (v: string) => createHmac('sha256', KEY).update(v).digest('base64url').slice(0, 22);

/** Token embedded in the form: render time + HMAC, so it can't be forged. */
export function formToken(now = Date.now()): string {
  const t = String(now);
  return `${t}.${sign(t)}`;
}

export function formTokenOk(token: string, now = Date.now()): boolean {
  const [t, sig] = token.split('.');
  if (!t || !sig) return false;
  const want = Buffer.from(sign(t));
  const got = Buffer.from(sig);
  if (want.length !== got.length || !timingSafeEqual(want, got)) return false;
  const age = now - Number(t);
  return age >= MIN_FILL_MS && age <= MAX_FILL_MS;
}

/** Salted hash of the client address. Raw IPs are never stored. */
export const reporterHash = (ip: string) => createHash('sha256').update(`${KEY}|${ip}`).digest('hex').slice(0, 32);

export function clientIp(request: Request, fallback: string): string {
  if (process.env.TRUST_PROXY === '1') {
    const xff = request.headers.get('x-forwarded-for');
    if (xff) return xff.split(',')[0].trim();
  }
  return fallback;
}

export type Parsed = { ok: true; value: Omit<ReportInput, 'reporterHash'> } | { ok: false; reason: 'invalid' | 'bot' };

const STATUSES = new Set(['accepting', 'not_accepting', 'waitlist']);
const SCOPES = new Set(['adults', 'children', 'all']);

export function parseReport(form: FormData, now = new Date()): Parsed {
  const get = (k: string) => (form.get(k) ?? '').toString().trim();
  if (get('web') !== '') return { ok: false, reason: 'bot' };          // honeypot
  if (!formTokenOk(get('t'), now.getTime())) return { ok: false, reason: 'bot' };

  const providerId = Number(get('provider_id'));
  const status = get('status');
  const scope = get('scope') || 'all';
  const date = get('date');
  if (!Number.isInteger(providerId) || providerId <= 0) return { ok: false, reason: 'invalid' };
  if (!STATUSES.has(status) || !SCOPES.has(scope)) return { ok: false, reason: 'invalid' };
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return { ok: false, reason: 'invalid' };

  // Interpret the date as noon local-ish time so timezones can't push it into tomorrow.
  const observedAt = new Date(`${date}T12:00:00Z`);
  const ageDays = (now.getTime() - observedAt.getTime()) / 86_400_000;
  if (Number.isNaN(ageDays) || ageDays < -1 || ageDays > MAX_AGE_DAYS) return { ok: false, reason: 'invalid' };
  const clamped = observedAt > now ? now : observedAt;

  const note = get('note').replace(/\s+/g, ' ').slice(0, 300) || null;
  return {
    ok: true,
    value: {
      providerId, status: status as ReportInput['status'], scope: scope as ReportInput['scope'],
      observedAt: clamped, note, selfPay: get('self_pay') === '1', english: get('english') === '1',
    },
  };
}
