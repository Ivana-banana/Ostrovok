import { expect, test, type Locator } from "@playwright/test";

// Сценарий демо жюри S6 (eval/cases.json): запрос в чат, дальше только клики на доске
// поездки до ваучера, затем перезагрузка. Работает на агенте с фикстурами (без ключа LLM).
const REQUEST = "Выходные в Казани, из Москвы, 2 взрослых, 11–13 декабря, до 50 тысяч на всё.";

const EMPTY = /^\s*(—|)\s*$/;

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
  await expect(page.getByRole("region", { name: "Таймлайн" })).toBeVisible();

  await page.getByRole("button", { name: "Положить в корзину" }).click();
  const cart = page.getByRole("region", { name: "Корзина" });
  await expect(cart.getByTestId("cart-total")).toContainText("синтетика");

  // Убрать и вернуть позицию: итог пересчитан сервером, без дублей.
  const before = (await cart.getByTestId("cart-total").textContent()) ?? "";
  const rows = await cart.locator("ul.plain").first().locator("li").count();
  const timeline = page.getByRole("region", { name: "Таймлайн" });
  await expect(timeline.getByText("убрано из корзины")).toHaveCount(0);
  await cart.getByRole("button", { name: /^Убрать/ }).first().click();
  await expect(cart.getByTestId("cart-total")).not.toHaveText(before);
  // События убранной позиции остаются в таймлайне, но помечены.
  await expect(timeline.getByText("убрано из корзины").first()).toBeVisible();
  await cart.getByRole("button", { name: /^Вернуть/ }).click();
  await expect(cart.getByTestId("cart-total")).toHaveText(before);
  await expect(timeline.getByText("убрано из корзины")).toHaveCount(0);
  await expect(cart.locator("ul.plain").first().locator("li")).toHaveCount(rows);

  const confirm = page.getByRole("button", { name: /^Подтвердить бронь/ });
  await expect(confirm).toContainText("оплата имитируется");
  await expect(page.getByRole("region", { name: "Ваучер" })).toHaveCount(0);
  await confirm.click();
  const voucher = page.getByRole("region", { name: "Ваучер" });
  await expect(voucher).toContainText("Номер брони");

  // Перезагрузка: бронь, корзина и план на месте.
  const booking = await voucher.locator("h2").textContent();
  await page.reload();
  await expect(page.getByRole("region", { name: "Ваучер" }).locator("h2")).toHaveText(booking ?? "");
  await expect(page.getByRole("region", { name: "Корзина" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Сравнение отелей" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("ширина телефона: чат и доска без горизонтальной прокрутки страницы", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByLabel("Запрос").fill(REQUEST);
  await page.getByRole("button", { name: "Отправить" }).click();
  await expect(page.getByRole("region", { name: "Сравнение отелей" })).toBeVisible();
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(wide).toBe(false);
});
