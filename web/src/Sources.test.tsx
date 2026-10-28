import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";
import { NO_KEY_NOTE, SWITCHES_KEY, SourceSwitches, type Switches, loadSwitches } from "./Sources";

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

it("по умолчанию оба переключателя выключены", () => {
  expect(loadSwitches()).toEqual({ real_data: false, llm: false });
  localStorage.setItem(SWITCHES_KEY, "{broken");
  expect(loadSwitches()).toEqual({ real_data: false, llm: false });
});

it("TRAP-1: без ключа в .env переключатель недоступен и выключен, с подсказкой", () => {
  const [real, llm] = render({ real_data: true, llm: true }, { real_data: false, llm: true });
  expect(real.disabled && !real.checked).toBe(true);
  expect(real.closest("label")?.title).toBe("нет ключа в .env");
  expect(llm.disabled || !llm.checked).toBe(false);
  expect(llm.closest("label")?.textContent).toBe("GigaChat");
});

it("TRAP-1: без ключа причина видна подписью, не только подсказкой (#262)", () => {
  const [real] = render({ real_data: false, llm: false }, { real_data: false, llm: true });
  const note = box.querySelector(".switches-note");
  expect(note?.textContent).toBe(NO_KEY_NOTE);
  expect(real.getAttribute("aria-describedby")).toBe(note?.id);
  render({ real_data: false, llm: false }, { real_data: true, llm: true });
  expect(box.querySelector(".switches-note")).toBeNull();
});

it("включение отдаёт новое состояние", () => {
  let got: Switches | null = null;
  const [real] = render({ real_data: false, llm: false }, { real_data: true, llm: true }, (s) => (got = s));
  act(() => real.click());
  expect(got).toEqual({ real_data: true, llm: false });
});
