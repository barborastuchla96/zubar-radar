import type { APIRoute } from 'astro';
import { confirmationText, createSignup, logMail, parseSignup, sendFailed } from '../../lib/alerts';
import { mailEnabled, sendMail } from '../../lib/mail';
import { clientIp, reporterHash } from '../../lib/report';

export const POST: APIRoute = async ({ request, clientAddress, redirect, site }) => {
  const form = await request.formData();
  const parsed = parseSignup(form);
  const back = (u: string) => redirect(`${parsed.back}${parsed.back.includes('?') ? '&' : '?'}u=${u}#upozorneni`, 303);
  if (!mailEnabled()) return back('off');
  if (!parsed.ok) return back(parsed.reason === 'bot' ? 'sent' : 'invalid');

  const result = await createSignup(parsed.value, reporterHash(clientIp(request, clientAddress)));
  if (result.kind === 'too_many') return back('too_many');
  if (result.kind === 'send') {
    try {
      await sendMail(parsed.value.email, 'Potvrďte prosím upozornění na volné ordinace',
        confirmationText(String(site ?? process.env.SITE_URL), result.token, parsed.value));
      await logMail('confirm');
    } catch (e) {
      console.error('confirmation email failed:', e);
      await sendFailed(result.token);
      return back('error');
    }
  }
  // "already" gets the same answer, so the form doesn't reveal who is subscribed.
  return back('sent');
};
