// English version (/en/…). Components read the language from the URL, so the same
// card, map and form serve both versions. Town slugs stay Czech (/en/gp/praha-6).
import { SPECIALTIES, type SpecialtyInfo, cityUrl, providerUrl, slugify } from './site';

export type Lang = 'cs' | 'en';
export const langOf = (url: URL): Lang => (url.pathname === '/en' || url.pathname.startsWith('/en/') ? 'en' : 'cs');

export interface SpecialtyEn { en: string; singular: string; plural: string; short: string; nearby: string; specialist?: boolean }
export const SPECIALTY_EN: Record<string, SpecialtyEn> = {
  zubar:       { en: 'dentist',          singular: 'Dentist',              plural: 'Dentists',              short: 'Dentists',     nearby: 'Other dentists nearby' },
  praktik:     { en: 'gp',               singular: 'GP (family doctor)',   plural: 'GPs (family doctors)',  short: 'GPs',          nearby: 'Other GPs nearby' },
  pediatr:     { en: 'pediatrician',     singular: 'Pediatrician',         plural: 'Pediatricians',         short: 'Pediatricians', nearby: 'Other pediatricians nearby' },
  gynekolog:   { en: 'gynecologist',     singular: 'Gynecologist',         plural: 'Gynecologists',         short: 'Gynecologists', nearby: 'Other gynecologists nearby' },
  hygienistka: { en: 'dental-hygienist', singular: 'Dental hygienist',     plural: 'Dental hygienists',     short: 'Dental hygiene', nearby: 'Other dental hygienists nearby' },
  ocni:        { en: 'eye-doctor',       singular: 'Eye doctor',           plural: 'Eye doctors',           short: 'Eye doctors',  nearby: 'Other eye doctors nearby', specialist: true },
  orl:         { en: 'ent',              singular: 'ENT doctor',           plural: 'ENT doctors',           short: 'ENT',          nearby: 'Other ENT doctors nearby', specialist: true },
  kozni:       { en: 'dermatologist',    singular: 'Dermatologist',        plural: 'Dermatologists',        short: 'Dermatologists', nearby: 'Other dermatologists nearby', specialist: true },
  psychiatr:   { en: 'psychiatrist',     singular: 'Psychiatrist',         plural: 'Psychiatrists',         short: 'Psychiatrists', nearby: 'Other psychiatrists nearby', specialist: true },
  neurolog:    { en: 'neurologist',      singular: 'Neurologist',          plural: 'Neurologists',          short: 'Neurologists', nearby: 'Other neurologists nearby', specialist: true },
};
/** /en/gp → the "praktik" specialty */
export const specialtyFromEn = (en: string): SpecialtyInfo | undefined =>
  SPECIALTIES.find((s) => SPECIALTY_EN[s.slug]?.en === en);
export const enOf = (slug: string) => SPECIALTY_EN[slug];

// --- URLs in either language ---
export const specUrl = (lang: Lang, slug: string) => (lang === 'en' ? `/en/${SPECIALTY_EN[slug].en}` : `/${slug}`);
export const placeUrl = (lang: Lang, slug: string, city: string) =>
  lang === 'en' ? `/en/${SPECIALTY_EN[slug].en}/${city}` : cityUrl(slug, city);
export const doctorUrl = (lang: Lang, p: { id: number; name: string }) =>
  lang === 'en' ? `/en/doctor/${p.id}-${slugify(p.name)}` : providerUrl(p);
export const homeUrl = (lang: Lang) => (lang === 'en' ? '/en' : '/');

// --- words ---
export const STATUS_EN: Record<string, { label: string; hint: string; tag: string }> = {
  accepting:     { label: 'Accepting new patients', hint: 'Recent reports say they take new patients.', tag: '✓ Accepting' },
  waitlist:      { label: 'Waiting list',           hint: 'They put new patients on a waiting list.',  tag: '⏳ Waiting list' },
  mixed:         { label: 'Unclear',                hint: 'Reports disagree.',                          tag: '! Unclear' },
  not_accepting: { label: 'Not accepting',          hint: 'Recent reports say they take no new patients.', tag: '✗ Not accepting' },
  unknown:       { label: "Don't know yet",         hint: 'No reports yet.',                            tag: '?' },
};

const n = (x: number) => x.toLocaleString('en');
export const practices = (x: number) => `${n(x)} ${x === 1 ? 'practice' : 'practices'}`;
export const acceptingN = (x: number) => `${n(x)} accepting`;

const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' });
/** "today", "yesterday", "5 days ago", "2 months ago" */
export function agoEn(d: Date | string | null): string | null {
  if (!d) return null;
  const days = Math.round((new Date(d).getTime() - Date.now()) / 86_400_000);
  if (days > -1) return 'today';
  if (days > -45) return rtf.format(days, 'day');
  return rtf.format(Math.round(days / 30), 'month');
}
const TZ = 'Europe/Prague';
export const dayLongEn = (d = new Date()) => d.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: TZ });
export const monthYearEn = (d = new Date()) => d.toLocaleDateString('en-GB', { month: 'long', year: 'numeric', timeZone: TZ });

/** "Brno, dentists: as of 9 October 2026, 12 of 331 practices accept new patients according to recent reports." */
export function factLineEn(place: string, plural: string, accepting: number, total: number, english = 0) {
  const head = `${place}, ${plural.toLowerCase().replace(/\bgps\b/, 'GPs').replace(/\bent\b/, 'ENT')}: as of ${dayLongEn()}`;
  const base = accepting > 0
    ? `${head}, ${n(accepting)} of ${practices(total)} ${accepting === 1 ? 'accepts' : 'accept'} new patients according to recent reports.`
    : `${head}, we have no report yet of any of the ${practices(total)} accepting new patients.`;
  return english > 0 ? `${base} We know of ${n(english)} where you can speak English.` : base;
}

/** Czech place names stay as they are; only the "(okres …)" suffix is translated. */
export const placeEn = (label: string) => label.replace(/\(okres (.*)\)$/, '($1 district)');

const REGION_EN: Record<string, string> = {
  'Hlavní město Praha': 'Prague', 'Středočeský kraj': 'Central Bohemian Region', 'Jihočeský kraj': 'South Bohemian Region',
  'Plzeňský kraj': 'Plzeň Region', 'Karlovarský kraj': 'Karlovy Vary Region', 'Ústecký kraj': 'Ústí nad Labem Region',
  'Liberecký kraj': 'Liberec Region', 'Královéhradecký kraj': 'Hradec Králové Region', 'Pardubický kraj': 'Pardubice Region',
  'Kraj Vysočina': 'Vysočina Region', 'Jihomoravský kraj': 'South Moravian Region', 'Olomoucký kraj': 'Olomouc Region',
  'Moravskoslezský kraj': 'Moravian-Silesian Region', 'Zlínský kraj': 'Zlín Region',
};
export const regionEn = (name: string) => REGION_EN[name] ?? name;

/** "GPs (family doctors)" → "GPs (family doctors)", "Dentists" → "dentists": for use mid-sentence. */
export const lowerEn = (s: string) => s.toLowerCase().replace(/\bgp(s?)\b/g, 'GP$1').replace(/\bent\b/g, 'ENT');
/** "a GP (family doctor)", "an eye doctor", "an ENT doctor" */
export const aDoctorEn = (slug: string) => {
  const s = lowerEn(SPECIALTY_EN[slug].singular).replace(/\bent\b/i, 'ENT');
  return `${/^[aeiou]|^ENT/i.test(s) ? 'an' : 'a'} ${s}`;
};
