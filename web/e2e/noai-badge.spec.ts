import { expect, test } from "@playwright/test";

// #350, TRAP-1: без ключа LLM пометка «Без ИИ» видна до первого сообщения (из /api/mode).
test("TRAP-1: пометка «Без ИИ» видна до первого сообщения", async ({ page, request }) => {
  const mode = await (await request.get("/api/mode")).json();
  expect(typeof mode.llm).toBe("boolean"); // a missing route fails here
  await page.goto("/");
  const badge = page.locator(".badge-noai");
  if (mode.llm === false) await expect(badge).toContainText("Без ИИ");
  else await expect(badge).toHaveCount(0);
});
