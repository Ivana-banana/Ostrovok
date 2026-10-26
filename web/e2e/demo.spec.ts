import { expect, test } from "@playwright/test";

// Сценарий демо жюри S6 (eval/cases.json): запрос в чат, дальше только клики на доске
// поездки до ваучера, затем перезагрузка. Работает на агенте с фикстурами (без ключа LLM).
const REQUEST = "Выходные в Казани, из Москвы, 2 взрослых, 11–13 декабря, до 50 тысяч на всё.";

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
  await expect(page.getByRole("region", { name: "Сравнение транспорта" })).toContainText("ч ");

  // Выбор другого отеля — один клик; агент собирает план с таймлайном.
  await hotels.getByRole("button", { name: /^Выбрать/ }).first().click();
  await expect(page.getByRole("region", { name: "Таймлайн" })).toBeVisible();

  await page.getByRole("button", { name: "Положить в корзину" }).click();
  const cart = page.getByRole("region", { name: "Корзина" });
  await expect(cart.getByTestId("cart-total")).toContainText("синтетика");

  // Убрать и вернуть позицию: итог пересчитан сервером, без дублей.
  const before = (await cart.getByTestId("cart-total").textContent()) ?? "";
  const rows = await cart.locator("ul.plain").first().locator("li").count();
  await cart.getByRole("button", { name: /^Убрать/ }).first().click();
  await expect(cart.getByTestId("cart-total")).not.toHaveText(before);
  await cart.getByRole("button", { name: /^Вернуть/ }).click();
  await expect(cart.getByTestId("cart-total")).toHaveText(before);
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
