import type { APIRoute } from 'astro';
import { type CityCount, cities } from '../../lib/db';
import { isPragueSlug, slugify, specialty } from '../../lib/site';
import { placeUrl } from '../../lib/i18n';

// Town suggestions for the search box ("našeptávač"). Town lists change monthly; cache them briefly.
const cache = new Map<string, { at: number; rows: CityCount[] }>();
async function places(spec: string) {
  const hit = cache.get(spec);
  if (hit && Date.now() - hit.at < 10 * 60_000) return hit.rows;
  const rows = await cities(spec, 1);
  cache.set(spec, { at: Date.now(), rows });
  return rows;
}

export const GET: APIRoute = async ({ url }) => {
  const spec = specialty(url.searchParams.get('s') ?? '');
  const lang = url.searchParams.get('lang') === 'en' ? 'en' : 'cs';
  const needle = slugify((url.searchParams.get('q') ?? '').slice(0, 60));
  if (!spec || needle.length < 2) return Response.json([]);

  const flat = (s: string) => s.replace(/-/g, '');
  const rank = (slug: string) =>
    slug.startsWith(needle) ? 0 : slug.includes(`-${needle}`) ? 1 : needle.length >= 3 && flat(slug).includes(flat(needle)) ? 2 : -1;

  const rows = (await places(spec.slug))
    .map((c) => ({ c, r: rank(c.city_slug) }))
    .filter((x) => x.r >= 0)
    .sort((a, b) => a.r - b.r || b.c.n - a.c.n)
    .slice(0, 8)
    .map(({ c }) => ({
      name: c.city.replace(/ \(okres .*\)$/, ''), url: placeUrl(lang, spec.slug, c.city_slug), n: c.n, accepting: c.accepting,
      where: isPragueSlug(c.city_slug) ? (lang === 'en' ? 'Prague' : 'Praha') : c.district ? (lang === 'en' ? `${c.district} district` : `okres ${c.district}`) : c.region,
    }));
  // "pra…" → offer the whole of Prague first
  if ('praha'.startsWith(needle) || 'prague'.startsWith(needle)) rows.unshift(lang === 'en'
    ? { name: 'Prague – all of it', url: placeUrl(lang, spec.slug, 'praha'), n: 0, accepting: 0, where: 'all districts' }
    : { name: 'Praha – celá', url: placeUrl(lang, spec.slug, 'praha'), n: 0, accepting: 0, where: 'všechny městské části' });
  return Response.json(rows.slice(0, 8), { headers: { 'Cache-Control': 'public, max-age=300' } });
};
