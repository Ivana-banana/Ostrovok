import type { ChatReply, ChatRequest } from "./contracts";

export async function chat(req: ChatRequest): Promise<ChatReply> {
  const r = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) throw new Error(`API ${r.status}`);
  return (await r.json()) as ChatReply;
}
