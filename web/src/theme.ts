// Colour theme of the app: the palettes live in styles.css under [data-theme].
// The URL (?theme=lime) wins and is remembered; storage can throw, then the URL or default works.

export const THEMES = { banana: "Банан", lime: "Лайм" } as const;
export type Theme = keyof typeof THEMES;

export const THEME_KEY = "trip.theme";
const DEFAULT: Theme = "banana";

const isTheme = (t: unknown): t is Theme => typeof t === "string" && t in THEMES;

export function initialTheme(search = typeof location === "undefined" ? "" : location.search): Theme {
  const asked = new URLSearchParams(search).get("theme");
  if (isTheme(asked)) {
    saveTheme(asked);
    return asked;
  }
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (isTheme(stored)) return stored;
  } catch {
    // no storage: the default theme
  }
  return DEFAULT;
}

export function saveTheme(t: Theme) {
  try {
    localStorage.setItem(THEME_KEY, t);
  } catch {
    // no storage: the theme lives until reload
  }
}

export function applyTheme(t: Theme) {
  document.documentElement.dataset.theme = t;
}
