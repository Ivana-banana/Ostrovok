import type { ChatReply } from "./contracts";

// One reply line for the user; the no-model mode is labelled honestly (TRAP-1).
export function describe(r: ChatReply): string {
  return r.llm ? r.reply : `${r.reply} — упрощённый режим без ИИ`;
}
