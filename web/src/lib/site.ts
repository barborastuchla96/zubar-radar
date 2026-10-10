// Copy, labels and helpers. Everything user-facing is Czech.

export const SITE_NAME = 'Přijímá nové pacienty?';
export const TAGLINE = 'Zubaři a lékaři s volnou kapacitou';

/** Launch city: featured on the homepage. */
export const FEATURED_CITY = { slug: 'praha', name: 'Praha' };

/**
 * The register files Prague practices under districts ("Praha 6" -> praha-6),
 * plus a few under plain "Praha". /<specialty>/praha is a hub over all of them.
 */
export const isPragueSlug = (slug: string) => slug === 'praha' || /^praha-\d+$/.test(slug);
/** Czech alphabetical order with numbers compared as numbers: Brno, Čáslav…; Praha 2 before Praha 10. */
export const placeCompare = new Intl.Collator('cs', { numeric: true, sensitivity: 'base' }).compare;
export const byDistrict = (a: { city: string }, b: { city: string }) => placeCompare(a.city, b.city);

export interface SpecialtyInfo {
  slug: string;
  singular: string;     // "Zubař"
  plural: string;       // "Zubaři"
  short: string;        // nav label
  acc: string;          // 4th case, after "hledáte": "zubaře"
  nearby: string;       // "Další zubaři v okolí"
  some: string;         // "některý zubař" (… začne přijímat)
  seo?: string;         // name used in page titles when people also search another word
  icon: string;
  specialist?: boolean; // shown in its own group; many want a referral
}

export const SPECIALTIES: SpecialtyInfo[] = [
  { slug: 'zubar', singular: 'Zubař', plural: 'Zubaři', short: 'Zubaři', acc: 'zubaře', nearby: 'Další zubaři v okolí', some: 'některý zubař', icon: '🦷' },
  { slug: 'praktik', singular: 'Praktický lékař', plural: 'Praktičtí lékaři', short: 'Praktičtí lékaři', acc: 'praktického lékaře', nearby: 'Další praktičtí lékaři v okolí', some: 'některý praktický lékař', icon: '🩺' },
  { slug: 'pediatr', singular: 'Dětský lékař', plural: 'Dětští lékaři', short: 'Dětští lékaři', acc: 'dětského lékaře', nearby: 'Další dětští lékaři v okolí', some: 'některý dětský lékař', seo: 'Dětští lékaři (pediatři)', icon: '🧸' },
  { slug: 'gynekolog', singular: 'Gynekolog', plural: 'Gynekologové', short: 'Gynekologové', acc: 'gynekologa', nearby: 'Další gynekologové v okolí', some: 'některý gynekolog', icon: '🌸' },
  { slug: 'hygienistka', singular: 'Dentální hygiena', plural: 'Dentální hygiena', short: 'Dentální hygiena', acc: 'dentální hygienu', nearby: 'Další dentální hygiena v okolí', some: 'některá ordinace dentální hygieny', icon: '✨' },
  { slug: 'ocni', singular: 'Oční lékař', plural: 'Oční lékaři', short: 'Oční', acc: 'očního lékaře', nearby: 'Další oční lékaři v okolí', some: 'některý oční lékař', seo: 'Oční lékaři (oftalmologové)', icon: '👁️', specialist: true },
  { slug: 'orl', singular: 'ORL lékař', plural: 'ORL lékaři', short: 'ORL', acc: 'ORL lékaře', nearby: 'Další ORL lékaři v okolí', some: 'některý ORL lékař', seo: 'ORL lékaři (ušní, nosní, krční)', icon: '👂', specialist: true },
  { slug: 'kozni', singular: 'Kožní lékař', plural: 'Kožní lékaři', short: 'Kožní', acc: 'kožního lékaře', nearby: 'Další kožní lékaři v okolí', some: 'některý kožní lékař', seo: 'Kožní lékaři (dermatologové)', icon: '🩹', specialist: true },
  { slug: 'psychiatr', singular: 'Psychiatr', plural: 'Psychiatři', short: 'Psychiatři', acc: 'psychiatra', nearby: 'Další psychiatři v okolí', some: 'některý psychiatr', icon: '💬', specialist: true },
  { slug: 'neurolog', singular: 'Neurolog', plural: 'Neurologové', short: 'Neurologové', acc: 'neurologa', nearby: 'Další neurologové v okolí', some: 'některý neurolog', icon: '🧠', specialist: true },
  { slug: 'urolog', singular: 'Urolog', plural: 'Urologové', short: 'Urologové', acc: 'urologa', nearby: 'Další urologové v okolí', some: 'některý urolog', icon: '💧', specialist: true },
  { slug: 'chirurg', singular: 'Chirurg', plural: 'Chirurgové', short: 'Chirurgové', acc: 'chirurga', nearby: 'Další chirurgové v okolí', some: 'některý chirurg', seo: 'Chirurgové (chirurgické ambulance)', icon: '⚕️', specialist: true },
  { slug: 'ortoped', singular: 'Ortoped', plural: 'Ortopedové', short: 'Ortopedové', acc: 'ortopeda', nearby: 'Další ortopedové v okolí', some: 'některý ortoped', icon: '🦴', specialist: true },
];
export const MAIN_SPECIALTIES = SPECIALTIES.filter((s) => !s.specialist);
export const SPECIALISTS = SPECIALTIES.filter((s) => s.specialist);

/** Lower-case for mid-sentence use, keeping abbreviations: "ORL lékaři" -> "ORL lékaři", "Zubaři" -> "zubaři". */
export const lowerName = (s: string) => s.replace(/\p{L}+/gu, (w) => (w.length > 1 && w === w.toUpperCase() ? w : w.toLowerCase()));

/** Czech health insurers by code, as practices and patients know them. */
export const INSURERS: Record<string, string> = { '111': 'VZP', '201': 'VoZP', '205': 'ČPZP', '207': 'OZP', '209': 'ZPŠ', '211': 'ZP MV', '213': 'RBP' };
/** "VZP · OZP · ZP MV", or "všechny pojišťovny" when it is all seven */
export const insurersShort = (codes: string[] | null, en = false) =>
  !codes?.length ? null : codes.length === Object.keys(INSURERS).length ? (en ? 'all health insurers' : 'všechny pojišťovny') : codes.map((c) => INSURERS[c] ?? c).join(' · ');

export const specialty = (slug: string) => SPECIALTIES.find((s) => s.slug === slug);

// Czech plural forms: 1 → one, 2–4 → few, 0 and 5+ → many.
const pr = new Intl.PluralRules('cs');
export function plural(n: number, one: string, few: string, many: string): string {
  const cat = pr.select(n);
  return cat === 'one' ? one : cat === 'few' ? few : many;
}
const num = (n: number) => n.toLocaleString('cs');
/** "1 ordinace", "3 ordinace", "331 ordinací" */
export const ordinaci = (n: number) => `${num(n)} ${plural(n, 'ordinace', 'ordinace', 'ordinací')}`;
/** "1 přijímá", "3 přijímají", "12 přijímá" */
export const prijima = (n: number) => `${num(n)} ${plural(n, 'přijímá', 'přijímají', 'přijímá')}`;

// Every status has a symbol too, so it can be read without relying on colour.
export const STATUS_LABEL: Record<string, { label: string; hint: string; icon: string }> = {
  accepting:     { icon: '✓', label: 'Přijímá nové pacienty', hint: 'Podle nedávných zpráv přijímá nové pacienty.' },
  waitlist:      { icon: '⏳', label: 'Pořadník',              hint: 'Zapisuje nové pacienty do pořadníku.' },
  mixed:         { icon: '!', label: 'Nejasné',               hint: 'Zprávy si odporují.' },
  not_accepting: { icon: '✗', label: 'Nepřijímá',             hint: 'Podle nedávných zpráv nové pacienty nepřijímá.' },
  unknown:       { icon: '?', label: 'Zatím nevíme',          hint: 'Zatím o tom nemáme zprávy.' },
};

const rtf = new Intl.RelativeTimeFormat('cs', { numeric: 'auto' });

/** "dnes", "včera", "před 5 dny", "před 2 měsíci". */
export function ago(d: Date | string | null): string | null {
  if (!d) return null;
  const days = Math.round((new Date(d).getTime() - Date.now()) / 86_400_000);
  if (days > -1) return 'dnes';
  if (days > -45) return rtf.format(days, 'day');
  return rtf.format(Math.round(days / 30), 'month');
}

export function slugify(s: string): string {
  return s.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

/** "Benešov (okres Blansko)" for a town that shares its name with a bigger one elsewhere. */
export const placeLabel = (city: string, slug: string, district: string | null) =>
  district && slug !== slugify(city) ? `${city} (okres ${district})` : city;

export const providerUrl = (p: { id: number; name: string }) => `/lekar/${p.id}-${slugify(p.name)}`;
export const cityUrl = (spec: string, city: string) => `/${spec}/${city}`;
/** District (okres) page. */
export const districtUrl = (spec: string, okres: string) =>
  okres === 'praha' ? cityUrl(spec, 'praha') : `/${spec}/okres/${okres}`;
/** Region page; Prague is both a city and a region and already has its own page. */
export const regionUrl = (spec: string, kraj: string) =>
  kraj === 'hlavni-mesto-praha' ? cityUrl(spec, 'praha') : `/${spec}/kraj/${kraj}`;

export function address(p: { street: string | null; house_no: string | null; city: string | null; postcode?: string | null }) {
  const street = [p.street, p.house_no].filter(Boolean).join(' ');
  // Non-breaking spaces keep "120 00 Praha 2" together instead of "120 00 Praha / 2".
  const pc = p.postcode ? p.postcode.replace(/^(\d{3})(\d{2})$/, '$1\u00a0$2') : null;
  const city = p.city ? p.city.replace(/ (\d+)$/, '\u00a0$1') : null;
  return [street, [pc, city].filter(Boolean).join('\u00a0')].filter(Boolean).join(', ');
}

/** Normalise phone for tel: links ("+420 773 255 275" -> "+420773255275"). */
export const telHref = (phone: string) => 'tel:' + phone.replace(/[^\d+]/g, '');

/** Readable Czech number: "+420773255275" -> "773 255 275". Anything unusual is shown as given. */
export function formatPhone(phone: string): string {
  const d = phone.replace(/[^\d+]/g, '').replace(/^(\+|00)420/, '');
  return /^\d{9}$/.test(d) ? d.replace(/(\d{3})(\d{3})(\d{3})/, '$1 $2 $3') : phone.trim();
}

/** The register sometimes lists several addresses in one field: "a@x.cz,b@y.cz". */
export const emailsOf = (v: string | null) =>
  (v ?? '').split(/[\s,;]+/).map((e) => e.trim()).filter((e) => /^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(e));

/** Same for websites: "www.a.cz www.b.cz" → first usable link. */
export const firstWeb = (v: string | null) =>
  (v ?? '').split(/[\s,;]+/).map(webHref).find((u) => u != null) ?? null;

export function webHref(web: string): string | null {
  const w = web.trim();
  if (!w) return null;
  const url = /^https?:\/\//i.test(w) ? w : `https://${w}`;
  try { return new URL(url).toString(); } catch { return null; }
}

// Read at request time (not build time), so changing deploy/.env + restart is enough.
export type AdsProvider = 'none' | 'direct' | 'sklik';
export function adsProvider(): AdsProvider {
  const v = process.env.ADS_PROVIDER ?? 'none';
  return v === 'direct' || v === 'sklik' ? v : 'none';
}

/** Public contact address (CONTACT_EMAIL in deploy/.env). Hidden on the site until set. */
export function contactEmail(): string | null {
  const e = (process.env.CONTACT_EMAIL ?? '').trim();
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e) ? e : null;
}

/** Breadcrumb trail as schema.org JSON-LD (helps search engines show the path). */
// --- dated, quotable facts (search engines and AI assistants quote sentences like these) ---
const TZ = 'Europe/Prague';
/** "říjen 2026" */
export const monthYear = (d = new Date()) => d.toLocaleDateString('cs', { month: 'long', year: 'numeric', timeZone: TZ });
/** "9. října 2026" */
export const dayLong = (d = new Date()) => d.toLocaleDateString('cs', { day: 'numeric', month: 'long', year: 'numeric', timeZone: TZ });
/** "z 1 ordinace", "z 79 ordinací" (genitive after "z") */
const zOrdinaci = (n: number) => `z ${num(n)} ${n === 1 ? 'ordinace' : 'ordinací'}`;
/** "Brno, zubaři: k 9. října 2026 podle posledních zpráv přijímá nové pacienty 12 z 331 ordinací." */
export function factLine(place: string, specPlural: string, accepting: number, total: number, d = new Date()) {
  const head = `${place}, ${lowerName(specPlural)}: k ${dayLong(d)}`;
  return accepting > 0
    ? `${head} podle posledních zpráv ${plural(accepting, 'přijímá', 'přijímají', 'přijímá')} nové pacienty ${num(accepting)} ${zOrdinaci(total)}.`
    : total === 1
      ? `${head} zatím nemáme zprávu, že by jediná ordinace v seznamu přijímala nové pacienty.`
      : `${head} zatím nemáme zprávu, že by některá ${zOrdinaci(total)} přijímala nové pacienty.`;
}

export function breadcrumbLd(site: URL | undefined, items: [string, string][]) {
  return {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: items.map(([name, path], i) => ({
      '@type': 'ListItem', position: i + 1, name, item: new URL(path, site).toString(),
    })),
  };
}

/** Optional cookieless analytics (Umami-style script tag). */
export function analytics(): { src: string; id: string } | null {
  const src = process.env.ANALYTICS_SRC, id = process.env.ANALYTICS_ID;
  return src && id ? { src, id } : null;
}
