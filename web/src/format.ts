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

// «Гости» of the cart and the voucher: «2 взр., дети: 1 год, 5 лет, с питомцем».
export function guestsText(spec: Pick<TripSpec, "adults" | "children_ages" | "pets">): string {
  const parts = [`${spec.adults} взр.`];
  if (spec.children_ages.length) parts.push(`дети: ${spec.children_ages.map(yearsText).join(", ")}`);
  if (spec.pets) parts.push("с питомцем");
  return parts.join(", ");
}

// Chat line after a cart edit: the real count and total (TRAP-7: the sums are mock).
export function cartLine(cart: Cart): string {
  if (!cart.items.length) return "Корзина пуста.";
  const mock = cart.items.some((o) => o.synthetic);
  return `Теперь в корзине ${cart.items.length} поз. на ${rub(cart.total_rub)}${mock ? " (синтетика)" : ""}.`;
}

// Times are shown as written in the ISO string: local to the place of the event, never
// converted to the browser zone (TRAP-5).
export function localTime(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  return m ? `${m[3]}.${m[2]} ${m[4]}:${m[5]}` : iso;
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
// the destinations becomes one lead line with their count. Every other line (summary, climate,
// «Предлагаю», «Почему», assumptions, questions, hints) stays as it is.
const LISTS: { head: RegExp; row: RegExp; lead?: (rows: number) => string }[] = [
  { head: /^Отели:$/, row: /^\d+\. .+ — \S*★, рейтинг / },
  { head: /^Транспорт:$/, row: /^Рейс \d+\. / },
  { head: /^План поездки:$/, row: /^\d{2}\.\d{2} \d{2}:\d{2} \(UTC[+-][\d:]+\) — / },
  {
    head: /^Подходящие направления.*:$/,
    row: /^\d+\. .+ \(.+\) — от .+ на \d+ чел\./,
    lead: (n) => `Нашёл ${n} ${countWord(n, "направление", "направления", "направлений")} — выберите карточку ниже.`,
  },
];

function countWord(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m100 > 10 && m100 < 15) return many;
  return m10 === 1 ? one : m10 > 1 && m10 < 5 ? few : many;
}

export function brief(text: string): string {
  const out: string[] = [];
  let list: (typeof LISTS)[number] | null = null;
  let head = -1; // index in out of the lead line of the open list
  let rows = 0;
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (list && list.row.test(t)) {
      rows++;
      if (list.lead) out[head] = list.lead(rows);
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
