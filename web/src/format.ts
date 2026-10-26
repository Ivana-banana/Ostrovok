import type { ChatReply } from "./contracts";

// Одна строка ответа для пользователя; режим без модели подписываем честно (TRAP-1).
export function describe(r: ChatReply): string {
  return r.llm ? r.reply : `${r.reply} — упрощённый режим без ИИ`;
}
