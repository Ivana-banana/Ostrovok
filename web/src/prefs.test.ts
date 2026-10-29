import { beforeEach, describe, expect, it } from "vitest";
import type { TripSpec } from "./contracts";
import { PREFS_KEY, forgetPrefs, loadPrefs, prefsDraft, prefsText, rememberPrefs } from "./prefs";

beforeEach(() => localStorage.clear());

const spec = (o: Partial<TripSpec>) =>
  ({ origin: "Москва", adults: 2, children_ages: [5], pets: false, budget_rub: 90000, assumptions: [], ...o }) as TripSpec;

describe("память предпочтений", () => {
  it("запомненное подставляется в черновик и показывается пользователю", () => {
    rememberPrefs(spec({}));
    const p = loadPrefs();
    expect(prefsText(p)).toBe("вылет: Москва, 2 взрослых, ребёнок 5 лет");
    expect(prefsDraft(p)).toBe("из Москвы, 2 взрослых, ребёнок 5 лет. ");
  });

  it("цены и догадки агента не сохраняются", () => {
    rememberPrefs(spec({ assumptions: ["откуда: Москва (по умолчанию)", "взрослых: 2 (состав не назван)"] }));
    expect(localStorage.getItem(PREFS_KEY)).toBeNull();
    rememberPrefs(spec({}));
    expect(localStorage.getItem(PREFS_KEY)).not.toContain("90000");
  });

  it("догадка агента не стирает старую память, «Забыть» стирает", () => {
    rememberPrefs(spec({}));
    rememberPrefs(spec({ origin: null, assumptions: ["взрослых: 2 (состав не назван)"] }));
    expect(loadPrefs().origin).toBe("Москва");
    forgetPrefs();
    expect(localStorage.getItem(PREFS_KEY)).toBeNull();
  });
});

it("origin in the draft is in the genitive case", () => {
  expect(["Москва", "Казань", "Санкт-Петербург", "Нижний Новгород", "Ростов-на-Дону"].map((origin) => prefsDraft({ origin }))).toEqual([
    "из Москвы. ", "из Казани. ", "из Санкт-Петербурга. ", "из Нижнего Новгорода. ", "из Ростова-на-Дону. ",
  ]);
});
