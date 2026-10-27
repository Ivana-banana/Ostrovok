// Chat list of the sidebar. It lives only in the browser: the server keeps each session by id.
// Storage can throw (private mode, blocked cookies, quota): every call then falls back to memory.

export type ChatEntry = { session_id: string; title: string; updated_at: number };

export const HISTORY_KEY = "trip.chats";
const TITLE_MAX = 60;

// The first user message, on one line and short enough for the sidebar.
export function chatTitle(text: string): string {
  const t = text.replace(/\s+/g, " ").trim();
  if (!t) return "Новая поездка";
  return t.length > TITLE_MAX ? `${t.slice(0, TITLE_MAX - 1).trimEnd()}…` : t;
}

const isEntry = (e: unknown): e is ChatEntry =>
  typeof e === "object" &&
  e !== null &&
  typeof (e as ChatEntry).session_id === "string" &&
  (e as ChatEntry).session_id !== "" &&
  typeof (e as ChatEntry).title === "string" &&
  typeof (e as ChatEntry).updated_at === "number";

// Newest first; broken or foreign data gives an empty list.
export function loadChats(): ChatEntry[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    const data: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(data)) return [];
    return sortChats(data.filter(isEntry));
  } catch {
    return [];
  }
}

export function saveChats(list: ChatEntry[]) {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(list));
  } catch {
    // no storage: the list lives until reload
  }
}

const sortChats = (list: ChatEntry[]) => [...list].sort((a, b) => b.updated_at - a.updated_at);

// Adds the chat or marks it as just used. A new chat takes the title; an old one keeps its own
// (the user may have renamed it).
export function touchChat(list: ChatEntry[], id: string, title: string, now = Date.now()): ChatEntry[] {
  const old = list.find((e) => e.session_id === id);
  const entry = old ? { ...old, updated_at: now } : { session_id: id, title: chatTitle(title), updated_at: now };
  return sortChats([entry, ...list.filter((e) => e.session_id !== id)]);
}

export function renameChat(list: ChatEntry[], id: string, title: string): ChatEntry[] {
  const t = chatTitle(title);
  return list.map((e) => (e.session_id === id ? { ...e, title: t } : e));
}

export function removeChat(list: ChatEntry[], id: string): ChatEntry[] {
  return list.filter((e) => e.session_id !== id);
}
