import { expect, test, type Locator, type Page } from "@playwright/test";

// Сценарий демо жюри S6 (eval/cases.json): запрос в чат, дальше только клики по карточкам
// этапа под ответом агента до ваучера, затем перезагрузка (одно окно, #434). Работает на агенте с фикстурами (без ключа LLM).
const REQUEST = "Выходные в Казани, из Москвы, 2 взрослых, 11–13 декабря, до 50 тысяч на всё.";

// #334: семья с ребёнком и младенцем — самая длинная подпись мест в строке перелёта.
const FAMILY = "Сочи из Москвы самолётом, 2 взрослых и двое детей 1 и 4 лет, 3 ночи с 20 ноября";

const EMPTY = /^\s*(—|)\s*$/;

// «41 000 ₽синтетика» → 41000.
const rub = (t: string | null) => Number((t ?? "").replace("синтетика", "").replace(/\D/g, ""));

// S6 п.2 в UI: ≥ 3 разных отеля, у каждого заполнены ≥ 5 признаков. Отели считаем по названию,
// а не по числу строк: строка может быть тарифом отеля.
async function expectHotelTable(hotels: Locator) {
  const heads = (await hotels.locator("thead th").allTextContents()).map((t) => t.trim());
  const features = ["Цена за всё", "Питание", "Отмена", "До центра", "Рейтинг"];
  for (const f of features) expect(heads, `колонка «${f}»`).toContain(f);
  const rows = hotels.locator("tbody tr");
  const n = await rows.count();
  const full = new Set<string>();
  for (let i = 0; i < n; i++) {
    const cells = (await rows.nth(i).locator("td").allTextContents()).map((t) => t.trim());
    const filled = features.filter((f) => !EMPTY.test(cells[heads.indexOf(f)] ?? ""));
    const name = (cells[heads.indexOf("Отель")] ?? "").replace(/выбран$/, "").trim();
    if (name && filled.length === features.length) full.add(name);
  }
  expect([...full].length, `отелей с 5 заполненными признаками: ${[...full].join(", ")}`).toBeGreaterThanOrEqual(3);
  // Цена несёт пометку «синтетика», условия отмены читаются словами.
  await expect(hotels.locator("tbody tr").first().locator(".price")).toContainText("синтетика");
  await expect(hotels).toContainText(/бесплатн|невозвратный/);
}

// S6 п.2 в UI: поезд и рейс показаны со временем в пути и временем прибытия (11.12, ночной
// поезд 07:10, рейс 10:30; время местное).
async function expectTransportTable(transport: Locator) {
  const heads = (await transport.locator("thead th").allTextContents()).map((t) => t.trim());
  for (const f of ["Тип", "Прибытие", "В пути"]) expect(heads, `колонка «${f}»`).toContain(f);
  const cell = async (mode: string, col: string, arrive: RegExp) => {
    const rows = transport.locator("tbody tr").filter({ hasText: mode });
    const n = await rows.count();
    expect(n, `строк транспорта «${mode}»`).toBeGreaterThan(0);
    for (let i = 0; i < n; i++) {
      const cells = (await rows.nth(i).locator("td").allTextContents()).map((t) => t.trim());
      if (arrive.test(cells[heads.indexOf("Прибытие")] ?? "")) return cells[heads.indexOf(col)];
    }
    return undefined;
  };
  for (const [mode, arrive] of [
    ["поезд", /^11\.12 07:10/],
    ["самолёт", /^11\.12 10:30/],
  ] as const) {
    const travel = await cell(mode, "В пути", arrive);
    expect(travel, `${mode}: прибытие ${arrive}`).toMatch(/^\d+ ч \d{2} мин$/);
    expect(travel).not.toBe("0 ч 00 мин");
  }
}

test("S6: запрос → сравнение → план → корзина → ваучер → перезагрузка", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });

  await page.goto("/");
  await page.getByLabel("Запрос").fill(REQUEST);
  await page.getByRole("button", { name: "Отправить" }).click();
  await expect(page.getByRole("status")).not.toBeEmpty();
  // #434: справа — путь поездки: текущий шаг «Сравнение», пройденные — с итогом из данных.
  const road = page.getByRole("complementary", { name: "Путь поездки" });
  await expect(road.locator("[aria-current='step']")).toContainText("Сравнение");
  await expect(road.locator("li.done").nth(1)).toContainText("Казань");

  // Сравнение: ≥ 3 отеля, цены с пометкой «синтетика», транспорт со временем в пути.
  const hotels = page.getByRole("region", { name: "Сравнение отелей" });
  await expect(hotels.locator("tbody tr").nth(2)).toBeVisible(); // S6: не меньше 3
  await expect(hotels.locator(".price .synth").first()).toHaveText("синтетика");
  const transport = page.getByRole("region", { name: "Сравнение транспорта" });
  await expect(transport).toContainText("ч ");
  await expectHotelTable(hotels);
  await expectTransportTable(transport);

  // Выбор другого отеля — один клик; агент собирает план с таймлайном.
  await hotels.getByRole("button", { name: /^Выбрать/ }).first().click();
  // Таймлайн виден уже на сравнении; этап сборки — кнопка «Положить в корзину» (на сравнении «Собрать план»).
  await expect(page.getByRole("button", { name: "Положить в корзину" })).toBeVisible();
  const timeline = page.getByRole("region", { name: "Таймлайн" });
  await expect(timeline).toBeVisible();

  // Выбор транспорта: рейс туда (прилёт 10:30), затем поезд туда. S6 п.3: при поезде таймлайн
  // помечает прибытие 07:10 при заезде с 14:00 и предлагает действие с ценой.
  await transport.getByRole("button", { name: /^Выбрать рейс \d+: Перелёт Москва → Казань \(KZN\), 11\.12 08:55/ }).click();
  await expect(timeline).toContainText("Прилёт");
  await expect(timeline).toContainText("10:30");
  await transport.getByRole("button", { name: /^Выбрать рейс \d+: Поезд Москва → Казань/ }).click();
  const note = timeline.getByRole("note").filter({ hasText: "Прибытие в 07:10" });
  await expect(note).toContainText("заезд с 14:00");
  await expect(note.locator("li").filter({ hasText: "Ранний заезд" }).locator(".price")).toContainText("синтетика");

  await page.getByRole("button", { name: "Положить в корзину" }).click();
  const cart = page.getByRole("region", { name: "Корзина" });
  await expect(cart.getByTestId("cart-total")).toContainText("синтетика");
  // S6 п.4: итог = сумма цен позиций, без дублей.
  const cartItems = cart.locator("ul.plain").first().locator("li");
  const sumItems = async () => (await cartItems.locator(".price").allTextContents()).reduce((a, t) => a + rub(t), 0);
  expect(rub(await cart.getByTestId("cart-total").textContent())).toBe(await sumItems());

  // Убрать и вернуть позицию: итог пересчитан сервером, без дублей.
  const before = (await cart.getByTestId("cart-total").textContent()) ?? "";
  const rows = await cart.locator("ul.plain").first().locator("li").count();
  await expect(timeline.getByText("убрано из корзины")).toHaveCount(0);
  await cart.getByRole("button", { name: /^Убрать/ }).first().click();
  await expect(cart.getByTestId("cart-total")).not.toHaveText(before);
  // События убранной позиции остаются в таймлайне, но помечены.
  await expect(timeline.getByText("убрано из корзины").first()).toBeVisible();
  await cart.getByRole("button", { name: /^Вернуть/ }).click();
  await expect(cart.getByTestId("cart-total")).toHaveText(before);
  await expect(timeline.getByText("убрано из корзины")).toHaveCount(0);
  await expect(cartItems).toHaveCount(rows);
  expect(rub(await cart.getByTestId("cart-total").textContent())).toBe(await sumItems());

  const confirm = page.getByRole("button", { name: /^Подтвердить бронь/ });
  await expect(confirm).toContainText("оплата имитируется");
  // S6 п.5–6: на кнопке итоговая сумма; бюджет 50 000 ₽ не превышен.
  const total = rub(await cart.getByTestId("cart-total").textContent());
  expect(rub(await confirm.textContent())).toBe(total);
  expect(total).toBeLessThanOrEqual(50_000);
  await expect(page.getByRole("region", { name: "Ваучер" })).toHaveCount(0);
  await confirm.click();
  const voucher = page.getByRole("region", { name: "Ваучер" });
  await expect(voucher).toContainText("Номер брони");
  // S6 п.6: даты, гости, все позиции с offer_id, итог совпадает с корзиной.
  await expect(voucher).toContainText("11.12.2026 – 13.12.2026");
  await expect(voucher).toContainText("2 взр.");
  const items = voucher.locator("ul.plain li");
  expect(await items.count()).toBe(rows);
  for (const id of await items.locator("small").allTextContents()) expect(id).toMatch(/^[a-z]+-[\w-]+$/);
  expect(rub(await voucher.locator(".total").textContent())).toBe(total);

  // Перезагрузка: бронь, корзина и план на месте.
  const booking = await voucher.locator("h2").textContent();
  await page.reload();
  await expect(page.getByRole("region", { name: "Ваучер" }).locator("h2")).toHaveText(booking ?? "");
  await expect(page.getByRole("region", { name: "Корзина" })).toBeVisible();
  // #434: после брони сравнение свёрнуто в «Варианты» под ответом и открывается кликом.
  const options = page.locator(".attach details.options");
  await expect(page.getByRole("region", { name: "Сравнение отелей" })).toBeHidden();
  await options.locator("summary").click();
  await expect(page.getByRole("region", { name: "Сравнение отелей" })).toBeVisible();
  expect(errors).toEqual([]);
});

const noPageScroll = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);

test("ширина телефона: чат с карточками этапа без горизонтальной прокрутки страницы", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByLabel("Запрос").fill(REQUEST);
  await page.getByRole("button", { name: "Отправить" }).click();
  await expect(page.getByRole("status")).not.toBeEmpty();
  expect(await noPageScroll(page)).toBe(false);
  // #434: one window — the comparison stands in the chat under the answer, no plan panel.
  const hotels = page.locator(".feed").getByRole("region", { name: "Сравнение отелей" });
  await expect(hotels).toBeVisible();
  await expect(page.getByRole("button", { name: /План/ })).toHaveCount(0);
  // The progress is a caption with a bar on a phone: «Шаг 3 из 5 · Сравнение».
  await expect(page.locator(".step-caption")).toHaveText("Шаг 3 из 5 · Сравнение");
  // A tap opens the roadmap of the trip as a sheet; ✕ closes it.
  const road = page.getByRole("complementary", { name: "Путь поездки" });
  await expect(road).toBeHidden();
  await page.locator(".step-caption").click();
  await expect(road.locator("[aria-current='step']")).toContainText("Сравнение");
  expect(await noPageScroll(page)).toBe(false);
  await page.getByRole("button", { name: "Закрыть путь поездки" }).click();
  await expect(road).toBeHidden();
  expect(await noPageScroll(page)).toBe(false);
  // TRAP-7: every price of the tables is visible with «синтетика», inside the screen, no scroll.
  for (const name of ["Сравнение отелей", "Сравнение транспорта"]) {
    const prices = page.getByRole("region", { name }).locator("tbody .price");
    const n = await prices.count();
    expect(n, `цен в «${name}»`).toBeGreaterThan(0);
    for (let i = 0; i < n; i++) {
      const p = prices.nth(i);
      await p.scrollIntoViewIfNeeded();
      await expect(p).toBeVisible();
      await expect(p.locator(".synth")).toHaveText("синтетика");
      const b = await p.boundingBox();
      expect(b && b.x >= 0 && b.x + b.width <= 390, `цена ${i + 1} в «${name}» в пределах экрана`).toBe(true);
    }
  }
  const blockScroll = await page.locator(".attach").evaluate((e) => e.scrollWidth > e.clientWidth);
  expect(blockScroll).toBe(false);
  // The parameters are one row of chips above the chat: the row scrolls, not the page.
  await expect(page.getByRole("region", { name: "Параметры поездки" })).toContainText("Казань");
  expect(await noPageScroll(page)).toBe(false);
  // Chats: a drawer by ☰.
  await page.getByRole("button", { name: "Список чатов" }).click();
  await expect(page.getByRole("button", { name: "＋ Новый чат" })).toBeVisible();
  expect(await noPageScroll(page)).toBe(false);
});

test("ширина телефона: длинная подпись мест семьи в корзине без горизонтальной прокрутки", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByLabel("Запрос").fill(FAMILY);
  await page.getByRole("button", { name: "Отправить" }).click();
  const hotels = page.getByRole("region", { name: "Сравнение отелей" });
  await hotels.getByRole("button", { name: /^Выбрать/ }).first().click();
  await expect(page.getByRole("region", { name: "Таймлайн" })).toBeVisible();
  await page.getByRole("button", { name: "Положить в корзину" }).click();
  const cart = page.getByRole("region", { name: "Корзина" });
  const label = "3 места (1 детский тариф, 1 младенец без места, 10 % тарифа)";
  const flights = cart.locator("ul.plain").first().locator("li").filter({ hasText: label });
  await expect(flights).toHaveCount(2); // туда и обратно
  expect(await noPageScroll(page)).toBe(false);
  // Подпись не обрезана: переносится, а не прячется за overflow (scrollWidth <= clientWidth).
  const box = await cart.boundingBox();
  for (const row of await flights.all()) {
    const r = await row.boundingBox();
    expect(r && box && r.x + r.width <= box.x + box.width + 1).toBe(true);
    const clipped = await row
      .locator(":scope > span:first-child")
      .evaluate((e) => e.scrollWidth > e.clientWidth);
    expect(clipped, "подпись мест обрезана").toBe(false);
  }
});

test("два чата: переключение возвращает сообщения каждого, «Новый чат» — пустой", async ({ page }) => {
  await page.goto("/");
  const ask = async (text: string) => {
    await page.getByLabel("Запрос").fill(text);
    await page.getByRole("button", { name: "Отправить" }).click();
    await expect(page.getByRole("status")).not.toBeEmpty();
  };
  const userMsgs = () => page.locator(".msg.user").allTextContents();
  const chats = page.getByRole("navigation", { name: "Прошлые чаты" });
  await ask(REQUEST);
  await expect(chats.locator(".chat-open")).toHaveCount(1);

  await page.getByRole("button", { name: "＋ Новый чат" }).click();
  expect(await userMsgs()).toEqual([]);
  await expect(page.getByText("Куда поедем?")).toBeVisible();
  await expect(page.getByRole("region", { name: "Сравнение отелей" })).toHaveCount(0);

  await ask(FAMILY);
  await expect(chats.locator(".chat-open")).toHaveCount(2);
  const kazan = chats.locator(".chat-open").filter({ hasText: "Выходные в Казани" });
  const sochi = chats.locator(".chat-open").filter({ hasText: "Сочи из Москвы" });

  await kazan.click();
  await expect(kazan).toHaveAttribute("aria-current", "page");
  await expect(page.locator(".msg.user")).toHaveText([REQUEST]);
  await expect(page.getByRole("region", { name: "Параметры поездки" })).toContainText("Казань");

  await sochi.click();
  await expect(sochi).toHaveAttribute("aria-current", "page");
  await expect(page.locator(".msg.user")).toHaveText([FAMILY]);
  await expect(page.getByRole("region", { name: "Параметры поездки" })).toContainText("Сочи");

  // Reload: the list and the open chat stay.
  await page.reload();
  await expect(chats.locator(".chat-open")).toHaveCount(2);
  await expect(page.locator(".msg.user")).toHaveText([FAMILY]);
});

// #368: на телефоне карточка транспорта — время и город в одной строке, без пустой полосы
// кнопки и без строки «Пересадки: нет». Длинный город (TRAP-2) переносится внутри карточки.
const LONG_CITY = "Из Санкт-Петербурга в Минеральные Воды, 2 взрослых, 3 ночи с 10 декабря";

async function transportLayout(transport: Locator) {
  const box = await transport.locator(".scroll").evaluate((e) => ({
    scroll: e.scrollWidth,
    client: e.clientWidth,
    right: e.getBoundingClientRect().right,
  }));
  const rows = await transport.locator("tbody tr").evaluateAll((trs) =>
    trs.map((tr) => {
      const cells = [...tr.querySelectorAll("td")];
      const ends = cells.filter((td) => td.querySelector("small"));
      const place = ends.map((td) => {
        const when = td.querySelector(".when")!.getBoundingClientRect();
        const city = td.querySelector("small")!.getClientRects()[0];
        return Math.abs(when.top - city.top);
      });
      const cityRight = Math.max(...ends.flatMap((td) => [...td.querySelector("small")!.getClientRects()].map((r) => r.right)));
      const empty = cells.filter((td) => !td.textContent?.trim()).map((td) => td.getBoundingClientRect().height);
      const noChange = cells.filter((td) => td.textContent?.trim() === "нет").map((td) => td.getBoundingClientRect().height);
      return { place, cityRight, empty, noChange };
    }),
  );
  expect(rows.length).toBeGreaterThan(0);
  expect(box.scroll, "у таблицы транспорта нет прокрутки вбок").toBeLessThanOrEqual(box.client);
  for (const [i, r] of rows.entries()) {
    expect(r.place, `строка ${i + 1}: отправление и прибытие`).toHaveLength(2);
    for (const d of r.place) expect(d, `строка ${i + 1}: город начинается в строке времени`).toBeLessThan(4);
    expect(r.cityRight, `строка ${i + 1}: город не обрезан краем карточки`).toBeLessThanOrEqual(box.right);
    for (const h of r.empty) expect(h, `строка ${i + 1}: пустая ячейка занимает место`).toBe(0);
    for (const h of r.noChange) expect(h, `строка ${i + 1}: «Пересадки: нет» видно`).toBe(0);
  }
  return rows;
}

test("ширина телефона: транспорт — время с городом в одной строке, без пустых ячеек", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByLabel("Запрос").fill(REQUEST);
  await page.getByRole("button", { name: "Отправить" }).click();
  const transport = page.getByRole("region", { name: "Сравнение транспорта" });
  await expect(transport).toBeVisible();
  // Pick a hotel: the picked rows lose the «Выбрать» button, their cell is empty.
  await page.getByRole("region", { name: "Сравнение отелей" }).getByRole("button", { name: /^Выбрать/ }).first().click();
  await expect(page.getByRole("region", { name: "Таймлайн" })).toBeVisible();
  await expect(transport.locator("tr.chosen")).not.toHaveCount(0); // the plan picked the legs
  const rows = await transportLayout(transport);
  expect(rows.some((r) => r.empty.length > 0), "есть строка без кнопки").toBe(true);
  expect(await noPageScroll(page)).toBe(false);
});

for (const width of [390, 360]) {
  test(`ширина ${width} px: длинный город в транспорте переносится, прокрутки вбок нет (TRAP-2)`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/");
    await page.getByLabel("Запрос").fill(LONG_CITY);
    await page.getByRole("button", { name: "Отправить" }).click();
    const transport = page.getByRole("region", { name: "Сравнение транспорта" });
    await expect(transport).toContainText("Минеральные Воды");
    await transportLayout(transport);
    expect(await noPageScroll(page)).toBe(false);
  });
}

test.describe("с анимацией", () => {
  test.use({ reducedMotion: "no-preference" });

  test("ответ печатается, затем видны подсказки, клик по подсказке уходит агенту", async ({ page }) => {
    await page.goto("/");
    // Record that the reply was typed: the typing node lives ≤ 1.5 s, polling could miss it.
    await page.evaluate(() => {
      const w = window as unknown as { typed: boolean; busy: boolean };
      w.typed = false;
      w.busy = false;
      new MutationObserver(() => {
        if (document.querySelector(".msg.agent .typing")) w.typed = true;
        if (document.querySelector(".msg.agent[aria-busy='true']")) w.busy = true;
      }).observe(document.body, { subtree: true, childList: true, attributes: true });
    });
    await page.getByLabel("Запрос").fill("Хочу на море");
    await page.getByRole("button", { name: "Отправить" }).click();
    const status = page.getByRole("status");
    await expect(status).not.toBeEmpty();
    // Typing ends in ≤ 1.5 s: the message is not busy, the hints appear under it.
    await expect(status).not.toHaveAttribute("aria-busy", "true", { timeout: 5000 });
    const seen = await page.evaluate(() => {
      const w = window as unknown as { typed: boolean; busy: boolean };
      return { typed: w.typed, busy: w.busy };
    });
    expect(seen.typed, "ответ печатался по словам").toBe(true);
    expect(seen.busy, "сообщение было aria-busy во время печати").toBe(true);
    const hint = page.locator(".attach").getByRole("button").first();
    await expect(hint).toBeVisible();
    const label = (await hint.getAttribute("aria-label")) ?? (await hint.textContent()) ?? "";
    await hint.click();
    await expect(page.locator(".msg.user")).toHaveCount(2);
    await expect(page.getByRole("status")).not.toBeEmpty();
    expect(label.length).toBeGreaterThan(0);
  });
});
