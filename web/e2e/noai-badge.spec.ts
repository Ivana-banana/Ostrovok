import { expect, test } from "@playwright/test";

// #350, TRAP-1: пометка «Без ИИ» видна до первого сообщения. #490: GigaChat по умолчанию
// выключен, поэтому пометка видна и с ключом; без ключа в .env переключатель недоступен.
test("TRAP-1: пометка «Без ИИ» видна до первого сообщения", async ({ page, request }) => {
  const mode = await (await request.get("/api/mode")).json();
  expect(typeof mode.llm).toBe("boolean"); // a missing route fails here
  expect(typeof mode.real_data).toBe("boolean");
  await page.goto("/");
  await expect(page.locator(".badge-noai")).toContainText("Без ИИ");
  const giga = page.getByRole("switch", { name: "GigaChat" });
  const real = page.getByRole("switch", { name: "Реальные данные (Авиасейлс)" });
  await expect(giga).not.toBeChecked();
  await expect(real).not.toBeChecked();
  if (mode.llm) await expect(giga).toBeEnabled();
  else await expect(giga).toBeDisabled();
  if (mode.real_data) await expect(real).toBeEnabled();
  else await expect(real).toBeDisabled();
});
