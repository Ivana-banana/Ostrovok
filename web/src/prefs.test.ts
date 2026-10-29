import { beforeEach, describe, expect, it } from "vitest";
import type { TripSpec } from "./contracts";
import { PREFS_KEY, loadPrefs, prefsDraft, prefsFrom, prefsText, savePrefs } from "./prefs";

beforeEach(() => localStorage.clear());

const spec = (o: Partial<TripSpec>) =>
  ({ origin: "Москва", adults: 2, children_ages: [5], pets: false, budget_rub: 90000, assumptions: [], ...o }) as TripSpec;

describe("память предпочтений", () => {
  it("запомненное подставляется в черновик и показывается пользователю", () => {
    savePrefs(prefsFrom(spec({})));
    const p = loadPrefs();
    expect(prefsText(p)).toBe("вылет: Москва, 2 взрослых, ребёнок 5 лет");
    expect(prefsDraft(p)).toContain("2 взрослых");
  });

  it("цены и догадки агента не сохраняются", () => {
    savePrefs(prefsFrom(spec({ assumptions: ["откуда: Москва (по умолчанию)", "взрослых: 2 (состав не назван)"] })));
    expect(localStorage.getItem(PREFS_KEY)).toBeNull();
    savePrefs(prefsFrom(spec({})));
    expect(localStorage.getItem(PREFS_KEY)).not.toContain("90000");
  });
});
