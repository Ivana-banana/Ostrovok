import type { LlmMode } from "./contracts";

// #490: the user turns on the real sources; both off by default. Sent with every chat request.
export type Switches = { real_data: boolean; llm: boolean };

export const SWITCHES_KEY = "trip.switches";
const OFF: Switches = { real_data: false, llm: false };
const NO_KEY = "нет ключа в .env";
// #262: the jury runs without keys, so the reason is visible, not only in a tooltip.
export const NO_KEY_NOTE =
  "Недоступно в этой сборке: нет ключа API в .env. В демо команды работает с реальными данными.";
// #534: /api/mode did not answer twice — the keys are unknown, not missing.
export const NO_MODE_NOTE = "Режимы не загрузились: обновите страницу.";
export const LOADING_NOTE = "Режимы загружаются…";
export type ModeState = "loading" | "ok" | "failed"; // GET /api/mode
const NOTE_ID = "switches-no-key";
const LABELS: [keyof Switches, string][] = [
  ["real_data", "Реальные данные (Авиасейлс)"],
  ["llm", "GigaChat"],
];

// Storage can throw or hold foreign data: then both switches are off.
export function loadSwitches(): Switches {
  try {
    const data: unknown = JSON.parse(localStorage.getItem(SWITCHES_KEY) ?? "null");
    if (typeof data === "object" && data !== null) {
      const d = data as Record<string, unknown>;
      return { real_data: d.real_data === true, llm: d.llm === true };
    }
  } catch {
    // broken or blocked storage: the defaults
  }
  return OFF;
}

export function saveSwitches(s: Switches) {
  try {
    localStorage.setItem(SWITCHES_KEY, JSON.stringify(s));
  } catch {
    // no storage: the switches hold until the reload
  }
}

// What a chat request asks for: a switch counts only when /api/mode says its key is in .env.
export function requested(on: Switches, can: LlmMode): Switches {
  return { real_data: on.real_data && can.real_data, llm: on.llm && can.llm };
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
  return (
    <div className="switches" role="group" aria-label="Источники данных и модель">
      {LABELS.map(([key, label]) => (
        <label key={key} className="switch" title={can[key] || mode !== "ok" ? undefined : NO_KEY}>
          <input
            type="checkbox"
            role="switch"
            checked={on[key] && can[key]}
            disabled={!can[key]}
            aria-describedby={can[key] ? undefined : NOTE_ID}
            onChange={(e) => onChange({ ...on, [key]: e.target.checked })}
          />
          <span>{label}</span>
        </label>
      ))}
      {LABELS.some(([key]) => !can[key]) && (
        <small id={NOTE_ID} className="switches-note">
          {mode === "failed" ? NO_MODE_NOTE : mode === "loading" ? LOADING_NOTE : NO_KEY_NOTE}
        </small>
      )}
    </div>
  );
}
