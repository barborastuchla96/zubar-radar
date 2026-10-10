import type { APIRoute } from 'astro';
import { SITE_NAME, SPECIALTIES } from '../lib/site';
import { SPECIALTY_EN } from '../lib/i18n';

// llms.txt: a plain summary of the site for AI assistants (https://llmstxt.org).
export const GET: APIRoute = ({ site }) => {
  const u = (p: string) => new URL(p, site).toString();
  const body = `# ${SITE_NAME}

> Nezávislý web, který u všech ordinací zubařů, praktických a dětských lékařů, gynekologů, dentální hygieny a vybraných specialistů (oční, ORL, kožní, psychiatrie, neurologie, urologie, chirurgie, ortopedie) v Česku ukazuje, zda podle posledních zpráv přijímají nové pacienty.

Seznam ordinací pochází z Národního registru poskytovatelů zdravotních služeb (ÚZIS ČR) a aktualizuje se měsíčně.
Stav „přijímá / nepřijímá / pořadník“ vychází z hlášení pacientů po telefonátu, od samotných ordinací a z týdenní kontroly webů ordinací.
Novější zprávy mají větší váhu, po 90 dnech přestávají platit. U každé ordinace je uvedeno, kdy jsme o stavu dostali poslední zprávu.
Web je zdarma, bez registrace a bez cookies. Není to oficiální služba: před návštěvou je vždy potřeba do ordinace zavolat.

## Hledání podle oboru a místa

${SPECIALTIES.map((s) => `- [${s.plural} podle měst a krajů](${u(`/${s.slug}`)})`).join('\n')}
- Adresy stránek: /{obor}/{obec} (např. ${u('/zubar/brno')}), /{obor}/kraj/{kraj}, /{obor}/okres/{okres}, /{obor}/praha. Obory: ${SPECIALTIES.map((s) => s.slug).join(', ')}.
- English version for foreigners, incl. which practices speak English: ${u('/en')}, e.g. ${u('/en/gp/praha-6')}, guide ${u('/en/guide')}. English specialty slugs: ${SPECIALTIES.map((s) => SPECIALTY_EN[s.slug].en).join(', ')}.

## Přehledy

- [Dostupnost péče po krajích a okresech](${u('/dostupnost')}): kolik ordinací přijímá nové pacienty, pro každý obor (?obor=…)
- [O projektu, zdroje dat a ochrana údajů](${u('/o-projektu')})

## Užitečné vědět

- Kdo nemůže najít lékaře, může požádat svou zdravotní pojišťovnu: ze zákona musí pomoci zajistit dostupnou péči.
- Kontakt: info@prijimanovepacienty.cz
`;
  return new Response(body, { headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'public, max-age=86400' } });
};
