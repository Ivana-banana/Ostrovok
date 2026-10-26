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

// A cart edit is followed by a session read (the plan may change), so the last call is a GET.
function lastCartCall() {
  return calls.filter((c) => c.key === "POST /api/cart").at(-1);
}

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
    expect(lastCartCall()).toMatchObject({
      key: "POST /api/cart",
      body: { session_id: "s1", action: "remove", offer_id: tr1.offer_id },
    });
    expect(box.querySelector("[data-testid='cart-total']")?.textContent).toContain("0 ₽");
    await click("Вернуть");
    expect(lastCartCall()?.body).toMatchObject({ action: "add", offer_id: tr1.offer_id });
    expect(box.querySelector("[data-testid='cart-total']")?.textContent).toContain("600 ₽");
    expect(box.querySelectorAll("[aria-label='Корзина'] li").length).toBe(1);
  });

  it("корзина: «Вернуть» отель возвращает и его услуги", async () => {
    localStorage.setItem(SESSION_KEY, "s1");
    const hotel = checkoutStage.plan.offers.find((o) => o.kind === "hotel")!;
    const extra = checkoutStage.plan.offers.find((o) => o.kind === "extra")!;
    const items = [hotel, extra];
    const full = { items, total_rub: hotel.price_rub + extra.price_rub };
    const session = { ...structuredClone(checkoutStage), cart: full };
    routes["GET /api/session/s1"] = () => ({ status: 200, body: session });
    let inCart = items;
    routes["POST /api/cart"] = (b) => {
      const { action, offer_id } = b as { action: string; offer_id: string };
      const offer = items.find((o) => o.offer_id === offer_id)!;
      inCart = action === "add" ? [...inCart, offer] : [];
      return { status: 200, body: { items: inCart, total_rub: inCart.reduce((n, o) => n + o.price_rub, 0) } };
    };
    await mount();
    await click("Убрать");
    expect(box.querySelectorAll("button[aria-label^='Вернуть']").length).toBe(1);
    await click("Вернуть");
    const added = calls.filter((c) => c.key === "POST /api/cart").map((c) => c.body);
    expect(added.slice(1)).toEqual([
      { session_id: "s1", action: "add", offer_id: hotel.offer_id },
      { session_id: "s1", action: "add", offer_id: extra.offer_id },
    ]);
    expect(box.querySelector("[data-testid='cart-total']")?.textContent).toContain(
      String(full.total_rub).replace(/\B(?=(\d{3})+(?!\d))/g, " "),
    );
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

  it("подтверждение: 409 показывает текст сервера и не создаёт ваучер", async () => {
    localStorage.setItem(SESSION_KEY, "s1");
    routes["GET /api/session/s1"] = () => ({ status: 200, body: checkoutStage });
    routes["POST /api/checkout"] = () => ({
      status: 409,
      body: { detail: "Итог корзины 700 ₽, а вы подтверждаете 600 ₽." },
    });
    await mount();
    await click("Подтвердить бронь");
    expect(box.querySelector("[role='alert']")?.textContent).toContain("Итог корзины 700 ₽");
    expect(box.querySelector("[aria-label='Ваучер']")).toBeNull();
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

  it("выбор транспорта на этапе сравнения — сообщение агенту «возьми рейс N»", async () => {
    localStorage.setItem(SESSION_KEY, "s1");
    const compare = { ...structuredClone(checkoutStage), stage: "compare" as const };
    compare.plan.selected = [];
    routes["GET /api/session/s1"] = () => ({ status: 200, body: compare });
    routes["POST /api/chat"] = () => ({ status: 200, body: checkoutStage });
    await mount();
    const table = box.querySelector("[aria-label='Сравнение транспорта']");
    const pick = [...(table?.querySelectorAll("button") ?? [])][0] as HTMLButtonElement;
    expect(pick.getAttribute("aria-label")).toContain("Выбрать рейс 1");
    await act(async () => pick.click());
    await flush();
    expect(calls.at(-1)?.body).toMatchObject({ message: "возьми рейс 1" });
  });

  it("выбранный рейс и этап корзины — без кнопки выбора транспорта", async () => {
    localStorage.setItem(SESSION_KEY, "s1");
    routes["GET /api/session/s1"] = () => ({ status: 200, body: checkoutStage });
    await mount();
    const table = box.querySelector("[aria-label='Сравнение транспорта']");
    expect(table?.querySelectorAll("button").length).toBe(0);
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

  it("S9: сдвиг чипа отправляет агенту сообщение с полным порядком приоритетов", async () => {
    localStorage.setItem(SESSION_KEY, "s1");
    const compare = { ...structuredClone(checkoutStage), stage: "compare" as const };
    compare.plan.spec.priorities = ["price", "cancellation"];
    routes["GET /api/session/s1"] = () => ({ status: 200, body: compare });
    const after = structuredClone(compare);
    after.plan.spec.priorities = ["cancellation", "price"];
    routes["POST /api/chat"] = () => ({ status: 200, body: after });
    await mount();
    const down = box.querySelector("button[aria-label='Опустить приоритет цена']") as HTMLButtonElement;
    await act(async () => down.click());
    await flush();
    expect(calls.at(-1)).toMatchObject({
      key: "POST /api/chat",
      body: { session_id: "s1", message: "Мои приоритеты по важности: отмена, цена." },
    });
    const chips = [...box.querySelectorAll("[aria-label='Приоритеты'] .chip span")].map((c) => c.textContent);
    expect(chips).toEqual(["1. отмена", "2. цена"]);
  });

  it("подвал: атрибуция OpenStreetMap (ODbL) видна и без поездки", async () => {
    await mount();
    const foot = box.querySelector("footer[aria-label='Источники данных']");
    expect(foot?.textContent).toContain("© участники OpenStreetMap");
    const link = foot?.querySelector("a") as HTMLAnchorElement;
    expect(link.textContent).toBe("ODbL");
    expect(link.href).toBe("https://www.openstreetmap.org/copyright");
    expect(foot?.textContent).toContain("синтетика");
  });

  it("TRAP-7: подвал называет всю синтетику и оценки команды", async () => {
    await mount();
    const foot = (box.querySelector("footer[aria-label='Источники данных']")?.textContent ?? "").replace(/\s+/g, " ");
    for (const w of ["Цены, рейсы, тарифы, трансферы и рейтинги — синтетика", "отели вне снимка OSM", "Климат и время в пути — оценки команды", "Оплата имитируется"])
      expect(foot).toContain(w);
  });
});
