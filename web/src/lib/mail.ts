import nodemailer, { type Transporter } from 'nodemailer';
import { SITE_NAME } from './site';

/** Sender name on Czech e-mails (same as radar/alerts.py MAIL_FROM_NAME). */
export const MAIL_FROM_NAME_CS = 'Barbora';

/** Alerts are switched on only when SMTP is configured (deploy/.env). */
export const mailEnabled = () => Boolean(process.env.SMTP_HOST);

let transport: Transporter | undefined;

export async function sendMail(to: string, subject: string, text: string, headers: Record<string, string> = {}, fromName = SITE_NAME) {
  if (!mailEnabled()) throw new Error('SMTP_HOST is not set');
  const port = Number(process.env.SMTP_PORT ?? 465);
  transport ??= nodemailer.createTransport({
    host: process.env.SMTP_HOST,
    port,
    secure: port === 465,
    requireTLS: port !== 465 && !['localhost', '127.0.0.1'].includes(process.env.SMTP_HOST ?? ''),  // 587: STARTTLS or nothing
    auth: process.env.SMTP_USER ? { user: process.env.SMTP_USER, pass: process.env.SMTP_PASSWORD ?? '' } : undefined,
    connectionTimeout: 15_000,
  });
  const from = process.env.MAIL_FROM || process.env.SMTP_USER!;
  await transport.sendMail({ from: { name: fromName, address: from }, to, subject, text, headers });
}
