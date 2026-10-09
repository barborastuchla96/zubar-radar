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
  icon: string;
}

export const SPECIALTIES: SpecialtyInfo[] = [
  { slug: 'zubar', singular: 'Zubař', plural: 'Zubaři', short: 'Zubaři', acc: 'zubaře', nearby: 'Další zubaři v okolí', some: 'některý zubař', icon: '🦷' },
  { slug: 'praktik', singular: 'Praktický lékař', plural: 'Praktičtí lékaři', short: 'Praktičtí lékaři', acc: 'praktického lékaře', nearby: 'Další praktičtí lékaři v okolí', some: 'některý praktický lékař', icon: '🩺' },
  { slug: 'pediatr', singular: 'Dětský lékař', plural: 'Dětští lékaři', short: 'Dětští lékaři', acc: 'dětského lékaře', nearby: 'Další dětští lékaři v okolí', some: 'některý dětský lékař', icon: '🧸' },
  { slug: 'gynekolog', singular: 'Gynekolog', plural: 'Gynekologové', short: 'Gynekologové', acc: 'gynekologa', nearby: 'Další gynekologové v okolí', some: 'některý gynekolog', icon: '🌸' },
  { slug: 'hygienistka', singular: 'Dentální hygiena', plural: 'Dentální hygiena', short: 'Dentální hygiena', acc: 'dentální hygienu', nearby: 'Další dentální hygiena v okolí', some: 'některá ordinace dentální hygieny', icon: '✨' },
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
/** "1 přijímá", "3 přijímají", "12 přijímá" */
export const prijima = (n: number) => `${num(n)} ${plural(n, 'přijímá', 'přijímají', 'přijímá')}`;

// Every status has a symbol too, so it can be read without relying on colour.
export const STATUS_LABEL: Record<string, { label: string; hint: string; icon: string }> = {
  accepting:     { icon: '✓', label: 'Přijímá nové pacienty', hint: 'Podle čerstvých hlášení přijímá nové pacienty.' },
  waitlist:      { icon: '⏳', label: 'Pořadník',              hint: 'Zapisuje nové pacienty do pořadníku.' },
  mixed:         { icon: '!', label: 'Nejasné',               hint: 'Hlášení si odporují. Zavolejte a ověřte.' },
  not_accepting: { icon: '✗', label: 'Nepřijímá',             hint: 'Podle čerstvých hlášení nové pacienty nepřijímá.' },
  unknown:       { icon: '?', label: 'Zatím nevíme',          hint: 'Zatím to nikdo nenahlásil. Zavolejte a dejte vědět ostatním.' },
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
/** Region page; Prague is both a city and a region and already has its own page. */
export const regionUrl = (spec: string, kraj: string) =>
  kraj === 'hlavni-mesto-praha' ? cityUrl(spec, 'praha') : `/${spec}/kraj/${kraj}`;

export function address(p: { street: string | null; house_no: string | null; city: string | null; postcode?: string | null }) {
  const street = [p.street, p.house_no].filter(Boolean).join(' ');
  const pc = p.postcode ? p.postcode.replace(/^(\d{3})(\d{2})$/, '$1 $2') : null;
  return [street, [pc, p.city].filter(Boolean).join(' ')].filter(Boolean).join(', ');
}

/** Normalise phone for tel: links ("+420 773 255 275" -> "+420773255275"). */
export const telHref = (phone: string) => 'tel:' + phone.replace(/[^\d+]/g, '');

/** Readable Czech number: "+420773255275" -> "773 255 275". Anything unusual is shown as given. */
export function formatPhone(phone: string): string {
  const d = phone.replace(/[^\d+]/g, '').replace(/^(\+|00)420/, '');
  return /^\d{9}$/.test(d) ? d.replace(/(\d{3})(\d{3})(\d{3})/, '$1 $2 $3') : phone.trim();
}

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
