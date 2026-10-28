import { useEffect, useRef, useState } from "react";
import { ApiError, cartAction, chat, checkout, getMode, getSession } from "./api";
import { Board, EarlierChoices, TripParams } from "./Board";
import { MIN_THINK_MS, PRODUCT_NAME, motionOk } from "./brand";
import { AgentText, Roadmap, STEPS, StepCaption, Thinking, stepOf } from "./Chat";
import type { ChatReply, Guest, LlmMode, Offer, Stage } from "./contracts";
import { SOURCES, brief, cartLine, describe, withoutNoAi } from "./format";
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
import { Mascot, moodOf } from "./Mascot";
import {
  type ModeState,
  SourceSwitches,
  type Switches,
  loadSwitches,
  requested,
  saveSwitches,
} from "./Sources";

// stage: the stage after an agent message; the roadmap links a done step to it (#434).
// An agent message keeps its reply: the choices under it stay after «ещё», a reload or a chat switch.
type Msg = { role: "user" | "agent"; text: string; stage?: Stage; reply?: ChatReply };
const KEEP_REPLIES = 6; // the last agent replies stored with the chat; older ones keep only their text
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

// A stored reply is used to draw old choices: a broken one is dropped, the text stays.
function withValidReply(m: Msg): Msg {
  const r = m.reply as Partial<ChatReply> | undefined;
  const ok =
    typeof r === "object" &&
    r !== null &&
    typeof r.stage === "string" &&
    typeof r.plan === "object" &&
    r.plan !== null &&
    Array.isArray(r.plan.destinations) &&
    Array.isArray(r.plan.offers);
  return ok ? m : { ...m, reply: undefined };
}

// Saved chat of a session; broken or foreign data falls back to an empty chat.
function loadUi(sid: string): Ui {
  try {
    const raw = localStorage.getItem(uiKey(sid));
    const data: unknown = raw ? JSON.parse(raw) : null;
    if (typeof data === "object" && data !== null) {
      const { messages, removed } = data as Record<string, unknown>;
      return {
        messages: Array.isArray(messages) ? messages.filter(isMsg).map(withValidReply) : [],
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

function saveUi(sid: string, ui: Ui) {
  let keep = KEEP_REPLIES;
  const messages = [...ui.messages]
    .reverse()
    .map((m) => (m.reply && keep-- > 0 ? m : { ...m, reply: undefined }))
    .reverse();
  try {
    localStorage.setItem(uiKey(sid), JSON.stringify({ ...ui, messages }));
  } catch {
    // storage full or blocked: the session still lives on the server
  }
}

const userError = (e: unknown) =>
  e instanceof ApiError ? e.message : "Что-то пошло не так. Попробуйте ещё раз.";

const EMPTY_UI: Ui = { messages: [], removed: [] };

export const MODE_RETRY_MS = 2000; // #534: the second GET /api/mode

// The help texts name the real sources found in the offers of the plan (#547, TRAP-7).
const HOTELS_REAL = "Отели — 101hotels (цена «от» на сегодня)";
const FLIGHTS_REAL = "перелёты — Авиасейлс (цены из кэша поиска)";
const TRAINS_REAL = "поезда — tutu";
const realParts = (offers: Offer[]) => {
  const real = (kind: string) =>
    offers.some((o) => !o.synthetic && (o.kind === kind || (o.kind === "transport" && o.mode === kind)));
  return [real("hotel") && HOTELS_REAL, real("flight") && FLIGHTS_REAL, real("train") && TRAINS_REAL].filter(
    Boolean,
  ) as string[];
};
const priceSources = (offers: Offer[]) => {
  const parts = realParts(offers);
  return parts.length
    ? `${parts.join(", ")}, остальные цены — синтетика. Оплата имитирована.`
    : "Цены и рейсы — синтетика. Оплата имитирована.";
};
const dataNote = (offers: Offer[]) => {
  const parts = realParts(offers);
  return parts.length
    ? `${parts.join(", ")}; остальные цены, тарифы, трансферы и рейтинги — синтетика.`
    : "Цены, рейсы, тарифы, трансферы и рейтинги — синтетика; отели вне снимка OSM — тоже.";
};

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
  const [road, setRoad] = useState(false); // the roadmap sheet below 1100 px
  const [renaming, setRenaming] = useState<{ id: string; title: string } | null>(null);
  const [query, setQuery] = useState("");
  const [thinking, setThinking] = useState(false);
  const [noAi, setNoAi] = useState(false); // the backend answered without a model (TRAP-1)
  const [switches, setSwitches] = useState<Switches>(loadSwitches); // #490: both off by default
  const [can, setCan] = useState<LlmMode>({ llm: false, real_data: false }); // /api/mode: keys in .env
  const [mode, setMode] = useState<ModeState>("loading"); // /api/mode: failed after a retry (#534)
  const box = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const lastMsg = useRef<HTMLDivElement>(null); // the last agent message: its block sits under it
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
        : [{ role: "agent" as const, text: describe(r), stage: r.stage }];
      writeSid(id);
      setReply(r);
      setNoAi(!r.llm);
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

  // The switches and the mode badge must not wait for the first reply.
  // #534: one retry after MODE_RETRY_MS (the container may still start), then a visible note.
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const ask = (retry: boolean) =>
      getMode()
        .then((m) => {
          if (!live) return;
          setCan({ llm: m.llm === true, real_data: m.real_data === true });
          setMode("ok");
        })
        .catch((e) => {
          console.warn("GET /api/mode failed", retry ? "; retrying" : "; the switches stay off", e);
          if (!live) return;
          if (retry) timer = setTimeout(() => ask(false), MODE_RETRY_MS);
          else setMode("failed");
        });
    ask(true);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, []);

  function switchSources(next: Switches) {
    setSwitches(next);
    saveSwitches(next);
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

  // A new answer: its top is in view, the stage block under it is read downwards.
  // A new user message: the end of the chat, where the agent will answer.
  useEffect(() => {
    const last = ui.messages.at(-1);
    if (last?.role === "agent") lastMsg.current?.scrollIntoView?.({ block: "start" });
    else end.current?.scrollIntoView?.({ block: "end" });
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
    const no = loadNo.current;
    try {
      const t0 = Date.now();
      const r = await chat({ session_id: sid, message: m, ...requested(switches, can) });
      // A fast offline reply still shows the thinking indicator for a moment (not with reduced motion).
      const rest = MIN_THINK_MS - (Date.now() - t0);
      if (motionOk() && rest > 0) await new Promise((res) => setTimeout(res, rest));
      if (no !== loadNo.current) return; // the user switched the chat while the agent thought
      writeSid(r.session_id);
      setReply(r);
      setNoAi(!r.llm);
      update(
        { ...withUser, messages: [...withUser.messages, { role: "agent", text: describe(r), stage: r.stage, reply: r }] },
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
      update({ ...ui, removed, messages: [...ui.messages, { role: "agent", text: cartLine(cart), stage: (fresh ?? reply).stage }] });
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
      update({ ...ui, messages: [...ui.messages, { role: "agent", text, stage: "booked" }] });
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
  const asked = ui.messages.filter((m) => m.role === "user").length; // the thinking banana and plane take turns
  const on = requested(switches, can);
  // The «Без ИИ» badge of the sidebar (TRAP-1): GigaChat is off, or the last reply was rule-based.
  const badgeNoAi = !on.llm || (!!reply && noAi);
  // A done step links to the first answer after which the trip was past it.
  const doneAt = STEPS.map((_, i) => {
    const n = ui.messages.findIndex(
      (m) => m.role === "agent" && m.stage && (stepOf(m.stage) > i || (i === STEPS.length - 1 && m.stage === "booked")),
    );
    return n < 0 ? null : n;
  });
  const jump = (n: number) => {
    setRoad(false);
    document.querySelector(`[data-msg="${n}"]`)?.scrollIntoView?.({ block: "start", behavior: motionOk() ? "smooth" : "auto" });
  };
  const cls = ["app", drawer && "drawer-open", road && "road-open"].filter(Boolean).join(" ");
  const synthetic = !reply || !reply.plan.offers.length || reply.plan.offers.some((o) => o.synthetic);
  const groups = groupChats(filterChats(chats, query));
  // The cards of the current stage under the last answer; hidden while the agent thinks.
  const cardNames = reply ? reply.plan.destinations.map((d) => d.name) : [];
  // Only at inspire: there the cards have «Выбрать». At select the chips are the only way to pick.
  const questions = reply
    ? reply.questions.filter((q) => reply.stage !== "inspire" || !(q.options.length > 0 && q.options.every((o) => cardNames.includes(o))))
    : [];
  const block = reply && !thinking && (
    <div key="stage-block" className="attach">
      {/* #458: a question whose options only repeat the names of the cards below is redundant */}
      {questions.length > 0 && (
        <div className="questions">
          {questions.map((q) => (
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
    </div>
  );

  return (
    <div
      className={cls}
      onKeyDown={(e) => {
        if (e.key !== "Escape") return;
        if (drawer) setDrawer(false);
        else if (road) setRoad(false);
      }}
    >
      <aside id="chats" className="side" aria-label="Чаты">
        <div className="brand">
          <Mascot size={38} />
          <b>{PRODUCT_NAME}</b>
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
          {badgeNoAi && (
            <span className="badge-noai">
              <i />
              Без ИИ — упрощённый режим
            </span>
          )}
          <SourceSwitches on={switches} can={can} mode={mode} onChange={switchSources} />
          <details className="hint">
            <summary aria-label="Где хранятся чаты">
              <span className="qmark" aria-hidden="true">?</span>
            </summary>
            <p>Список чатов хранится только в этом браузере.</p>
          </details>
        </div>
      </aside>
      {drawer && <div className="backdrop" aria-hidden="true" onClick={() => setDrawer(false)} />}
      {road && <div className="backdrop road-backdrop" aria-hidden="true" onClick={() => setRoad(false)} />}
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
        </header>
        <StepCaption stage={reply?.stage ?? null} onOpen={() => setRoad(true)} />
        {reply && (
          <TripParams
            spec={reply.plan.spec}
            locked={busy || reply.stage === "clarify" || reply.stage === "booked"}
            onSend={send}
          />
        )}
        <div className="feed" role="log" aria-live="polite">
          <div className="thread">
            {note && (
              <p className="notice" role="note">
                {note}
              </p>
            )}
            {!ui.messages.length && !restoring && (
              <div className="hello">
                <Mascot size={110} state="hello" />
                <h2>Куда поедем?</h2>
              </div>
            )}
            {restoring && <p className="muted">Восстанавливаю поездку…</p>}
            {ui.messages.flatMap((m, i) => {
              const msg =
                m.role === "user" ? (
                  <div key={i} className="msg user" data-msg={i}>
                    <div className="b">{m.text}</div>
                  </div>
                ) : (
                  <div
                    key={i}
                    data-msg={i}
                    ref={i === lastAgent ? lastMsg : undefined}
                    className="msg agent"
                    role={i === lastAgent && !thinking ? "status" : undefined}
                  >
                    <Mascot size={30} state={i === lastAgent ? moodOf(reply, !!error) : "idle"} />
                    <div className="b">
                      <AgentText
                        text={badgeNoAi ? withoutNoAi(brief(m.text, i === lastAgent)) : brief(m.text, i === lastAgent)}
                        synthetic={synthetic}
                        choice={i === lastAgent && reply ? { reply, busy, onSend: send } : undefined}
                      />
                    </div>
                  </div>
                );
              // One key for the block: it moves under a new last answer without losing its state.
              const old = m.role === "agent" && i !== lastAgent ? m.reply : undefined;
              if (old && reply) return [msg, <EarlierChoices key={`earlier-${i}`} reply={old} current={reply} busy={busy} onSend={send} />];
              return i === lastAgent && block ? [msg, block] : [msg];
            })}
            {lastAgent < 0 && block}
            {thinking && <Thinking variant={asked % 2 ? "banana" : "plane"} />}
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            <div ref={end} />
          </div>
        </div>
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
          <details className="hint composer-hint">
            <summary aria-label="Откуда цены">
              <span className="qmark" aria-hidden="true">?</span>
            </summary>
            <p>
              {priceSources(reply?.plan.offers ?? [])}
            </p>
          </details>
        </form>
      </section>
      <aside id="roadmap" className="roadmap" aria-label="Путь поездки">
        <header className="hd">
          <b>Путь поездки</b>
          <button
            type="button"
            className="icon road-close"
            aria-label="Закрыть путь поездки"
            onClick={() => setRoad(false)}
          >
            ✕
          </button>
        </header>
        <Roadmap reply={reply} doneAt={doneAt} onJump={jump} />
        <footer className="sources" aria-label="Источники данных">
          {/* the attribution stays one click away (ODbL), the panel stays quiet */}
          <details>
            <summary>
              Данные <span className="qmark" aria-hidden="true">?</span>
            </summary>
          {SOURCES.map((x) => (
            <span key={x.name}>
              {x.text}, лицензия{" "}
              <a href={x.url} target="_blank" rel="noreferrer">
                {x.licence}
              </a>
              .
            </span>
          ))}
          <span>
            {dataNote(reply?.plan.offers ?? [])} Климат и время в пути — оценки команды. Оплата
            имитируется.
          </span>
          </details>
        </footer>
      </aside>
    </div>
  );
}
