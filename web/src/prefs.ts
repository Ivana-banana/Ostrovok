// Stable traveller preferences from the last trip. They live only in this browser (localStorage).
// Only these four fields are kept: no prices, offers, dates or names (task #19).
import type { TripSpec } from "./contracts";
import { partyText } from "./params";

export type Prefs = { origin?: string; adults?: number; children_ages?: number[]; pets?: boolean };

export const PREFS_KEY = "trip.prefs";

const AGENT_ORIGIN = (a: string) => a.split(":", 1)[0] === "откуда";
// Mirrors PARTY_UNKNOWN in backend/src/api/model.py.
const AGENT_PARTY = "взрослых: 2 (состав не назван)";

// Takes only what the user named himself: a field the agent guessed is not a preference.
export function prefsFrom(spec: TripSpec): Prefs {
  const p: Prefs = {};
  if (spec.origin && !spec.assumptions.some(AGENT_ORIGIN)) p.origin = spec.origin;
  if (!spec.assumptions.includes(AGENT_PARTY)) {
    p.adults = spec.adults;
    p.children_ages = spec.children_ages;
  }
  if (spec.pets) p.pets = true;
  return p;
}

export function loadPrefs(): Prefs {
  try {
    const d: unknown = JSON.parse(localStorage.getItem(PREFS_KEY) ?? "null");
    if (typeof d !== "object" || d === null) return {};
    const o = d as Record<string, unknown>;
    const p: Prefs = {};
    if (typeof o.origin === "string" && o.origin) p.origin = o.origin;
    if (typeof o.adults === "number" && o.adults > 0) p.adults = o.adults;
    if (Array.isArray(o.children_ages) && o.children_ages.every((a) => typeof a === "number")) p.children_ages = o.children_ages;
    if (o.pets === true) p.pets = true;
    return p;
  } catch {
    return {};
  }
}

function savePrefs(p: Prefs) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(p));
  } catch {
    // no storage: nothing is remembered
  }
}

// Named fields overwrite the old ones; fields the agent guessed keep the old value.
export function rememberPrefs(spec: TripSpec): Prefs {
  const old = loadPrefs();
  const next = { ...old, ...prefsFrom(spec) };
  if (JSON.stringify(next) !== JSON.stringify(old)) savePrefs(next);
  return next;
}

export function forgetPrefs(): Prefs {
  try {
    localStorage.removeItem(PREFS_KEY);
  } catch {
    // nothing to forget
  }
  return {};
}

// «вылет: Москва, 2 взрослых, с питомцем» — shown to the user as «как в прошлый раз».
export function prefsText(p: Prefs): string {
  const parts: string[] = [];
  if (p.origin) parts.push(`вылет: ${p.origin}`);
  if (p.adults) parts.push(partyText(p.adults, p.children_ages ?? []));
  if (p.pets) parts.push("с питомцем");
  return parts.join(", ");
}

// The text that fills the composer. The user sees it and can edit it before sending.
export const prefsDraft = (p: Prefs) => `${prefsText(p)}. `;
