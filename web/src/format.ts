import type { Cart, ChatReply, Meal, Mode, Priority, Stage } from "./contracts";

// One reply line for the user; the no-model mode is labelled honestly (TRAP-1).
export function describe(r: ChatReply): string {
  return r.llm ? r.reply : `${r.reply} — упрощённый режим без ИИ`;
}

export const STAGES: { id: Stage; label: string }[] = [
  { id: "clarify", label: "Уточнение" },
  { id: "inspire", label: "Направления" },
  { id: "select", label: "Выбор" },
  { id: "compare", label: "Сравнение" },
  { id: "assemble", label: "План" },
  { id: "checkout", label: "Корзина" },
  { id: "booked", label: "Бронь" },
];

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

// Chat line after a cart edit: the real count and total (TRAP-7: the sums are mock).
export function cartLine(cart: Cart): string {
  if (!cart.items.length) return "Корзина пуста.";
  const mock = cart.items.some((o) => o.synthetic);
  return `В корзине ${cart.items.length} поз. на ${rub(cart.total_rub)}${mock ? " (синтетика)" : ""}.`;
}

// Times are shown as written in the ISO string: local to the place of the event, never
// converted to the browser zone (TRAP-5).
export function localTime(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  return m ? `${m[3]}.${m[2]} ${m[4]}:${m[5]}` : iso;
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
