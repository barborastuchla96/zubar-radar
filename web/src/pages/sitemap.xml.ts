import type { APIRoute } from 'astro';
import { sitemapEntries } from '../lib/db';
import { SPECIALTIES, cityUrl, providerUrl } from '../lib/site';

export const GET: APIRoute = async ({ site }) => {
  const base = site!.toString().replace(/\/$/, '');
  const urls = ['/', ...SPECIALTIES.flatMap((s) => [`/${s.slug}`, cityUrl(s.slug, 'praha')])];
  for (const e of await sitemapEntries()) {
    urls.push(e.kind === 'city' ? cityUrl(e.specialty!, e.slug) : providerUrl({ id: Number(e.slug), name: e.name }));
  }
  const body = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    urls.map((u) => `<url><loc>${base}${encodeURI(u)}</loc></url>`).join('\n') + '\n</urlset>\n';
  return new Response(body, { headers: { 'Content-Type': 'application/xml; charset=utf-8', 'Cache-Control': 'public, max-age=86400' } });
};
