import type { LlmMode } from "./contracts";

// #566: real sources and GigaChat by default. One switch «Синтетика», off by default, turns every
// source synthetic. GigaChat has no switch: the request asks for it whenever /api/mode has a key.
export type Switches = { synthetic: boolean };

export const SWITCHES_KEY = "trip.synthetic"; // #566: a new key, the old «trip.switches» held «off»
const OFF: Switches = { synthetic: false };
// #262: the jury runs without keys, so the reason is visible, not only in a tooltip.
export const NO_KEY_NOTE =
  "GigaChat недоступен в этой сборке: нет ключа API в .env. Ответы — по правилам.";
// #534: /api/mode did not answer twice — the keys are unknown, not missing.
export const NO_MODE_NOTE = "Режимы не загрузились: обновите страницу.";
export const LOADING_NOTE = "Режимы загружаются…";
export type ModeState = "loading" | "ok" | "failed"; // GET /api/mode
const NOTE_ID = "switches-note";

// Storage can throw or hold foreign data: then the switch is off (real data).
export function loadSwitches(): Switches {
  try {
    const data: unknown = JSON.parse(localStorage.getItem(SWITCHES_KEY) ?? "null");
    if (typeof data === "object" && data !== null) {
      return { synthetic: (data as Record<string, unknown>).synthetic === true };
    }
  } catch {
    // broken or blocked storage: the default
  }
  return OFF;
}

export function saveSwitches(s: Switches) {
  try {
    localStorage.setItem(SWITCHES_KEY, JSON.stringify(s));
  } catch {
    // no storage: the switch holds until the reload
  }
}

// What a chat request asks for: real data unless «Синтетика»; the model when /api/mode has a key.
export function requested(on: Switches, can: LlmMode): { real_data: boolean; llm: boolean } {
  return { real_data: !on.synthetic, llm: can.llm };
}

export function SourceSwitches({
  on,
  can,
  mode = "ok",
  onChange,
}: {
  on: Switches;
  can: LlmMode;
  mode?: ModeState; // not "ok": the keys are unknown, the note says so instead of «нет ключа»
  onChange: (next: Switches) => void;
}) {
  const note = mode === "failed" ? NO_MODE_NOTE : mode === "loading" ? LOADING_NOTE : can.llm ? null : NO_KEY_NOTE;
  return (
    <div className="switches" role="group" aria-label="Источники данных">
      <label className="switch" title="Все цены, рейсы и отели — синтетические, без сети">
        <input
          type="checkbox"
          role="switch"
          checked={on.synthetic}
          onChange={(e) => onChange({ synthetic: e.target.checked })}
        />
        <span>Синтетика</span>
      </label>
      {note && (
        <small id={NOTE_ID} className="switches-note">
          {note}
        </small>
      )}
    </div>
  );
}
