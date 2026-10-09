// Guide pages. Set REVIEWED to true once someone who knows Czech healthcare law and
// practice has checked the texts; until then they stay out of search results.
export const GUIDES_REVIEWED = false;
export const GUIDES_UPDATED = '2026-10-09';

export interface Guide { slug: string; title: string; lead: string }
export const GUIDES: Guide[] = [
  { slug: 'jak-najit-lekare', title: 'Jak najít zubaře nebo lékaře, který přijímá nové pacienty',
    lead: 'Pět kroků, které ušetří nejvíc času: koho volat první, kdy volat a co říct.' },
  { slug: 'kdyz-vas-nikde-nevezmou', title: 'Co dělat, když vás žádný lékař nechce přijmout',
    lead: 'Kdy vás lékař smí odmítnout, co po něm můžete chtít a jak vám musí pomoci pojišťovna.' },
];

export function faqLd(items: [string, string][]) {
  return {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: items.map(([q, a]) => ({ '@type': 'Question', name: q, acceptedAnswer: { '@type': 'Answer', text: a } })),
  };
}
