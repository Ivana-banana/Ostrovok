import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe as suite, expect, it, vi } from "vitest";
import fixture from "../../tests/fixtures/chat_reply.json";
import { App, SESSION_KEY } from "./App";
import type { ChatReply } from "./contracts";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const booked = fixture as unknown as ChatReply;
const checkoutStage: ChatReply = { ...structuredClone(booked), stage: "checkout", voucher: null };

type Route = (body: unknown) => { status: number; body?: unknown };
let routes: Record<string, Route>;
let calls: { key: string; body: unknown }[];
let root: Root;
let box: HTMLDivElement;

function json(status: number, body: unknown = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  localStorage.clear();
  calls = [];
  routes = {};
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ key, body });
    const route = routes[key];
    if (!route) return json(404, { detail: "Not Found" });
    const out = route(body);
    return json(out.status, out.body);
  });
  box = document.createElement("div");
  document.body.appendChild(box);
  root = createRoot(box);
});

afterEach(() => {
  act(() => root.unmount());
  box.remove();
  vi.unstubAllGlobals();
});

async function flush() {
  for (let i = 0; i < 5; i++) await act(async () => new Promise((r) => setTimeout(r, 0)));
}

async function mount() {
  await act(async () => root.render(<App />));
  await flush();
}

function button(text: string): HTMLButtonElement {
  const b = [...box.querySelectorAll("button")].find((x) => x.textContent?.startsWith(text));
  if (!b) throw new Error(`нет кнопки «${text}»`);
  return b;
}

async function click(text: string) {
  await act(async () => button(text).click());
  await flush();
}

suite("приложение", () => {
  it("перезагрузка восстанавливает сессию: план, корзину и бронь", async () => {
    localStorage.setItem(SESSION_KEY, "s1");
    routes["GET /api/session/s1"] = () => ({ status: 200, body: booked });
    await mount();
    expect(box.querySelector("[aria-label='Ваучер']")?.textContent).toContain("OST-0001");
    expect(box.querySelector("[aria-label='Корзина']")?.textContent).toContain("Такси с вокзала");
    expect(box.querySelector("[role='status']")?.textContent).toContain("Бронь оформлена.");
  });

  it("неизвестная сессия: начинаем заново без ошибки", async () => {
    localStorage.setItem(SESSION_KEY, "gone");
    await mount();
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
    expect(box.querySelector("[role='alert']")).toBeNull();
    expect(box.textContent).toContain("Здесь появится доска поездки");
  });

  it("TRAP-1: ответ без модели помечен «без ИИ» в чате и на доске", async () => {
    routes["POST /api/chat"] = () => ({ status: 200, body: { ...booked, llm: false } });
    await mount();
    await click("Выходные в Казани");
    await click("Отправить");
    expect(calls.at(-1)?.body).toMatchObject({ session_id: null });
    expect(localStorage.getItem(SESSION_KEY)).toBe("s1");
    expect(box.querySelector("[role='status']")?.textContent).toContain("без ИИ");
    expect(box.querySelector(".board")?.textContent).toContain("без ИИ");
  });

  it("ошибка сервера: понятный текст без трассировки", async () => {
    routes["POST /api/chat"] = () => ({ status: 500, body: { detail: "Traceback ..." } });
    await mount();
    await click("Выходные в Казани");
    await click("Отправить");
    const alert = box.querySelector("[role='alert']")?.textContent ?? "";
    expect(alert).toContain("Попробуйте ещё раз");
    expect(alert).not.toContain("Traceback");
  });

  it("корзина: убрать и вернуть позицию, итог от сервера", async () => {
    localStorage.setItem(SESSION_KEY, "s1");
    routes["GET /api/session/s1"] = () => ({ status: 200, body: checkoutStage });
    const tr1 = checkoutStage.cart.items[0];
    routes["POST /api/cart"] = (b) => {
      const add = (b as { action: string }).action === "add";
      return { status: 200, body: add ? checkoutStage.cart : { items: [], total_rub: 0 } };
    };
    await mount();
    await click("Убрать");
    expect(calls.at(-1)).toMatchObject({
      key: "POST /api/cart",
      body: { session_id: "s1", action: "remove", offer_id: tr1.offer_id },
    });
    expect(box.querySelector("[data-testid='cart-total']")?.textContent).toContain("0 ₽");
    await click("Вернуть");
    expect(calls.at(-1)?.body).toMatchObject({ action: "add", offer_id: tr1.offer_id });
    expect(box.querySelector("[data-testid='cart-total']")?.textContent).toContain("600 ₽");
    expect(box.querySelectorAll("[aria-label='Корзина'] li").length).toBe(1);
  });

  it("подтверждение: отправляет итог, который видел пользователь, и показывает ваучер", async () => {
    localStorage.setItem(SESSION_KEY, "s1");
    let session: ChatReply = checkoutStage;
    routes["GET /api/session/s1"] = () => ({ status: 200, body: session });
    routes["POST /api/checkout"] = () => {
      session = booked;
      return { status: 200, body: booked.voucher };
    };
    await mount();
    await click("Подтвердить бронь");
    expect(calls.find((c) => c.key === "POST /api/checkout")?.body).toEqual({
      session_id: "s1",
      confirm_total_rub: 600,
    });
    expect(box.querySelector("[aria-label='Ваучер']")?.textContent).toContain("OST-0001");
  });

  it("выбор отеля на этапе сравнения — сообщение агенту порядковым словом", async () => {
    localStorage.setItem(SESSION_KEY, "s1");
    const compare = { ...structuredClone(checkoutStage), stage: "compare" as const };
    compare.plan.selected = [];
    routes["GET /api/session/s1"] = () => ({ status: 200, body: compare });
    routes["POST /api/chat"] = () => ({ status: 200, body: checkoutStage });
    await mount();
    await click("Выбрать");
    expect(calls.at(-1)?.body).toMatchObject({ message: "беру первый" });
  });

  it("хранилище браузера бросает ошибку — приложение работает, ответ показан", async () => {
    const boom = () => {
      throw new DOMException("blocked", "SecurityError");
    };
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(boom);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(boom);
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(boom);
    routes["POST /api/chat"] = () => ({ status: 200, body: checkoutStage });
    try {
      await mount();
      await click("Выходные в Казани");
      await click("Отправить");
      expect(box.querySelector("[role='alert']")).toBeNull();
      expect(box.querySelector("[role='status']")?.textContent).toContain("Бронь оформлена.");
      expect(box.querySelector("[aria-label='Корзина']")).not.toBeNull();
      await click("Новая поездка");
      expect(box.textContent).toContain("Здесь появится доска поездки");
    } finally {
      vi.restoreAllMocks();
    }
  });

  it("испорченная история чата в хранилище — берём ответ сервера", async () => {
    localStorage.setItem(SESSION_KEY, "s1");
    localStorage.setItem("trip.ui.s1", JSON.stringify({ messages: "x", removed: [1, null] }));
    routes["GET /api/session/s1"] = () => ({ status: 200, body: checkoutStage });
    await mount();
    expect(box.querySelector("[role='alert']")).toBeNull();
    expect(box.querySelector("[role='status']")?.textContent).toContain("Бронь оформлена.");
    expect(box.querySelector("[aria-label='Корзина']")?.textContent).not.toContain("Вернуть");
  });
});
