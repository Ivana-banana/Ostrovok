import { expect, test } from "@playwright/test";

// #350, TRAP-1: без ключа LLM пометка «Без ИИ» видна до первого сообщения (из /api/health).
test("TRAP-1: пометка «Без ИИ» видна до первого сообщения", async ({ page, request }) => {
  const health = await (await request.get("/api/health")).json();
  await page.goto("/");
  const badge = page.locator(".badge-noai");
  if (health.llm === false) await expect(badge).toContainText("Без ИИ");
  else await expect(badge).toHaveCount(0);
});
