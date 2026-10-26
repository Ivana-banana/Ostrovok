import type { Prediction } from "./contracts";

// Одна строка результата для пользователя; заглушку подписываем честно.
export function describe(p: Prediction): string {
  const base = `${p.label} (${p.score.toFixed(2)}): ${p.explanation}`;
  return p.stub ? `${base} — демо на заглушке` : base;
}
