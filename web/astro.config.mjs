import { defineConfig } from 'astro/config';
import node from '@astrojs/node';

const site = new URL(process.env.SITE_URL ?? 'http://localhost:4321');

export default defineConfig({
  site: site.toString(),
  output: 'server',
  adapter: node({ mode: 'standalone' }),
  trailingSlash: 'ignore',
  security: {
    // Behind Caddy the app sees http://web:4321; trust the proxy's
    // X-Forwarded-Host/Proto for our own domain only, so Astro's origin
    // check accepts form posts from https://<domain>.
    allowedDomains: [{ hostname: site.hostname, protocol: site.protocol.replace(':', '') }],
  },
});
