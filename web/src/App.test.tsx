import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe as suite, expect, it, vi } from "vitest";
import fixture from "../../tests/fixtures/chat_reply.json";
import { App, SESSION_KEY } from "./App";
import { VERBS } from "./brand";
import { HISTORY_KEY } from "./history";
import { SWITCHES_KEY } from "./Sources";
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
  const b = [...box.querySelectorAll("button")].find(
    (x) => x.textContent?.startsWith(text) || x.getAttribute("aria-label") === text,
  );
  if (!b) throw new Error(`нет кнопки «${text}»`);
  return b;
}

async function type(label: string, value: string) {
  const input = [...box.querySelectorAll("label")]
    .find((l) => l.textContent?.startsWith(label))
    ?.querySelector("input");
  if (!input) throw new Error(`нет поля «${label}»`);
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
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

  it("#434: справа путь поездки вместо панели плана; подпись шага открывает его листом", async () => {
    localStorage.setItem(SESSION_KEY, "s1");
    routes["GET /api/session/s1"] = () => ({ status: 200, body: checkoutStage });
    await mount();
    const road = box.querySelector("aside#roadmap[aria-label='Путь поездки']");
    expect(road?.querySelector("[aria-current='step'] .label")?.textContent).toBe("Корзина");
    expect(road?.querySelector("[aria-label='Сравнение отелей']")).toBeNull(); // no plan data on the right
    expect(box.querySelector("header.top .road, header.top .stage")).toBeNull();
    await click("Шаг 4 из 5 · Корзина");
    expect(box.querySelector(".app")?.classList.contains("road-open")).toBe(true);
    await click("Закрыть путь поездки");
    expect(box.querySelector(".app")?.classList.contains("road-open")).toBe(false);
  });

  it("индикатор «думает» чередуется между ответами: банан, потом самолётик", async () => {
    let answer: (v: Response) => void = () => {};
    vi.stubGlobal("fetch", () => new Promise<Response>((r) => (answer = r)));
    await mount();
    await click("Выходные в Казани");
    await click("Отправить");
    expect(box.querySelector(".think.banana .mascot.thinking")).not.toBeNull();
    await act(async () => answer(json(200, checkoutStage)));
    await flush();
    const area = box.querySelector("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(area, "Ещё вариант");
      area.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await click("Отправить");
    expect(box.querySelector(".think.plane .route")).not.toBeNull();
    await act(async () => answer(json(200, checkoutStage)));
    await flush();
  });

  it("неизвестная сессия: начинаем заново без ошибки", async () => {
    localStorage.setItem(SESSION_KEY, "gone");
    await mount();
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
    expect(box.querySelector("[role='alert']")).toBeNull();
    expect(box.textContent).toContain("Куда поедем?");
  });

  it("TRAP-1: ответ без модели помечен «без ИИ» значком и в блоке этапа, текст ответа — без хвоста", async () => {
    routes["POST /api/chat"] = () => ({ status: 200, body: { ...booked, llm: false } });
    await mount();
    await click("Выходные в Казани");
    await click("Отправить");
    expect(calls.at(-1)?.body).toMatchObject({ session_id: null });
    expect(localStorage.getItem(SESSION_KEY)).toBe("s1");
    // #434: the badge says it once; the label after every answer is not repeated in the bubble.
    expect(box.querySelector("aside .badge-noai")?.textContent).toContain("Без ИИ");
    expect(box.querySelector(".board")?.textContent).toContain("без ИИ");
    expect(box.querySelector("[role='status'] .b")?.textContent).toBe("Бронь оформлена.");
  });

  it("TRAP-1: пометка «Без ИИ» видна до первого сообщения, если /api/mode сообщает llm=false", async () => {
    localStorage.setItem(SWITCHES_KEY, JSON.stringify({ real_data: false, llm: true }));
    routes["GET /api/mode"] = () => ({ status: 200, body: { llm: false, real_data: false } });
    await mount();
    expect(box.querySelector("aside .badge-noai")?.textContent).toContain("Без ИИ");
  });

  it("#490: модель есть, но GigaChat выключен (по умолчанию) — пометка «Без ИИ»", async () => {
    routes["GET /api/mode"] = () => ({ status: 200, body: { llm: true, real_data: true } });
    await mount();
    expect(box.querySelector("aside .badge-noai")?.textContent).toContain("Без ИИ");
  });

  it("с моделью и включённым GigaChat пометки до первого сообщения нет", async () => {
    localStorage.setItem(SWITCHES_KEY, JSON.stringify({ real_data: false, llm: true }));
    routes["GET /api/mode"] = () => ({ status: 200, body: { llm: true, real_data: false } });
    await mount();
    expect(box.querySelector(".badge-noai")).toBeNull();
  });

  it("#490: переключатель уходит с запросом и хранится в браузере; без ключа — false", async () => {
    localStorage.setItem(SWITCHES_KEY, JSON.stringify({ real_data: false, llm: true }));
    routes["GET /api/mode"] = () => ({ status: 200, body: { llm: false, real_data: true } });
    routes["POST /api/chat"] = () => ({ status: 200, body: checkoutStage });
    await mount();
    const real = box.querySelector<HTMLInputElement>("aside input[role='switch']");
    await act(async () => real?.click());
    expect(JSON.parse(localStorage.getItem(SWITCHES_KEY) ?? "{}")).toEqual({ real_data: true, llm: true });
    await click("Выходные в Казани");
    await click("Отправить");
    expect(calls.at(-1)?.body).toMatchObject({ real_data: true, llm: false });
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

  it("#187: после «В корзину» из апселла чат называет актуальные число позиций и сумму", async () => {
    localStorage.setItem(SESSION_KEY, "s1");
    const offer = (id: string) => checkoutStage.plan.offers.find((o) => o.offer_id === id)!;
    const before = [...checkoutStage.cart.items, offer("h1")];
    const after = [...before, offer("x1")];
    // total_rub of the stored cart is stale (1): the chat line must use the total the server returns
    const session = { ...structuredClone(checkoutStage), cart: { items: before, total_rub: 1 } };
    const total = after.reduce((n, o) => n + o.price_rub, 0);
    routes["GET /api/session/s1"] = () => ({ status: 200, body: session });
    routes["POST /api/cart"] = () => ({ status: 200, body: { items: after, total_rub: total } });
    await mount();
    await click("В корзину");
    const sum = String(total).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
    const last = [...box.querySelectorAll(".msg.agent")].at(-1)?.textContent;
    expect(last).toBe(`Теперь в корзине ${after.length} поз. на ${sum} ₽ (синтетика).`);
  });

  it("#161: корзина изменилась, а план не обновился — ошибка вместо тихого устаревшего плана", async () => {
    localStorage.setItem(SESSION_KEY, "s1");
    let reads = 0;
    routes["GET /api/session/s1"] = () => (++reads === 1 ? { status: 200, body: checkoutStage } : { status: 500 });
    routes["POST /api/cart"] = () => ({ status: 200, body: { items: [], total_rub: 0 } });
    await mount();
    await click("Убрать");
    expect(box.querySelector("[role='alert']")?.textContent).toContain("Не удалось обновить план");
  });

  it("#161: бронь оформлена, а план не обновился — ошибка про перезагрузку", async () => {
    localStorage.setItem(SESSION_KEY, "s1");
    let reads = 0;
    routes["GET /api/session/s1"] = () => (++reads === 1 ? { status: 200, body: checkoutStage } : { status: 500 });
    routes["POST /api/checkout"] = () => ({ status: 200, body: booked.voucher });
    await mount();
    await click("Подтвердить бронь");
    expect(box.querySelector("[role='alert']")?.textContent).toContain("перезагрузите страницу");
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

  it("#367: имя и контакт из формы уходят в запрос как guest", async () => {
    localStorage.setItem(SESSION_KEY, "s1");
    routes["GET /api/session/s1"] = () => ({ status: 200, body: checkoutStage });
    routes["POST /api/checkout"] = () => ({ status: 200, body: booked.voucher });
    await mount();
    await type("Имя гостя", "  Иван Петров ");
    await type("Контакт", "ivan@example.com");
    await click("Подтвердить бронь");
    expect(calls.find((c) => c.key === "POST /api/checkout")?.body).toEqual({
      session_id: "s1",
      confirm_total_rub: 600,
      guest: { name: "Иван Петров", contact: "ivan@example.com" },
    });
  });

  it("#367: контакт без имени — кнопка выключена и просит имя", async () => {
    localStorage.setItem(SESSION_KEY, "s1");
    routes["GET /api/session/s1"] = () => ({ status: 200, body: checkoutStage });
    await mount();
    await type("Контакт", "ivan@example.com");
    expect(button("Подтвердить бронь").disabled).toBe(true);
    expect(box.querySelector(".guest-form")?.textContent).toContain("Укажите имя");
    await type("Имя гостя", "Иван");
    expect(button("Подтвердить бронь").disabled).toBe(false);
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
      await click("＋ Новый чат");
      expect(box.textContent).toContain("Куда поедем?");
      expect(box.querySelector("[aria-label='Корзина']")).toBeNull();
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

  it("подписи про хранение чатов и синтетику спрятаны под «?» и есть в разметке", async () => {
    await mount();
    const hints = [...box.querySelectorAll("details.hint")] as HTMLDetailsElement[];
    expect(hints.map((h) => h.open)).toEqual([false, false]);
    expect(hints.map((h) => h.textContent)).toEqual([
      "?Список чатов хранится только в этом браузере.",
      "?Цены и рейсы — синтетика. Оплата имитирована.",
    ]);
    // one click opens a note
    await act(async () => hints[1].querySelector("summary")!.click());
    expect(hints[1].open).toBe(true);
  });

  it("подвал: атрибуция OpenStreetMap (ODbL) есть и без поездки, под «Данные ?» в один клик", async () => {
    await mount();
    const foot = box.querySelector("footer[aria-label='Источники данных']");
    expect(foot?.textContent).toContain("© участники OpenStreetMap");
    const link = foot?.querySelector("a") as HTMLAnchorElement;
    expect(link.textContent).toBe("ODbL");
    expect(link.href).toBe("https://www.openstreetmap.org/copyright");
    expect(foot?.textContent).toContain("синтетика");
    // hidden under «Данные ?» until opened, one click away
    const more = foot?.querySelector("details");
    expect(more?.open).toBe(false);
    expect(more?.querySelector("summary")?.textContent).toContain("Данные");
  });

  it("TRAP-7: подвал называет всю синтетику и оценки команды", async () => {
    await mount();
    const foot = (box.querySelector("footer[aria-label='Источники данных']")?.textContent ?? "").replace(/\s+/g, " ");
    for (const w of ["Цены, рейсы, тарифы, трансферы и рейтинги — синтетика", "отели вне снимка OSM", "Климат и время в пути — оценки команды", "Оплата имитируется"])
      expect(foot).toContain(w);
  });
});

suite("список чатов слева", () => {
  // Each new chat gets its own session id; a known id returns the same session.
  function twoSessions() {
    let n = 0;
    const sessions: Record<string, ChatReply> = {};
    routes["POST /api/chat"] = (b) => {
      const { session_id } = b as { session_id: string | null };
      const id = session_id ?? `c${++n}`;
      sessions[id] = { ...structuredClone(checkoutStage), session_id: id };
      return { status: 200, body: sessions[id] };
    };
    for (const id of ["c1", "c2"])
      routes[`GET /api/session/${id}`] = () =>
        sessions[id] ? { status: 200, body: sessions[id] } : { status: 404 };
  }

  async function say(text: string) {
    const input = box.querySelector("textarea[aria-label='Запрос']") as HTMLTextAreaElement;
    const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
    await act(async () => {
      set.call(input, text);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await click("Отправить");
  }

  const titles = () => [...box.querySelectorAll(".chat-list .chat-open")].map((b) => b.textContent);
  const userMsgs = () => [...box.querySelectorAll(".msg.user")].map((m) => m.textContent);

  async function open(title: string) {
    const b = [...box.querySelectorAll(".chat-list .chat-open")].find((x) => x.textContent === title);
    if (!b) throw new Error(`нет чата «${title}»`);
    await act(async () => (b as HTMLButtonElement).click());
    await flush();
  }

  it("два чата: переключение возвращает сообщения каждого, «Новый чат» пустой", async () => {
    twoSessions();
    await mount();
    await say("Казань на выходные");
    await click("＋ Новый чат");
    expect(userMsgs()).toEqual([]);
    expect(box.textContent).toContain("Куда поедем?");
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
    await say("Сочи с детьми");
    expect(calls.filter((c) => c.key === "POST /api/chat").map((c) => c.body)).toEqual([
      { session_id: null, message: "Казань на выходные", real_data: false, llm: false },
      { session_id: null, message: "Сочи с детьми", real_data: false, llm: false },
    ]);
    expect(titles()).toEqual(["Сочи с детьми", "Казань на выходные"]);
    await open("Казань на выходные");
    expect(userMsgs()).toEqual(["Казань на выходные"]);
    expect(localStorage.getItem(SESSION_KEY)).toBe("c1");
    expect(box.querySelector(".chat-list li.on")?.textContent).toContain("Казань на выходные");
    await say("а подешевле");
    expect(calls.at(-1)?.body).toMatchObject({ session_id: "c1", message: "а подешевле" });
    await open("Сочи с детьми");
    expect(userMsgs()).toEqual(["Сочи с детьми"]);
    expect(box.querySelector("[aria-label='Корзина']")).not.toBeNull();
  });

  it("перезагрузка: список чатов на месте", async () => {
    twoSessions();
    await mount();
    await say("Казань на выходные");
    act(() => root.unmount());
    root = createRoot(box);
    await mount();
    expect(titles()).toEqual(["Казань на выходные"]);
    expect(userMsgs()).toEqual(["Казань на выходные"]);
  });

  it("чат, которого нет на сервере, убирается из списка с заметкой", async () => {
    localStorage.setItem(HISTORY_KEY, JSON.stringify([{ session_id: "gone", title: "Старая поездка", updated_at: 1 }]));
    await mount();
    await open("Старая поездка");
    expect(titles()).toEqual([]);
    expect(JSON.parse(localStorage.getItem(HISTORY_KEY)!)).toEqual([]);
    expect(box.querySelector("[role='note']")?.textContent).toContain("«Старая поездка» больше не хранится");
    expect(box.querySelector("[role='alert']")).toBeNull();
  });

  it("переименование и удаление чата — только в браузере", async () => {
    twoSessions();
    await mount();
    await say("Казань на выходные");
    await act(async () => button("✎").click());
    const input = box.querySelector("input[aria-label='Название чата']") as HTMLInputElement;
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    await act(async () => {
      set.call(input, "Татарстан");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => input.form!.requestSubmit());
    expect(titles()).toEqual(["Татарстан"]);
    expect(JSON.parse(localStorage.getItem(HISTORY_KEY)!)[0].title).toBe("Татарстан");
    const n = calls.length;
    await act(async () => (box.querySelector("button[aria-label='Удалить чат «Татарстан»']") as HTMLButtonElement).click());
    await flush();
    expect(titles()).toEqual([]);
    expect(userMsgs()).toEqual([]);
    expect(localStorage.getItem(SESSION_KEY)).toBeNull();
    expect(localStorage.getItem("trip.ui.c1")).toBeNull();
    expect(calls.length).toBe(n); // no server call
  });

  it("хранилище бросает ошибку — список чатов живёт в памяти, переключение работает", async () => {
    const boom = () => {
      throw new DOMException("blocked", "SecurityError");
    };
    twoSessions();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(boom);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(boom);
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(boom);
    try {
      await mount();
      await say("Казань на выходные");
      await click("＋ Новый чат");
      await say("Сочи с детьми");
      expect(titles()).toEqual(["Сочи с детьми", "Казань на выходные"]);
      await open("Казань на выходные");
      expect(box.querySelector("[role='alert']")).toBeNull();
      expect(box.querySelector("[aria-label='Корзина']")).not.toBeNull();
      expect(calls.at(-1)?.key).toBe("GET /api/session/c1");
    } finally {
      vi.restoreAllMocks();
    }
  });

  it("поиск по чатам оставляет только подходящие названия", async () => {
    const now = Date.now();
    localStorage.setItem(
      HISTORY_KEY,
      JSON.stringify([
        { session_id: "a", title: "Казань на выходные", updated_at: now },
        { session_id: "b", title: "Сочи с детьми", updated_at: now - 1 },
      ]),
    );
    await mount();
    const search = box.querySelector("input[aria-label='Поиск по чатам']") as HTMLInputElement;
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    await act(async () => {
      set.call(search, "сочи");
      search.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(titles()).toEqual(["Сочи с детьми"]);
    expect(box.querySelector(".group")?.textContent).toBe("Сегодня");
  });
});

suite("чат как в мессенджере", () => {
  it("пока агент отвечает — маскот и глагол из списка, после ответа индикатора нет", async () => {
    let answer: (v: Response) => void = () => {};
    vi.stubGlobal("fetch", () => new Promise<Response>((r) => (answer = r)));
    await mount();
    await click("Выходные в Казани");
    await click("Отправить");
    const think = box.querySelector("[aria-label='Агент думает']");
    expect(VERBS).toContain(think?.querySelector(".verb")?.textContent);
    await act(async () => answer(json(200, checkoutStage)));
    await flush();
    expect(box.querySelector("[aria-label='Агент думает']")).toBeNull();
    expect(box.querySelector("[role='status']")?.textContent).toContain("Бронь оформлена.");
  });

  it("TRAP-1: ответ без модели — пометка «Без ИИ» в списке чатов", async () => {
    localStorage.setItem(SWITCHES_KEY, JSON.stringify({ real_data: false, llm: true }));
    routes["GET /api/mode"] = () => ({ status: 200, body: { llm: true, real_data: false } });
    routes["POST /api/chat"] = () => ({ status: 200, body: { ...checkoutStage, llm: false } });
    await mount();
    expect(box.querySelector(".badge-noai")).toBeNull();
    await click("Выходные в Казани");
    await click("Отправить");
    expect(box.querySelector("aside[aria-label='Чаты'] .badge-noai")?.textContent).toContain("Без ИИ");
  });

  it("этап вдохновения: карточки направлений в чате с метками из данных, выбор — сообщением", async () => {
    const r = { ...structuredClone(checkoutStage), stage: "inspire" as const };
    const [d] = r.plan.destinations;
    r.plan.destinations = [d, { ...d, name: "Сочи", est_total_rub: d.est_total_rub + 1000, travel_hours: d.travel_hours + 1 }];
    r.plan.spec.budget_rub = d.est_total_rub + 500;
    routes["POST /api/chat"] = (b) =>
      (b as { message: string }).message === d.name
        ? { status: 200, body: checkoutStage }
        : { status: 200, body: r };
    await mount();
    await click("Выходные в Казани");
    await click("Отправить");
    const cards = [...box.querySelectorAll(".feed [aria-label='Направления'] .dest")];
    expect(cards).toHaveLength(2);
    // #434: one window — the cards stand under the answer, there is no plan panel.
    expect(box.querySelectorAll("[aria-label='Направления']")).toHaveLength(1);
    expect(box.querySelector("#plan, .plan-toggle, .plan-sheet-open")).toBeNull();
    expect(cards[0].querySelector(".tag.best")?.textContent).toBe("лучшая цена");
    expect(cards[1].querySelector(".tag.warn")?.textContent).toBe("дороже бюджета");
    expect(cards[0].querySelector(".tag.warn")).toBeNull();
    for (const c of cards) expect(c.querySelector(".price .synth")?.textContent).toBe("синтетика");
    await act(async () => (cards[0].querySelector("button") as HTMLButtonElement).click());
    await flush();
    expect(calls.at(-1)?.body).toMatchObject({ message: d.name });
  });

  it("#458: вопрос-чипы, что повторяют имена карточек, скрыты; другие вопросы остаются", async () => {
    const r = { ...structuredClone(checkoutStage), stage: "inspire" as const };
    const [d] = r.plan.destinations;
    r.plan.destinations = [d, { ...d, name: "Сочи" }];
    r.questions = [
      { text: "Куда поедем?", options: [d.name, "Сочи"] },
      { text: "Сколько вас?", options: ["двое", "трое"] },
    ];
    routes["POST /api/chat"] = () => ({ status: 200, body: r });
    await mount();
    await click("Выходные в Казани");
    await click("Отправить");
    const q = [...box.querySelectorAll(".questions p")].map((p) => p.textContent);
    expect(q).toEqual(["Сколько вас?"]);
  });

  it("#464: варианты прошлой подборки остаются под своим ответом и после перезагрузки", async () => {
    const first = { ...structuredClone(checkoutStage), stage: "inspire" as const };
    const [d] = first.plan.destinations;
    first.plan.destinations = [d, { ...d, name: "Кировск" }];
    const second = structuredClone(first);
    second.plan.destinations = [{ ...d, name: "Теберда" }, { ...d, name: "Губа" }];
    let n = 0;
    routes["POST /api/chat"] = () => ({ status: 200, body: n++ ? second : first });
    routes[`GET /api/session/${first.session_id}`] = () => ({ status: 200, body: second });
    await mount();
    await click("Выходные в Казани");
    await click("Отправить");
    const area = box.querySelector("textarea")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(area, "ещё");
      area.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await click("Отправить");
    const names = () => [...box.querySelectorAll(".feed .dest h3, .feed .dest b")].map((e) => e.textContent ?? "");
    const before = names().join(" ");
    expect(before).toContain("Кировск");
    expect(before).toContain("Теберда");
    // reload: a new app on the same storage
    act(() => root.unmount());
    root = createRoot(box);
    await mount();
    const after = names().join(" ");
    expect(after).toContain("Кировск");
    expect(after).toContain("Теберда");
  });

  it("#458: на select чипы-вопросы остаются: у карточек там нет «Выбрать»", async () => {
    const r = { ...structuredClone(checkoutStage), stage: "select" as const };
    const [d] = r.plan.destinations;
    r.plan.destinations = [d, { ...d, name: "Сочи" }];
    r.questions = [{ text: "Какой город берём?", options: [d.name, "Сочи"] }];
    routes["POST /api/chat"] = () => ({ status: 200, body: r });
    await mount();
    await click("Выходные в Казани");
    await click("Отправить");
    expect([...box.querySelectorAll(".questions p")].map((p) => p.textContent)).toEqual(["Какой город берём?"]);
  });

  it("строки «Допущения» и «Не проверяю» ответа — приглушённой строкой", async () => {
    const reply = "Нашёл варианты.\nДопущения: даты не названы.\nНе проверяю по данным: детская анимация.";
    routes["POST /api/chat"] = () => ({ status: 200, body: { ...checkoutStage, reply } });
    await mount();
    await click("Выходные в Казани");
    await click("Отправить");
    const lines = [...box.querySelectorAll("[role='status'] p")].map((p) => [p.className, p.textContent]);
    expect(lines).toEqual([
      ["", "Нашёл варианты."],
      ["aside", "Допущения: даты не названы."],
      ["aside", "Не проверяю по данным: детская анимация."],
    ]);
  });

  it("пустой чат: без серого текста-дубля, примеры стоят прямо над полем ввода", async () => {
    await mount();
    expect(box.textContent).not.toContain("Опишите поездку одним сообщением. Я подберу");
    expect(box.querySelector(".hello .chip")).toBeNull();
    const starters = box.querySelector("[aria-label='Примеры для начала']");
    expect(starters?.querySelectorAll("button")).toHaveLength(2);
    expect(starters?.nextElementSibling?.matches("form.composer")).toBe(true);
    await click("Выходные в Казани");
    const input = box.querySelector("textarea[aria-label='Запрос']") as HTMLTextAreaElement;
    expect(input.value).toContain("Выходные в Казани");
    routes["POST /api/chat"] = () => ({ status: 200, body: checkoutStage });
    await click("Отправить");
    expect(box.querySelector("[aria-label='Примеры для начала']")).toBeNull();
  });

  it("чат не открылся (сеть) — на экране остаётся прежний чат, сохранённая сессия не меняется", async () => {
    const now = Date.now();
    localStorage.setItem(SESSION_KEY, "s1");
    localStorage.setItem(
      HISTORY_KEY,
      JSON.stringify([
        { session_id: "s1", title: "Казань", updated_at: now },
        { session_id: "s2", title: "Сочи", updated_at: now - 1 },
      ]),
    );
    localStorage.setItem("trip.ui.s1", JSON.stringify({ messages: [{ role: "user", text: "Казань" }], removed: [] }));
    routes["GET /api/session/s1"] = () => ({ status: 200, body: checkoutStage });
    routes["GET /api/session/s2"] = () => ({ status: 500 });
    await mount();
    const sochi = [...box.querySelectorAll(".chat-open")].find((b) => b.textContent === "Сочи") as HTMLButtonElement;
    await act(async () => sochi.click());
    await flush();
    expect(box.querySelector("[role='alert']")?.textContent).toContain("Не удалось открыть чат.");
    expect([...box.querySelectorAll(".msg.user")].map((m) => m.textContent)).toEqual(["Казань"]);
    expect(box.querySelector("[aria-label='Корзина']")).not.toBeNull();
    expect(localStorage.getItem(SESSION_KEY)).toBe("s1");
    expect(box.querySelector(".chat-list li.on")?.textContent).toContain("Казань");
  });

  it("чата нет на сервере — убран из списка, прежний чат на экране и в сохранённой сессии", async () => {
    const now = Date.now();
    localStorage.setItem(SESSION_KEY, "s1");
    localStorage.setItem(
      HISTORY_KEY,
      JSON.stringify([
        { session_id: "s1", title: "Казань", updated_at: now },
        { session_id: "gone", title: "Старая", updated_at: now - 1 },
      ]),
    );
    routes["GET /api/session/s1"] = () => ({ status: 200, body: checkoutStage });
    await mount();
    const old = [...box.querySelectorAll(".chat-open")].find((b) => b.textContent === "Старая") as HTMLButtonElement;
    await act(async () => old.click());
    await flush();
    expect(box.querySelector("[role='note']")?.textContent).toContain("«Старая» больше не хранится");
    expect(localStorage.getItem(SESSION_KEY)).toBe("s1");
    expect(box.querySelector("[aria-label='Корзина']")).not.toBeNull();
    expect([...box.querySelectorAll(".chat-open")].map((b) => b.textContent)).toEqual(["Казань"]);
  });
});
