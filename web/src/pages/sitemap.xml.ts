import type { APIRoute } from 'astro';
import { cities, sitemapEntries } from '../lib/db';
import { REGIONS } from '../lib/regions';
import data from '../lib/areas.json';
import { SPECIALTIES, cityUrl, districtUrl, providerUrl, regionUrl, slugify } from '../lib/site';
import { placeUrl, specUrl } from '../lib/i18n';
import { GUIDES, GUIDES_REVIEWED } from '../lib/guides';

export const GET: APIRoute = async ({ site }) => {
  const base = site!.toString().replace(/\/$/, '');
  // District pages only where they are real pages: with one town they redirect to it, with none they're empty.
  const townsPerDistrict = await Promise.all(SPECIALTIES.map(async (s) => {
    const n = new Map<string, number>();
    for (const c of await cities(s.slug, 1)) if (c.district) n.set(slugify(c.district), (n.get(slugify(c.district)) ?? 0) + 1);
    return n;
  }));
  const urls = ['/', ...SPECIALTIES.map((s) => `/dostupnost?obor=${s.slug}`), ...SPECIALTIES.flatMap((s, i) => [`/${s.slug}`, cityUrl(s.slug, 'praha'),
    ...REGIONS.filter((r) => r.slug !== 'hlavni-mesto-praha').map((r) => regionUrl(s.slug, r.slug)),
    ...Object.keys(data.okresy).filter((o) => (townsPerDistrict[i].get(o) ?? 0) >= 2).map((o) => districtUrl(s.slug, o))])];
  if (GUIDES_REVIEWED) urls.push('/pruvodce', ...GUIDES.map((g) => `/pruvodce/${g.slug}`));
  urls.push('/o-projektu');
  // English: home, guide, specialty and town pages (practice pages are reachable from those).
  urls.push('/en', '/en/guide', ...SPECIALTIES.flatMap((s) => [specUrl('en', s.slug), placeUrl('en', s.slug, 'praha')]));
  for (const e of await sitemapEntries()) {
    if (e.kind === 'city') urls.push(cityUrl(e.specialty!, e.slug), placeUrl('en', e.specialty!, e.slug));
    else urls.push(providerUrl({ id: Number(e.slug), name: e.name }));
  }
  // A sitemap file holds at most 50,000 addresses.
  urls.splice(50_000);
  const body = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    urls.map((u) => `<url><loc>${base}${encodeURI(u)}</loc></url>`).join('\n') + '\n</urlset>\n';
  return new Response(body, { headers: { 'Content-Type': 'application/xml; charset=utf-8', 'Cache-Control': 'public, max-age=86400' } });
};
