import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";
import { NO_KEY_NOTE, SWITCHES_KEY, SourceSwitches, type Switches, loadSwitches, requested } from "./Sources";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root;
let box: HTMLDivElement;

beforeEach(() => {
  localStorage.clear();
  box = document.createElement("div");
  document.body.appendChild(box);
  root = createRoot(box);
});

afterEach(() => {
  act(() => root.unmount());
  box.remove();
});

function render(on: Switches, can: { llm: boolean; real_data: boolean }, onChange = (_: Switches) => {}) {
  act(() => root.render(<SourceSwitches on={on} can={can} onChange={onChange} />));
  return [...box.querySelectorAll<HTMLInputElement>("input[role='switch']")];
}

it("#566: по умолчанию «Синтетика» выключена — реальные данные", () => {
  expect(loadSwitches()).toEqual({ synthetic: false });
  localStorage.setItem(SWITCHES_KEY, "{broken");
  expect(loadSwitches()).toEqual({ synthetic: false });
});

it("#566: один переключатель «Синтетика», GigaChat без переключателя", () => {
  const sw = render({ synthetic: false }, { real_data: true, llm: true });
  expect(sw.map((x) => x.closest("label")?.textContent)).toEqual(["Синтетика"]);
  expect(box.querySelector(".switches-note")).toBeNull();
});

it("TRAP-1: без ключа модели причина видна подписью (#262)", () => {
  render({ synthetic: false }, { real_data: true, llm: false });
  expect(box.querySelector(".switches-note")?.textContent).toBe(NO_KEY_NOTE);
});

it("#566: запрос — реальные данные без «Синтетики», модель всегда (без ключа backend отвечает правилами)", () => {
  expect(requested({ synthetic: false })).toEqual({ real_data: true, llm: true });
  expect(requested({ synthetic: true })).toEqual({ real_data: false, llm: true });
});

it("включение отдаёт новое состояние", () => {
  let got: Switches | null = null;
  const [sw] = render({ synthetic: false }, { real_data: true, llm: true }, (s) => (got = s));
  act(() => sw.click());
  expect(got).toEqual({ synthetic: true });
});
