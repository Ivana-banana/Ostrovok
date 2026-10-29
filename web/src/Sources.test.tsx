import { expect, it } from "vitest";
import { requested } from "./Sources";

it("запрос всегда просит реальные данные и модель", () => {
  expect(requested()).toEqual({ real_data: true, llm: true });
});
