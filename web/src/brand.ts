// Product name and the voice of the mascot. The agent is a banana and speaks in the masculine
// gender: every text the client adds says «Собрал», «Нашёл», «Проверил».

// The working name until the team picks one: change it here only.
export const PRODUCT_NAME = "О!дин запрос";
export const PRODUCT_LINE = "агент путешествий · один запрос";

// The thinking line while the agent answers: one verb every VERB_MS.
export const VERBS = [
  "Пакую чемоданы",
  "Сверяю расписания",
  "Торгуюсь с отелями",
  "Листаю путеводитель",
  "Проверяю прогноз у моря",
  "Считаю пересадки",
  "Прокладываю маршрут",
  "Ловлю попутный ветер",
  "Разворачиваю карту",
  "Взвешиваю багаж",
  "Ищу, где теплее",
  "Меряю температуру воды",
];
export const VERB_MS = 700;

// Animations only when the user did not ask for less motion. No matchMedia (tests): no motion.
export function motionOk(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: no-preference)").matches;
  } catch {
    return false;
  }
}
