import { useEffect, useRef, useState } from "react";
import { ApiError, cartAction, chat, checkout, getSession } from "./api";
import { Board, Destinations, destinationsInChat } from "./Board";
import { PRODUCT_LINE, PRODUCT_NAME, motionOk } from "./brand";
import { AgentText, StagePills, Thinking } from "./Chat";
import type { ChatReply, Guest, Offer } from "./contracts";
import { SOURCES, cartLine, describe } from "./format";
import {
  type ChatEntry,
  filterChats,
  groupChats,
  loadChats,
  removeChat,
  renameChat,
  saveChats,
  touchChat,
} from "./history";
import { Mascot } from "./Mascot";

type Msg = { role: "user" | "agent"; text: string };
type Ui = { messages: Msg[]; removed: Offer[] };

const STALE_PLAN = "Не удалось обновить план — перезагрузите страницу";

export const SESSION_KEY = "trip.session";
const uiKey = (sid: string) => `trip.ui.${sid}`;

const EXAMPLES = [
  "Хочу к тёплому морю в ноябре, недорого, с ребёнком 5 лет, без долгих перелётов. Летим из Москвы.",
  "Выходные в Казани, из Москвы, 2 взрослых, 11–13 декабря, до 50 тысяч на всё.",
];

// Storage can throw (private mode, blocked cookies, quota): the app then works without it.
function readSid(): string | null {
  try {
    return localStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}

function writeSid(id: string) {
  try {
    localStorage.setItem(SESSION_KEY, id);
  } catch {
    // no storage: reload starts a new trip, the current one keeps working
  }
}

function dropSid() {
  try {
    localStorage.removeItem(SESSION_KEY);
  } catch {
    // no storage: nothing to drop
  }
}

const isMsg = (m: unknown): m is Msg =>
  typeof m === "object" &&
  m !== null &&
  ((m as Msg).role === "user" || (m as Msg).role === "agent") &&
  typeof (m as Msg).text === "string";

const isOffer = (o: unknown): o is Offer =>
  typeof o === "object" &&
  o !== null &&
  typeof (o as Offer).offer_id === "string" &&
  typeof (o as Offer).title === "string" &&
  typeof (o as Offer).price_rub === "number";

// Saved chat of a session; broken or foreign data falls back to an empty chat.
function loadUi(sid: string): Ui {
  try {
    const raw = localStorage.getItem(uiKey(sid));
    const data: unknown = raw ? JSON.parse(raw) : null;
    if (typeof data === "object" && data !== null) {
      const { messages, removed } = data as Record<string, unknown>;
      return {
        messages: Array.isArray(messages) ? messages.filter(isMsg) : [],
        removed: Array.isArray(removed) ? removed.filter(isOffer) : [],
      };
    }
  } catch {
    // broken storage: start the chat history from the server state
  }
  return { messages: [], removed: [] };
}

function dropUi(sid: string) {
  try {
    localStorage.removeItem(uiKey(sid));
  } catch {
    // no storage: nothing to drop
  }
}

// The plan panel starts open where it fits next to the chat (the breakpoint of styles.css).
function wideScreen(): boolean {
  try {
    return window.matchMedia("(min-width: 760px)").matches;
  } catch {
    return true; // no matchMedia (tests, old browsers): show the plan
  }
}

function saveUi(sid: string, ui: Ui) {
  try {
    localStorage.setItem(uiKey(sid), JSON.stringify(ui));
  } catch {
    // storage full or blocked: the session still lives on the server
  }
}

const userError = (e: unknown) =>
  e instanceof ApiError ? e.message : "Что-то пошло не так. Попробуйте ещё раз.";

const EMPTY_UI: Ui = { messages: [], removed: [] };

export function App() {
  const [text, setText] = useState("");
  const [reply, setReply] = useState<ChatReply | null>(null);
  const [ui, setUi] = useState<Ui>(EMPTY_UI);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [restoring, setRestoring] = useState(() => !!readSid());
  const [chats, setChats] = useState<ChatEntry[]>(loadChats);
  const [drawer, setDrawer] = useState(false);
  const [planOpen, setPlanOpen] = useState(wideScreen);
  const [renaming, setRenaming] = useState<{ id: string; title: string } | null>(null);
  const [query, setQuery] = useState("");
  const [thinking, setThinking] = useState(false);
  const [typing, setTyping] = useState<number | null>(null); // index of the message being typed
  const [noAi, setNoAi] = useState(false); // the backend answered without a model (TRAP-1)
  const box = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);
  // Every chat load gets a number: an answer of an older load is dropped.
  const loadNo = useRef(0);
  const chatsNow = useRef(chats);
  chatsNow.current = chats;

  const sid = reply?.session_id ?? null;
  const current = chats.find((c) => c.session_id === sid) ?? null;

  function update(next: Ui, id = sid) {
    setUi(next);
    if (id) saveUi(id, next);
  }

  function editChats(edit: (list: ChatEntry[]) => ChatEntry[]) {
    setChats((list) => {
      const next = edit(list);
      saveChats(next);
      return next;
    });
  }

  // Opens a saved chat: the server keeps the last reply, the browser keeps the messages.
  // prev: the chat on the screen before; it comes back when the new one does not open.
  async function load(id: string, failText: string, prev?: { reply: ChatReply | null; ui: Ui }) {
    const no = ++loadNo.current;
    setRestoring(true);
    setError(null);
    setNote(null);
    try {
      const r = await getSession(id);
      if (no !== loadNo.current) return;
      if (!r) {
        if (readSid() === id) dropSid();
        dropUi(id);
        if (prev) {
          setReply(prev.reply);
          setUi(prev.ui);
        }
        const gone = chatsNow.current.find((c) => c.session_id === id);
        editChats((list) => removeChat(list, id));
        if (gone) setNote(`Чат «${gone.title}» больше не хранится на сервере. Убрал его из списка.`);
        return;
      }
      const saved = loadUi(id);
      const messages = saved.messages.length
        ? saved.messages
        : [{ role: "agent" as const, text: describe(r) }];
      writeSid(id);
      setReply(r);
      setNoAi(!r.llm);
      setTyping(null);
      setUi({ ...saved, messages });
      const first = saved.messages.find((m) => m.role === "user")?.text ?? "";
      editChats((list) => (list.some((c) => c.session_id === id) ? list : touchChat(list, id, first)));
    } catch (e) {
      if (no !== loadNo.current) return;
      setError(`${failText} ${userError(e)}`);
      // The stored session still is the previous chat: show it again, not an empty screen.
      if (prev) {
        setReply(prev.reply);
        setUi(prev.ui);
      }
    } finally {
      if (no === loadNo.current) setRestoring(false);
    }
  }

  // Reload restores the last open chat.
  useEffect(() => {
    const stored = readSid();
    if (stored) void load(stored, "Не удалось восстановить поездку.");
    return () => {
      loadNo.current++;
    };
  }, []);

  // The input grows with the text up to 160 px; an example or a sent message resizes it too.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [text]);

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [ui.messages.length]);

  async function send(message: string) {
    const m = message.trim();
    if (!m || busy || restoring) return;
    setBusy(true);
    setError(null);
    setNote(null);
    const withUser = { ...ui, messages: [...ui.messages, { role: "user" as const, text: m }] };
    setUi(withUser);
    setThinking(true);
    try {
      const r = await chat({ session_id: sid, message: m });
      writeSid(r.session_id);
      setReply(r);
      setNoAi(!r.llm);
      if (motionOk()) setTyping(withUser.messages.length);
      update(
        { ...withUser, messages: [...withUser.messages, { role: "agent", text: describe(r) }] },
        r.session_id,
      );
      const first = withUser.messages.find((x) => x.role === "user")?.text ?? m;
      editChats((list) => touchChat(list, r.session_id, first));
      setText("");
    } catch (e) {
      setError(userError(e));
    } finally {
      setThinking(false);
      setBusy(false);
    }
  }

  async function onCart(action: "add" | "remove", offerId: string) {
    if (!reply || busy) return;
    setBusy(true);
    setError(null);
    const id = reply.session_id;
    try {
      let cart = await cartAction({ session_id: id, action, offer_id: offerId });
      const item =
        reply.cart.items.find((o) => o.offer_id === offerId) ??
        reply.plan.offers.find((o) => o.offer_id === offerId);
      // The server drops the add-ons of a removed item; «Вернуть» brings them back with it.
      const extras = (list: Offer[]) =>
        list.filter((o) => o.kind === "extra" && o.for_offer_id === offerId);
      const rest = ui.removed.filter((o) => o.offer_id !== offerId);
      let removed = rest;
      if (action === "remove" && item) {
        const own = extras(reply.cart.items).filter((o) => !rest.some((r) => r.offer_id === o.offer_id));
        removed = [...rest, item, ...own];
      } else if (action === "add") {
        const again = extras(rest);
        try {
          for (const x of again) {
            cart = await cartAction({ session_id: id, action: "add", offer_id: x.offer_id });
          }
          removed = rest.filter((o) => !again.includes(o));
        } catch (e) {
          setError(userError(e));
        }
      }
      // The cart edit can change the plan (selection, timeline, issues): take it from the server.
      const fresh = await getSession(id).catch(() => null);
      if (!fresh) setError(STALE_PLAN);
      // The old chat line with the cart sum is stale now: say the real count and total.
      update({ ...ui, removed, messages: [...ui.messages, { role: "agent", text: cartLine(cart) }] });
      setReply({ ...(fresh ?? reply), cart });
      editChats((list) => touchChat(list, id, ""));
    } catch (e) {
      setError(userError(e));
    } finally {
      setBusy(false);
    }
  }

  async function onCheckout(guest: Guest | null) {
    if (!reply || busy) return;
    setBusy(true);
    setError(null);
    const id = reply.session_id;
    try {
      const voucher = await checkout({
        session_id: id,
        confirm_total_rub: reply.cart.total_rub,
        ...(guest && { guest }),
      });
      const fresh = await getSession(id).catch(() => null);
      if (!fresh?.voucher) setError(STALE_PLAN);
      setReply(fresh?.voucher ? fresh : { ...reply, stage: "booked", voucher });
      const text = `Бронь ${voucher.booking_id} оформлена. ${voucher.simulated ? "Оплата имитирована." : "Оплата принята."}`;
      update({ ...ui, messages: [...ui.messages, { role: "agent", text }] });
      editChats((list) => touchChat(list, id, ""));
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setError(e.message);
        const fresh = await getSession(id).catch(() => null);
        if (fresh) setReply(fresh);
      } else setError(userError(e));
    } finally {
      setBusy(false);
    }
  }

  // «Новый чат»: the next message creates a new session on the server, as on the first visit.
  function newChat() {
    loadNo.current++;
    dropSid();
    setReply(null);
    setUi(EMPTY_UI);
    setError(null);
    setNote(null);
    setText("");
    setRestoring(false);
    setTyping(null);
    setDrawer(false);
    box.current?.focus();
  }

  function openChat(id: string) {
    setDrawer(false);
    if (busy || id === sid) return;
    const prev = { reply, ui };
    setReply(null);
    setUi(EMPTY_UI);
    setText("");
    void load(id, "Не удалось открыть чат.", prev);
  }

  function deleteChat(id: string) {
    editChats((list) => removeChat(list, id));
    dropUi(id);
    if (id === sid || id === readSid()) newChat();
  }

  function finishRename(save: boolean) {
    if (save && renaming && renaming.title.trim()) {
      const { id, title } = renaming;
      editChats((list) => renameChat(list, id, title));
    }
    setRenaming(null);
  }

  const lastAgent = ui.messages.map((m) => m.role).lastIndexOf("agent");
  const cls = ["app", planOpen && "plan-open", drawer && "drawer-open"].filter(Boolean).join(" ");
  const synthetic = !reply || !reply.plan.offers.length || reply.plan.offers.some((o) => o.synthetic);
  const groups = groupChats(filterChats(chats, query));
  const scroll = () => end.current?.scrollIntoView?.({ block: "end" });

  return (
    <div
      className={cls}
      onKeyDown={(e) => {
        if (e.key !== "Escape") return;
        if (drawer) setDrawer(false);
        else if (planOpen && !wideScreen()) setPlanOpen(false);
      }}
    >
      <aside id="chats" className="side" aria-label="Чаты">
        <div className="brand">
          <Mascot size={38} />
          <div>
            <b>{PRODUCT_NAME}</b>
            <small>{PRODUCT_LINE}</small>
          </div>
          <button
            type="button"
            className="icon drawer-close"
            aria-label="Закрыть список чатов"
            onClick={() => setDrawer(false)}
          >
            ✕
          </button>
        </div>
        <button type="button" className="newchat" onClick={newChat} disabled={busy}>
          ＋ Новый чат
        </button>
        {chats.length > 0 && (
          <input
            type="search"
            className="search"
            aria-label="Поиск по чатам"
            placeholder="Поиск по чатам"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        )}
        <nav className="grow" aria-label="Прошлые чаты">
          {groups.map((g) => (
            <div key={g.label}>
              <div className="group">{g.label}</div>
              <ul className="chat-list">
                {g.items.map((c) => {
                  const on = c.session_id === sid;
                  return (
                    <li key={c.session_id} className={on ? "on" : undefined}>
                      {renaming?.id === c.session_id ? (
                        <form
                          className="rename"
                          onSubmit={(e) => {
                            e.preventDefault();
                            finishRename(true);
                          }}
                        >
                          <input
                            aria-label="Название чата"
                            autoFocus
                            value={renaming.title}
                            onChange={(e) => setRenaming({ id: c.session_id, title: e.target.value })}
                            onBlur={() => finishRename(true)}
                            onKeyDown={(e) => {
                              if (e.key === "Escape") {
                                e.stopPropagation();
                                finishRename(false);
                              }
                            }}
                          />
                        </form>
                      ) : (
                        <>
                          <button
                            type="button"
                            className="chat-open"
                            title={c.title}
                            aria-current={on ? "page" : undefined}
                            disabled={busy && !on}
                            onClick={() => openChat(c.session_id)}
                          >
                            {c.title}
                          </button>
                          <button
                            type="button"
                            className="icon"
                            aria-label={`Переименовать чат «${c.title}»`}
                            onClick={() => setRenaming({ id: c.session_id, title: c.title })}
                          >
                            ✎
                          </button>
                          <button
                            type="button"
                            className="icon"
                            aria-label={`Удалить чат «${c.title}»`}
                            disabled={busy && on}
                            onClick={() => deleteChat(c.session_id)}
                          >
                            ✕
                          </button>
                        </>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
          {!chats.length && <p className="side-empty">Здесь появятся ваши поездки.</p>}
          {chats.length > 0 && !groups.length && <p className="side-empty">Не нашёл чатов по запросу.</p>}
        </nav>
        <div className="foot">
          {noAi && (
            <span className="badge-noai">
              <i />
              Без ИИ — упрощённый режим
            </span>
          )}
          <span>Список чатов хранится только в этом браузере.</span>
        </div>
      </aside>
      {drawer && <div className="backdrop" aria-hidden="true" onClick={() => setDrawer(false)} />}
      <section className="main" aria-label="Чат">
        <header className="top">
          <button
            type="button"
            className="icon burger"
            aria-label="Список чатов"
            aria-controls="chats"
            aria-expanded={drawer}
            onClick={() => setDrawer(true)}
          >
            ☰
          </button>
          <h1 title={current?.title}>{current?.title ?? "Новая поездка"}</h1>
          <StagePills stage={reply?.stage ?? null} />
          <button
            type="button"
            className="iconbtn plan-toggle"
            aria-controls="plan"
            aria-expanded={planOpen}
            onClick={() => setPlanOpen(!planOpen)}
          >
            ◧ План
          </button>
        </header>
        <div className="feed" role="log" aria-live="polite">
          <div className="thread">
            {note && (
              <p className="notice" role="note">
                {note}
              </p>
            )}
            {!ui.messages.length && !restoring && (
              <div className="hello">
                <Mascot size={56} />
                <h2>Куда поедем?</h2>
              </div>
            )}
            {restoring && <p className="muted">Восстанавливаю поездку…</p>}
            {ui.messages.map((m, i) =>
              m.role === "user" ? (
                <div key={i} className="msg user">
                  <div className="b">{m.text}</div>
                </div>
              ) : (
                <div
                  key={i}
                  className="msg agent"
                  role={i === lastAgent ? "status" : undefined}
                  aria-busy={typing === i || undefined}
                >
                  <Mascot size={30} />
                  <div className="b">
                    <AgentText
                      text={m.text}
                      synthetic={synthetic}
                      typing={typing === i}
                      onTick={scroll}
                      onDone={() => setTyping(null)}
                    />
                  </div>
                </div>
              ),
            )}
            {reply && typing === null && !thinking && (
              <div className="attach">
                {destinationsInChat(reply.stage) && reply.plan.destinations.length > 0 && (
                  <Destinations plan={reply.plan} stage={reply.stage} busy={busy} onSend={send} />
                )}
                {reply.questions.length > 0 && (
                  <div className="questions">
                    {reply.questions.map((q) => (
                      <div key={q.text}>
                        <p>{q.text}</p>
                        <div className="chips">
                          {q.options.map((o) => (
                            <button key={o} type="button" className="chip" disabled={busy} onClick={() => send(o)}>
                              {o}
                            </button>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
            {thinking && <Thinking />}
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            <div ref={end} />
          </div>
        </div>
        {reply && !planOpen && (
          <button type="button" className="plan-sheet-open" aria-controls="plan" onClick={() => setPlanOpen(true)}>
            План поездки
          </button>
        )}
        {!ui.messages.length && !restoring && (
          <div className="starters" aria-label="Примеры для начала">
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                type="button"
                className="chip example"
                onClick={() => {
                  setText(ex);
                  box.current?.focus();
                }}
              >
                {ex}
              </button>
            ))}
          </div>
        )}
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            void send(text);
          }}
        >
          <div className="box">
            <textarea
              ref={box}
              rows={1}
              aria-label="Запрос"
              placeholder="Опишите поездку одним сообщением…"
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void send(text);
                }
              }}
            />
            <button
              type="submit"
              className="send"
              aria-label="Отправить"
              title="Отправить"
              disabled={busy || restoring || !text.trim()}
            >
              ↑
            </button>
          </div>
          <p className="composer-hint">Цены и рейсы — синтетика. Оплата имитирована.</p>
        </form>
      </section>
      <aside id="plan" className="plan" aria-label="Доска поездки">
        <header className="hd">
          <b>План поездки</b>
          <button type="button" className="iconbtn" aria-label="Свернуть план" onClick={() => setPlanOpen(false)}>
            ✕
          </button>
        </header>
        <div className="bd">
          {reply ? (
            <Board
              reply={reply}
              busy={busy}
              removed={ui.removed.filter(
                (o) => o.kind !== "extra" || !ui.removed.some((p) => p.offer_id === o.for_offer_id),
              )}
              onSend={send}
              onCart={onCart}
              onCheckout={onCheckout}
            />
          ) : (
            <p className="muted empty">Здесь появится доска поездки: варианты, сравнение, план и корзина.</p>
          )}
          <footer className="sources" aria-label="Источники данных">
            <span>Данные:</span>
            {SOURCES.map((s) => (
              <span key={s.name}>
                {s.text}, лицензия{" "}
                <a href={s.url} target="_blank" rel="noreferrer">
                  {s.licence}
                </a>
                .
              </span>
            ))}
            <span>
              Цены, рейсы, тарифы, трансферы и рейтинги — синтетика; отели вне снимка OSM — тоже. Климат и
              время в пути — оценки команды. Оплата имитируется.
            </span>
          </footer>
        </div>
      </aside>
    </div>
  );
}
