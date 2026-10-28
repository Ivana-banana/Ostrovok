// #486: the ₽ sign and the digits of the price columns come from the bundled fonts, not from the system.
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "styles.css"), "utf8");

function fontFaces(family: string): { src: string; ranges: [number, number][] }[] {
  const out: { src: string; ranges: [number, number][] }[] = [];
  for (const m of css.matchAll(/@font-face\s*\{([^}]*)\}/g)) {
    const body = m[1];
    if (!body.includes(`font-family: "${family}"`)) continue;
    const src = /url\("([^"]+)"\)/.exec(body)?.[1] ?? "";
    const list = /unicode-range:\s*([^;]+);/.exec(body)?.[1] ?? "";
    const ranges = list.split(",").map((r): [number, number] => {
      const [a, b] = r.trim().replace("U+", "").split("-");
      return [parseInt(a, 16), parseInt(b ?? a, 16)];
    });
    out.push({ src, ranges });
  }
  return out;
}

const RUBLE = 0x20bd;

describe("fonts of the price (#486)", () => {
  for (const family of ["Onest", "Unbounded"]) {
    it(`${family}: the ₽ sign has exactly one bundled file`, () => {
      const faces = fontFaces(family);
      expect(faces.length).toBe(3);
      const own = faces.filter((f) => f.ranges.some(([a, b]) => RUBLE >= a && RUBLE <= b));
      expect(own.length).toBe(1);
      expect(own[0].src).toContain("latin-ext");
      for (const f of faces) expect(existsSync(resolve(__dirname, "../public", f.src.slice(1)))).toBe(true);
    });
  }

  it("the price columns and the price chip use tabular digits", () => {
    expect(css).toMatch(/th\.num,\s*td\.num\s*\{[^}]*font-variant-numeric:\s*tabular-nums/);
    expect(css).toMatch(/\.price\s*\{[^}]*font-variant-numeric:\s*tabular-nums/);
  });
});
