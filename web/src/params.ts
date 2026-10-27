import type { TripSpec } from "./contracts";
import { rub } from "./format";

export type ParamId = "origin" | "dates" | "party" | "budget";
export type ParamChip = { id: ParamId; label: string; value: string; assumed: boolean; why: string[] };

// Assumption labels (the text before «:») that mean the agent filled the field itself.
const ASSUMED: Record<ParamId, string[]> = {
  origin: ["откуда"],
  dates: ["когда", "длительность", "даты"],
  party: ["взрослых"],
  budget: ["бюджет"],
};

const MONTHS_GEN = [
  "января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря",
];

const label = (assumption: string) => assumption.split(":", 1)[0];

export function plural(n: number, one: string, few: string, many: string): string {
  const m100 = n % 100;
  const m10 = n % 10;
  if (m100 > 10 && m100 < 15) return many;
  if (m10 === 1) return one;
  return m10 > 1 && m10 < 5 ? few : many;
}

// «2 взрослых, дети 5 и 9 лет». One year word at the end: the rule reader needs that form.
export function partyText(adults: number, ages: number[]): string {
  const who = `${adults} ${plural(adults, "взрослый", "взрослых", "взрослых")}`;
  if (!ages.length) return who;
  const sorted = [...ages].sort((a, b) => a - b);
  const last = sorted[sorted.length - 1];
  const list = sorted.length > 1 ? `${sorted.slice(0, -1).join(", ")} и ${last}` : String(last);
  return `${who}, ${sorted.length > 1 ? "дети" : "ребёнок"} ${list} ${plural(last, "год", "года", "лет")}`;
}

// «2026-11-07» → «07.11»
const short = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;

function datesText(spec: TripSpec): string {
  return spec.date_from && spec.date_to
    ? `${short(spec.date_from)}–${short(spec.date_to)}`
    : "не выбраны";
}

function budgetText(spec: TripSpec): string {
  if (spec.budget_rub !== null) return `до ${rub(spec.budget_rub)}`;
  if (spec.hotel_budget_rub !== null) return `отель до ${rub(spec.hotel_budget_rub)}`;
  return "не задан";
}

export function paramChips(spec: TripSpec): ParamChip[] {
  const value: Record<ParamId, string> = {
    origin: spec.origin ?? "не выбран",
    dates: datesText(spec),
    party: partyText(spec.adults, spec.children_ages),
    budget: budgetText(spec),
  };
  const names: Record<ParamId, string> = {
    origin: "Откуда",
    dates: "Когда",
    party: "Кто едет",
    budget: "Бюджет",
  };
  return (Object.keys(names) as ParamId[]).map((id) => {
    const why = spec.assumptions.filter((a) => ASSUMED[id].includes(label(a)));
    return { id, label: names[id], value: value[id], assumed: why.length > 0, why };
  });
}

// Assumptions that no chip shows (route, refused places, ...).
export function otherAssumptions(spec: TripSpec): string[] {
  const shown = Object.values(ASSUMED).flat();
  return spec.assumptions.filter((a) => !shown.includes(label(a)));
}

// Chat messages of the chip editors. The agent reads them like any user message (rule form
// verified against backend/src/api/extract.py): «Вылет из», not «Летим из», which also sets the mode.
export const originMessage = (city: string) => `Вылет из ${city.trim()}`;

export function datesMessage(from: string, to: string): string {
  const day = (iso: string) => `${Number(iso.slice(8, 10))} ${MONTHS_GEN[Number(iso.slice(5, 7)) - 1]}`;
  return `с ${day(from)} по ${day(to)}`;
}

export const partyMessage = (adults: number, ages: number[]) =>
  `Едем ${partyText(adults, ages)}${ages.length ? "" : ", без детей"}`;

export const budgetMessage = (rubles: number) => `Бюджет до ${Math.round(rubles)} ₽ на всю поездку`;
