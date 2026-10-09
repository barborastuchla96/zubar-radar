// Turning what people type ("Dejvice", "Brno-sever", "160 00", "Vinohradská 12") into a place.
// Town names come from the register; everything else falls back to Prague neighbourhoods,
// the city in front of a district name, postcodes, and finally Mapy.com geocoding.
import { type CityCount, cities, postcodePlace } from './db';
import { slugify } from './site';

export type PlaceResult =
  | { kind: 'city'; slug: string }
  | { kind: 'list'; matches: CityCount[] }
  | { kind: 'near'; lat: number; lng: number; label: string }
  | { kind: 'none' };

// Prague neighbourhoods (katastrální území, the names people use) → the district the register files them under.
const PRAGUE: Record<number, string[]> = {
  1: ['Staré Město', 'Josefov', 'Malá Strana', 'Hradčany'],
  2: ['Nové Město', 'Vinohrady', 'Vyšehrad', 'Nusle'],
  3: ['Žižkov'],
  4: ['Michle', 'Podolí', 'Braník', 'Krč', 'Hodkovičky', 'Lhotka', 'Kunratice', 'Pankrác'],
  5: ['Smíchov', 'Košíře', 'Motol', 'Hlubočepy', 'Barrandov', 'Radlice', 'Jinonice', 'Slivenec', 'Malvazinky'],
  6: ['Dejvice', 'Bubeneč', 'Břevnov', 'Střešovice', 'Veleslavín', 'Vokovice', 'Ruzyně', 'Liboc', 'Suchdol', 'Sedlec', 'Lysolaje', 'Nebušice', 'Přední Kopanina', 'Petřiny'],
  7: ['Holešovice', 'Letná', 'Troja'],
  8: ['Karlín', 'Libeň', 'Kobylisy', 'Bohnice', 'Čimice', 'Ďáblice', 'Dolní Chabry', 'Březiněves', 'Palmovka'],
  9: ['Vysočany', 'Prosek', 'Střížkov', 'Hrdlořezy'],
  10: ['Vršovice', 'Strašnice', 'Malešice', 'Záběhlice'],
  11: ['Chodov', 'Háje', 'Jižní Město'],
  12: ['Modřany', 'Komořany', 'Kamýk', 'Cholupice', 'Točná'],
  13: ['Stodůlky', 'Třebonice', 'Lužiny', 'Nové Butovice', 'Butovice'],
  14: ['Černý Most', 'Kyje', 'Hostavice', 'Hloubětín'],
  15: ['Hostivař', 'Horní Měcholupy', 'Dolní Měcholupy', 'Štěrboholy', 'Petrovice'],
  16: ['Radotín', 'Zbraslav', 'Lipence', 'Velká Chuchle', 'Lochkov'],
  17: ['Řepy', 'Zličín'],
  18: ['Letňany', 'Čakovice'],
  19: ['Kbely', 'Vinoř', 'Satalice'],
  20: ['Horní Počernice'],
  21: ['Újezd nad Lesy', 'Běchovice', 'Klánovice', 'Koloděje'],
  22: ['Uhříněves', 'Pitkovice', 'Kolovraty', 'Benice', 'Královice', 'Nedvězí'],
};
const flat = (s: string) => slugify(s).replace(/-/g, '');
const PRAGUE_PART = new Map(Object.entries(PRAGUE).flatMap(([n, names]) => names.map((name) => [flat(name), `praha-${n}`] as const)));

export async function findPlace(specialty: string, q: string): Promise<PlaceResult> {
  const needle = flat(q).replace(/^prague/, 'praha');
  if (!needle) return { kind: 'none' };
  if (needle === 'praha' || needle === 'prag') return { kind: 'city', slug: 'praha' };
  const all = await cities(specialty, 1);
  const known = new Set(all.map((c) => c.city_slug));

  // 1. Town names from the register (as before)
  const rank = (slug: string) => (flat(slug) === needle ? 0 : flat(slug).startsWith(needle) ? 1 : 2);
  const matches = all.filter((c) => flat(c.city_slug).includes(needle))
    .sort((a, b) => rank(a.city_slug) - rank(b.city_slug) || b.n - a.n).slice(0, 30);
  if (matches.length === 1 || (matches.length > 0 && rank(matches[0].city_slug) === 0 && rank(matches[1]?.city_slug ?? '') !== 0)) {
    return { kind: 'city', slug: matches[0].city_slug };
  }
  if (matches.length > 0) return { kind: 'list', matches };

  // 2. "Praha 6 – Dejvice", "Dejvice", "Praha-Žižkov"
  const num = slugify(q).match(/^(?:praha|prague)-?(\d{1,2})\b/);
  if (num && known.has(`praha-${num[1]}`)) return { kind: 'city', slug: `praha-${num[1]}` };
  const part = PRAGUE_PART.get(needle.replace(/^praha/, ''));
  if (part && known.has(part)) return { kind: 'city', slug: part };

  // 3. "Brno-sever", "Ostrava-Poruba", "Ústí nad Labem-Střekov": the city in front
  const words = slugify(q).split('-');
  for (let k = words.length - 1; k >= 1; k--) {
    const slug = words.slice(0, k).join('-');
    if (known.has(slug)) return { kind: 'city', slug };
  }

  // 4. Postcode: "160 00", "16000"
  const pc = q.replace(/\s+/g, '');
  if (/^\d{5}$/.test(pc)) {
    const at = await postcodePlace(pc);
    if (at) return { kind: 'near', lat: at.lat, lng: at.lng, label: `PSČ ${pc.slice(0, 3)} ${pc.slice(3)}` };
  }

  // 5. Anything else (streets, neighbourhoods outside Prague): ask Mapy.com
  const geo = await geocode(q);
  return geo ? { kind: 'near', ...geo } : { kind: 'none' };
}

/** Mapy.com geocoding (same key as the map tiles). Quietly gives up on any problem. */
async function geocode(q: string): Promise<{ lat: number; lng: number; label: string } | null> {
  const key = (process.env.MAPY_API_KEY ?? '').trim();
  if (!key || q.trim().length < 3) return null;
  const base = process.env.MAPY_GEOCODE_URL ?? 'https://api.mapy.com/v1/geocode';   // overridable for tests
  const url = `${base}?lang=cs&limit=5&query=${encodeURIComponent(q.trim())}&apikey=${encodeURIComponent(key)}`;
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(3000) });
    if (!r.ok) return null;
    // v1 answer: { items: [{ name, location, position: { lon, lat } }] }; also accept lat/lon at the top level.
    const data = (await r.json()) as { items?: Record<string, any>[] };
    const pos = (i: Record<string, any>) => ({ lat: Number(i.position?.lat ?? i.lat), lng: Number(i.position?.lon ?? i.position?.lng ?? i.lon ?? i.lng) });
    const hit = (data.items ?? []).find((i) => {
      const { lat, lng } = pos(i);
      return lat > 48.5 && lat < 51.1 && lng > 12 && lng < 18.9;   // first hit inside Czechia
    });
    if (!hit) return null;
    const { lat, lng } = pos(hit);
    const where = typeof hit.location === 'string' ? hit.location.replace(/,\s*Česko$/, '') : '';
    return { lat, lng, label: [hit.name, where].filter((x) => typeof x === 'string' && x).join(', ') || q.trim() };
  } catch {
    return null;
  }
}
