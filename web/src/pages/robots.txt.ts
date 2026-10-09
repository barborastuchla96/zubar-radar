import type { APIRoute } from 'astro';

// Search engines and AI assistants are welcome: being quoted in their answers is how people find us.
// Only form endpoints and the personal "near me" results stay out.
const AI_BOTS = ['GPTBot', 'OAI-SearchBot', 'ChatGPT-User', 'ClaudeBot', 'Claude-SearchBot', 'Claude-User',
  'PerplexityBot', 'Google-Extended', 'Applebot-Extended', 'SeznamBot'];

export const GET: APIRoute = ({ site }) => {
  const rules = 'Disallow: /api/\nDisallow: /blizko\nDisallow: /upozorneni/\n';
  const body = ['User-agent: *\n' + rules, ...AI_BOTS.map((b) => `User-agent: ${b}\nAllow: /\n${rules}`)].join('\n') +
    `\nSitemap: ${new URL('/sitemap.xml', site)}\n`;
  return new Response(body, { headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'public, max-age=86400' } });
};
