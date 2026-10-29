import { expect, test } from "@playwright/test";

// #350, TRAP-1: без ключа модели пометка «Без ИИ» видна до первого сообщения. #566: GigaChat
// без переключателя — включён, когда ключ есть; «Синтетика» по умолчанию выключена.
test("TRAP-1: пометка «Без ИИ» видна до первого сообщения без ключа", async ({ page, request }) => {
  const mode = await (await request.get("/api/mode")).json();
  expect(typeof mode.llm).toBe("boolean"); // a missing route fails here
  expect(typeof mode.real_data).toBe("boolean");
  await page.goto("/");
  const badge = page.locator(".badge-noai");
  if (mode.llm) await expect(badge).toHaveCount(0);
  else await expect(badge).toContainText("Без ИИ");
  const synth = page.getByRole("switch", { name: "Синтетика" });
  await expect(synth).toBeEnabled();
  await expect(synth).not.toBeChecked();
});
