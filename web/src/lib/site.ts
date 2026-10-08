// Copy, labels and helpers. Everything user-facing is Czech.

export const SITE_NAME = 'Bere pacienty?';
export const TAGLINE = 'Kteří lékaři a zubaři právě přijímají nové pacienty';

/** Launch city: featured on the homepage. */
export const FEATURED_CITY = { slug: 'praha', name: 'Praha' };

/**
 * The register files Prague practices under districts ("Praha 6" -> praha-6),
 * plus a few under plain "Praha". /<specialty>/praha is a hub over all of them.
 */
export const isPragueSlug = (slug: string) => slug === 'praha' || /^praha-\d+$/.test(slug);
const districtNo = (slug: string) => Number(slug.split('-')[1] ?? 0);
export const byDistrict = (a: { city_slug: string }, b: { city_slug: string }) =>
  districtNo(a.city_slug) - districtNo(b.city_slug);

export interface SpecialtyInfo {
  slug: string;
  singular: string;     // "Zubař"
  plural: string;       // "Zubaři"
  short: string;        // nav label
  icon: string;
}

export const SPECIALTIES: SpecialtyInfo[] = [
  { slug: 'zubar', singular: 'Zubař', plural: 'Zubaři', short: 'Zubař', icon: '🦷' },
  { slug: 'praktik', singular: 'Praktický lékař', plural: 'Praktičtí lékaři', short: 'Praktik', icon: '🩺' },
  { slug: 'pediatr', singular: 'Dětský lékař', plural: 'Dětští lékaři', short: 'Pediatr', icon: '🧸' },
  { slug: 'gynekolog', singular: 'Gynekolog', plural: 'Gynekologové', short: 'Gynekolog', icon: '🌸' },
  { slug: 'hygienistka', singular: 'Dentální hygiena', plural: 'Dentální hygiena', short: 'Hygiena', icon: '✨' },
];

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
/** "1 bere", "3 berou", "12 bere" */
export const bere = (n: number) => `${num(n)} ${plural(n, 'bere', 'berou', 'bere')}`;
/** "1 přijímá", "3 přijímají", "12 přijímá" */
export const prijima = (n: number) => `${num(n)} ${plural(n, 'přijímá', 'přijímají', 'přijímá')}`;

export const STATUS_LABEL: Record<string, { label: string; hint: string }> = {
  accepting:     { label: 'Bere nové pacienty', hint: 'Podle posledních hlášení přijímá.' },
  waitlist:      { label: 'Pořadník',           hint: 'Zapisuje do pořadníku.' },
  mixed:         { label: 'Nejasné',            hint: 'Hlášení si odporují.' },
  not_accepting: { label: 'Nebere',             hint: 'Podle posledních hlášení nepřijímá.' },
  unknown:       { label: 'Nevíme',             hint: 'Zatím nikdo nenahlásil. Víte víc?' },
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

export const providerUrl = (p: { id: number; name: string }) => `/lekar/${p.id}-${slugify(p.name)}`;
export const cityUrl = (spec: string, city: string) => `/${spec}/${city}`;

export function address(p: { street: string | null; house_no: string | null; city: string | null; postcode?: string | null }) {
  const street = [p.street, p.house_no].filter(Boolean).join(' ');
  const pc = p.postcode ? p.postcode.replace(/^(\d{3})(\d{2})$/, '$1 $2') : null;
  return [street, [pc, p.city].filter(Boolean).join(' ')].filter(Boolean).join(', ');
}

/** Normalise phone for tel: links ("+420 773 255 275" -> "+420773255275"). */
export const telHref = (phone: string) => 'tel:' + phone.replace(/[^\d+]/g, '');

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

/** Optional cookieless analytics (Umami-style script tag). */
export function analytics(): { src: string; id: string } | null {
  const src = process.env.ANALYTICS_SRC, id = process.env.ANALYTICS_ID;
  return src && id ? { src, id } : null;
}
