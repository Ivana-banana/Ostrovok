import type { LlmMode } from "./contracts";

// #490: the user turns on the real sources; both off by default. Sent with every chat request.
export type Switches = { real_data: boolean; llm: boolean };

export const SWITCHES_KEY = "trip.switches";
const OFF: Switches = { real_data: false, llm: false };
const NO_KEY = "нет ключа в .env";
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
  onChange,
}: {
  on: Switches;
  can: LlmMode;
  onChange: (next: Switches) => void;
}) {
  return (
    <div className="switches" role="group" aria-label="Источники данных и модель">
      {LABELS.map(([key, label]) => (
        <label key={key} className="switch" title={can[key] ? undefined : NO_KEY}>
          <input
            type="checkbox"
            role="switch"
            checked={on[key] && can[key]}
            disabled={!can[key]}
            onChange={(e) => onChange({ ...on, [key]: e.target.checked })}
          />
          <span>{label}</span>
        </label>
      ))}
    </div>
  );
}
