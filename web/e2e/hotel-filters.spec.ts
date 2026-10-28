import { expect, test } from "@playwright/test";

// #431: чипы фильтров отелей стоят на доске; клик правит план без поиска с нуля.
test("#431: чип «Условия отмены» выбирает отель с бесплатной отменой", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Запрос").fill("Сочи из Москвы с 3 по 8 декабря, двое взрослых");
  await page.getByRole("button", { name: "Отправить" }).click();
  const chips = page.getByRole("group", { name: "Фильтры отелей" });
  await expect(chips).toBeVisible();
  await expect(chips).toContainText("Питание");
  await expect(chips).toContainText("Бюджет отеля");
  await page.screenshot({ path: "test-results/431-hotel-chips.png", fullPage: true });

  await chips.getByRole("button", { name: /Условия отмены/ }).click();
  await expect(chips.getByRole("button", { name: /Условия отмены/ })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  const hotels = page.getByRole("region", { name: "Сравнение отелей" });
  // The table keeps every hotel to compare; the chosen one has free cancellation.
  const chosen = hotels.locator("tbody tr", { hasText: "выбран" });
  await expect(chosen).toContainText("бесплатно до");
  await page.screenshot({ path: "test-results/431-hotel-chips-on.png", fullPage: true });
});
