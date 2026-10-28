import type { Cart, ChatReply, Meal, Mode, Priority, TripSpec } from "./contracts";

// One reply line for the user; the no-model mode is labelled honestly (TRAP-1).
const NO_AI = " — упрощённый режим без ИИ";
export function describe(r: ChatReply): string {
  return r.llm ? r.reply : `${r.reply}${NO_AI}`;
}

// The bubble text without the no-model label: only while the «Без ИИ» badge is on the screen
// (TRAP-1 stays honest by the badge, the label after every answer is noise, #434).
export function withoutNoAi(text: string): string {
  return text.endsWith(NO_AI) ? text.slice(0, -NO_AI.length) : text;
}

export const MEALS: Record<Meal, string> = {
  RO: "без питания",
  BB: "завтрак",
  HB: "завтрак и ужин",
  FB: "трёхразовое",
  AI: "всё включено",
};

export const MODES: Record<Mode, string> = { flight: "самолёт", train: "поезд", car: "машина" };

// Field names of plan.changes (TripSpec fields) in user words.
const FIELDS: Record<string, string> = {
  destinations: "направление",
  date_from: "дата начала",
  date_to: "дата конца",
  nights: "ночей",
  adults: "взрослых",
  children_ages: "дети",
  budget_rub: "бюджет",
  hotel_budget_rub: "бюджет отеля",
  meal: "питание",
  origin: "откуда",
  month: "месяц",
};

// The backend picks hotels and destinations by ordinal word ("беру второй").
const ORDINALS = ["первый", "второй", "третий", "четвёртый", "пятый"];

export function ordinal(i: number): string | null {
  return ORDINALS[i] ?? null;
}

export function rub(n: number): string {
  return `${String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, " ")} ₽`;
}

// «1 год», «2 года», «5 лет», «11 лет», «21 год»; age 0 (the backend turns months into 0): «до 1 года».
export function yearsText(n: number): string {
  if (n === 0) return "до 1 года";
  const m100 = n % 100;
  const m10 = n % 10;
  const word = m100 > 10 && m100 < 15 ? "лет" : m10 === 1 ? "год" : m10 > 1 && m10 < 5 ? "года" : "лет";
  return `${n} ${word}`;
}

// «1 ночь», «2 ночи», «5 ночей», «11 ночей», «21 ночь».
export function nightsText(n: number): string {
  const m100 = n % 100;
  const m10 = n % 10;
  const word = m100 > 10 && m100 < 15 ? "ночей" : m10 === 1 ? "ночь" : m10 > 1 && m10 < 5 ? "ночи" : "ночей";
  return `${n} ${word}`;
}

// «Гости» of the cart and the voucher: «2 взр., дети: 1 год, 5 лет, с питомцем».
export function guestsText(spec: Pick<TripSpec, "adults" | "children_ages" | "pets">): string {
  const parts = [`${spec.adults} взр.`];
  if (spec.children_ages.length) parts.push(`дети: ${spec.children_ages.map(yearsText).join(", ")}`);
  if (spec.pets) parts.push("с питомцем");
  return parts.join(", ");
}

// Chat line after a cart edit: the real count and total (TRAP-7: a mock or mixed sum says so).
export function cartLine(cart: Cart): string {
  if (!cart.items.length) return "Корзина пуста.";
  const mock = cart.items.filter((o) => o.synthetic).length;
  const mark = mock === cart.items.length ? " (синтетика)" : mock ? " (часть цен — синтетика)" : "";
  return `Теперь в корзине ${cart.items.length} поз. на ${rub(cart.total_rub)}${mark}.`;
}

// Times are shown as written in the ISO string: local to the place of the event, never
// converted to the browser zone (TRAP-5).
export function localTime(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  return m ? `${m[3]}.${m[2]} ${m[4]}:${m[5]}` : iso;
}

// The zone of the event as the backend writes it in the chat: «UTC+3», «UTC+5:30» (TRAP-5, #364).
export function zoneLabel(iso: string): string {
  const m = /([+-])(\d{2}):?(\d{2})$/.exec(iso);
  if (!m) return /Z$/.test(iso) ? "UTC" : "";
  const minutes = m[3] === "00" ? "" : `:${m[3]}`;
  return `UTC${m[1]}${Number(m[2])}${minutes}`;
}

// Local time with its zone: «11.12 07:10 (UTC+3)».
export function zonedTime(iso: string): string {
  const zone = zoneLabel(iso);
  return zone ? `${localTime(iso)} (${zone})` : localTime(iso);
}

// A moment of the service itself (voucher time, written in UTC): shown in the browser zone.
export function browserTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getDate())}.${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function localDate(iso: string | null): string {
  const m = iso ? /^(\d{4})-(\d{2})-(\d{2})/.exec(iso) : null;
  return m ? `${m[3]}.${m[2]}.${m[1]}` : "—";
}

export function clock(t: string): string {
  return t.slice(0, 5);
}

// Real duration between two zoned instants, e.g. "4 ч 05 мин".
export function duration(fromIso: string, toIso: string): string {
  const min = Math.round((Date.parse(toIso) - Date.parse(fromIso)) / 60000);
  if (!Number.isFinite(min) || min < 0) return "—";
  return `${Math.floor(min / 60)} ч ${String(min % 60).padStart(2, "0")} мин`;
}

export function hours(h: number): string {
  return `${h.toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ч`;
}

export function fieldLabel(what: string): string {
  return FIELDS[what] ?? what;
}

// plan.changes values are str() of Python values: "None", "['Казань']", "2026-12-11".
export function changeValue(v: string): string {
  if (v === "None" || v === "[]" || v === "") return "не задано";
  const list = /^\[(.*)\]$/.exec(v);
  const text = list ? list[1].replace(/'/g, "") : v;
  const date = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  return date ? `${date[3]}.${date[2]}.${date[1]}` : text;
}

// Priority names in user words. The same words reach the backend extractor (extract.py,
// PRIORITIES): each phrase matches only its own priority there.
export const PRIORITIES: Record<Priority, string> = {
  price: "цена",
  cancellation: "отмена",
  meal: "питание",
  location: "расположение",
  travel_time: "время в пути",
  rating: "рейтинг",
};

// A chat message that sets the whole priority order, most important first. The agent
// reads it as an order of importance, not as a filter: no other field changes (S9).
export function priorityMessage(order: Priority[]): string {
  return `Мои приоритеты по важности: ${order.map((p) => PRIORITIES[p]).join(", ")}.`;
}

// Moves the item one place; null when it cannot move.
export function movePriority(order: Priority[], i: number, by: -1 | 1): Priority[] | null {
  const j = i + by;
  if (i < 0 || i >= order.length || j < 0 || j >= order.length) return null;
  const next = [...order];
  [next[i], next[j]] = [next[j], next[i]];
  return next;
}

// Data sources the product really uses. Add a source here only when the code reads it
// (research/data-sources.md has the licences). Today: hotels from the OSM snapshot.
export const SOURCES: { name: string; text: string; url: string; licence: string }[] = [
  {
    name: "OpenStreetMap",
    text: "Часть отелей и их удобства — © участники OpenStreetMap",
    url: "https://www.openstreetmap.org/copyright",
    licence: "ODbL",
  },
];

// The cards under the last answer already show these lists (#434), so the chat bubble drops
// them: the hotel rows after «Отели:», the «Рейс N.» rows after «Транспорт:», the timeline rows
// after «План поездки:» and the destination rows (backend api/model.py _text_compare,
// _text_assemble, _text_inspire). A row is dropped only right after its header. The header of
// the destinations and of the plan becomes one short lead line. Every other line (summary, climate,
// «Предлагаю», «Почему», assumptions, questions, hints) stays as it is.
const LISTS: { head: RegExp; row: RegExp; lead?: (rows: number, last: boolean) => string }[] = [
  { head: /^Отели:$/, row: /^\d+\. .+ — \S*★, рейтинг / },
  { head: /^Транспорт:$/, row: /^Рейс \d+\. / },
  {
    head: /^План поездки:$/,
    row: /^\d{2}\.\d{2} \d{2}:\d{2} \(UTC[+-][\d:]+\) — /,
    lead: (_n, last) => (last ? "План поездки собран — таймлайн ниже." : "План поездки был собран."),
  },
  {
    head: /^Подходящие направления.*:$/,
    row: /^\d+\. .+ \(.+\) — от .+ на \d+ чел\./,
    lead: (n, last) =>
      `Нашёл ${n} ${countWord(n, "направление", "направления", "направлений")}${last ? " — выберите карточку ниже." : "."}`,
  },
];

function countWord(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m100 > 10 && m100 < 15) return many;
  return m10 === 1 ? one : m10 > 1 && m10 < 5 ? few : many;
}

// `last`: the cards sit under this answer. In an older answer they sit under a newer one, so
// the lead line must not point «ниже» (#439).
export function brief(text: string, last = true): string {
  const out: string[] = [];
  let list: (typeof LISTS)[number] | null = null;
  let head = -1; // index in out of the lead line of the open list
  let rows = 0;
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (list && list.row.test(t)) {
      rows++;
      if (list.lead) out[head] = list.lead(rows, last);
      continue;
    }
    list = LISTS.find((l) => l.head.test(t)) ?? null;
    if (list) {
      rows = 0;
      if (list.lead) {
        head = out.length;
        out.push(line); // no rows after it: the header stays as it is
      }
      continue;
    }
    out.push(line);
  }
  return out.some((l) => l.trim()) ? out.join("\n") : text;
}

// #468: the compare answer (backend api/model.py _offer_lines) has the header «Предлагаю:», one
// line per item of the trip and the line «Итого». `items` are «label: text» pairs, `more` are the
// details that the chat folds under «Подробнее»: why, risks, climate and the note on mock prices.
// null: the text has no such block (other stages, older answers), the chat shows it line by line.
export type OfferParts = {
  head: string[];
  items: { label: string; text: string }[];
  points: Point[] | null; // the items as blocks; null: a line did not parse, show the plain list
  total: string;
  more: string[];
  tail: string[];
};

// One item of the proposed trip as a block (#468): facts are the short sub-lines of the block.
export type Ride = { from: string; to: string; facts: string[] };
export type Point =
  | { kind: "hotel"; name: string; facts: string[]; price: string }
  | {
      kind: "leg";
      label: string; // «Туда» or «Обратно»
      mode: Mode;
      ride: Ride; // the route: «Москва» → «Казань (KZN)»; facts: seats, changes, notes
      start: string; // «10.12 18:50»
      end: string;
      zones: string[]; // one zone when both times share it, else the zone of each time
      price: string;
    }
  | { kind: "transfer"; rides: Ride[]; price: string }
  | { kind: "extra"; name: string; price: string } // a paid extra of the hotel: «Ранний заезд …»
  | { kind: "included"; facts: string[] };

const ITEM = /^(Отель|Туда|Обратно|Трансферы|Доп\. услуга|Включено): (.+)$/;
const MORE = /^(Почему|Риск|Климат): |^Цены( —|:) .+\.$/; // «Цены — синтетика.» or the sources (#567)

export function offerParts(text: string): OfferParts | null {
  const lines = text.split("\n").filter((l) => l.trim());
  const at = lines.findIndex((l) => l.trim() === "Предлагаю:");
  if (at < 0) return null;
  const items: OfferParts["items"] = [];
  let i = at + 1;
  for (let m; i < lines.length && (m = ITEM.exec(lines[i].trim())); i++) items.push({ label: m[1], text: m[2].replace(/\.$/, "") });
  if (!items.length || !lines[i]?.startsWith("Итого ")) return null;
  const rest = (part: string[]) => part.filter((l) => !MORE.test(l.trim()));
  const points = items.map(point);
  return {
    head: rest(lines.slice(0, at)),
    items,
    points: points.every((x) => x) ? (points as Point[]) : null,
    total: lines[i].slice("Итого ".length).replace(/\.$/, ""),
    more: lines.filter((l) => MORE.test(l.trim())),
    tail: rest(lines.slice(i + 1)),
  };
}

// The words of the mode at the start of a leg title (backend api/offers.py _leg).
const LEG_MODES: [RegExp, Mode][] = [
  [/^перелёт /, "flight"],
  [/^поезд /, "train"],
  [/^на своей машине /, "car"],
];
const ARROW = " → ";
const AT = String.raw`\d{2}\.\d{2} \d{2}:\d{2}`;
const SPAN = new RegExp(String.raw`^(.+), (${AT})(?: \((UTC[^)]+)\))? → (${AT}) \((UTC[^)]+)\)$`);

// «А → Б» and its facts as a ride; null without exactly one arrow in the route.
function ride(route: string, facts: string[]): Ride | null {
  const ends = route.split(ARROW);
  return ends.length === 2 ? { from: ends[0], to: ends[1], facts } : null;
}

// «text — 7 900 ₽»: the text and the price; null without a price at the end. The price keeps the
// label of the backend after it, «7 900 ₽ (101hotels)» (#567): the chat shows it as its chip.
const LABEL = / \([^()]+\)$/;
function priced(text: string): [string, string] | null {
  const label = /₽ \([^()]+\)$/.test(text) ? LABEL.exec(text)![0] : "";
  const bare = text.slice(0, text.length - label.length);
  const at = bare.lastIndexOf(" — ");
  return at > 0 && /₽$/.test(bare) ? [bare.slice(0, at), bare.slice(at + 3) + label] : null;
}

// One item line of the backend (api/model.py _offer_lines) as a block; null when it does not parse.
function point({ label, text }: { label: string; text: string }): Point | null {
  if (label === "Включено") return { kind: "included", facts: text.split(", ") };
  const p = priced(text);
  if (!p) return null;
  const [body, price] = p;
  if (label === "Доп. услуга") return { kind: "extra", name: body, price };
  if (label === "Отель") {
    // «Казантель, Казань, 2★, без питания, невозвратный»: the name may hold commas, the stars may miss
    const parts = body.split(", ");
    const facts = parts.splice(/★$/.test(parts.at(-3) ?? "") ? -3 : -2);
    return parts.length && facts.length ? { kind: "hotel", name: parts.join(", "), facts, price } : null;
  }
  if (label === "Трансферы") {
    // «вокзал → Казантель, Казань, ≈2.6 км; …»: the hotel name holds commas, the facts start at «≈»;
    // without «≈ … км» the whole part is the route and there are no facts
    const rides = body.split("; ").map((r) => {
      const at = r.indexOf(", ≈");
      return at > 0 ? ride(r.slice(0, at), r.slice(at + 2).split(", ")) : ride(r, []);
    });
    return rides.every((r) => r) ? { kind: "transfer", rides: rides as Ride[], price } : null;
  }
  // «поезд Москва → Казань, 2 места, 10.12 18:50 → 11.12 07:10 (UTC+3)»
  const m = SPAN.exec(body);
  const mode = m && LEG_MODES.find(([word]) => word.test(m[1]));
  if (!m || !mode) return null;
  const [route, ...facts] = m[1].replace(mode[0], "").split(", ");
  const r = ride(route, facts);
  return r && { kind: "leg", label, mode: mode[1], ride: r, start: m[2], end: m[4], zones: m[3] ? [m[3], m[5]] : [m[5]], price };
}
