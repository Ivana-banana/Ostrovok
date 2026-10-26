import { describe as suite, expect, it } from "vitest";
import { describe } from "./format";

suite("describe", () => {
  it("помечает заглушку", () => {
    expect(describe({ label: "x", score: 0, explanation: "e", stub: true })).toContain("заглушке");
  });
  it("без пометки для модели", () => {
    expect(describe({ label: "x", score: 0.5, explanation: "e", stub: false })).toBe("x (0.50): e");
  });
});
