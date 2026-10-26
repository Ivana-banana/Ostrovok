import { expect, test } from "@playwright/test";

// Сценарий демо жюри: открыть → ввести запрос → получить ответ с объяснением.
// Работает на заглушке с первого коммита; сценарии jury-click из eval/cases.json — сюда.
test("демо-путь: запрос → ответ", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Запрос").fill("пример");
  await page.getByRole("button", { name: "Отправить" }).click();
  await expect(page.getByRole("status")).toContainText(":");
});
