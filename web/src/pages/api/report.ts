import type { APIRoute } from 'astro';
import { addReport, provider } from '../../lib/db';
import { clientIp, parseReport, reporterHash } from '../../lib/report';
import { doctorUrl } from '../../lib/i18n';

export const POST: APIRoute = async ({ request, clientAddress, redirect }) => {
  // Not a form at all (a bot or a broken client): say so instead of crashing.
  const form = await request.formData().catch(() => null);
  if (!form) return new Response('Bad request', { status: 400 });
  const parsed = parseReport(form);
  const lang = form.get('lang') === 'en' ? 'en' : 'cs';
  const id = Number(form.get('provider_id'));
  const p = Number.isInteger(id) && id > 0 ? await provider(id) : undefined;
  // The "Jak to dopadlo?" box sends with fetch and wants a short JSON answer instead of a page.
  const wantsJson = (request.headers.get('accept') ?? '').includes('application/json');
  const back = (r: string) => wantsJson
    ? Response.json({ result: r })
    : redirect(`${p ? doctorUrl(lang, p) : lang === 'en' ? '/en' : '/'}?r=${r}#nahlasit`, 303);

  if (!parsed.ok) return back(parsed.reason === 'bot' ? 'ok' : 'invalid'); // don't tell bots they failed
  const result = await addReport({ ...parsed.value, reporterHash: reporterHash(clientIp(request, clientAddress)) });
  return back(result === 'no_provider' ? 'invalid' : result);
};
