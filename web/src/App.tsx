import { useEffect, useRef, useState } from "react";
import { ApiError, cartAction, chat, checkout, getSession } from "./api";
import { Board } from "./Board";
import type { ChatReply, Offer } from "./contracts";
import { SOURCES, describe } from "./format";

type Msg = { role: "user" | "agent"; text: string };
type Ui = { messages: Msg[]; removed: Offer[] };

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

function saveUi(sid: string, ui: Ui) {
  try {
    localStorage.setItem(uiKey(sid), JSON.stringify(ui));
  } catch {
    // storage full or blocked: the session still lives on the server
  }
}

const userError = (e: unknown) =>
  e instanceof ApiError ? e.message : "Что-то пошло не так. Попробуйте ещё раз.";

export function App() {
  const [text, setText] = useState("");
  const [reply, setReply] = useState<ChatReply | null>(null);
  const [ui, setUi] = useState<Ui>({ messages: [], removed: [] });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [restoring, setRestoring] = useState(() => !!readSid());
  const end = useRef<HTMLDivElement>(null);

  const sid = reply?.session_id ?? null;

  function update(next: Ui, id = sid) {
    setUi(next);
    if (id) saveUi(id, next);
  }

  // Reload restores the session: the server keeps the last reply, the browser keeps the chat.
  useEffect(() => {
    const stored = readSid();
    if (!stored) return;
    let live = true;
    getSession(stored)
      .then((r) => {
        if (!live) return;
        if (!r) {
          dropSid();
          return;
        }
        const saved = loadUi(stored);
        const messages = saved.messages.length
          ? saved.messages
          : [{ role: "agent" as const, text: describe(r) }];
        setReply(r);
        setUi({ ...saved, messages });
      })
      .catch((e) => live && setError(`Не удалось восстановить поездку. ${userError(e)}`))
      .finally(() => live && setRestoring(false));
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [ui.messages.length]);

  async function send(message: string) {
    const m = message.trim();
    if (!m || busy) return;
    setBusy(true);
    setError(null);
    const withUser = { ...ui, messages: [...ui.messages, { role: "user" as const, text: m }] };
    setUi(withUser);
    try {
      const r = await chat({ session_id: sid, message: m });
      writeSid(r.session_id);
      setReply(r);
      update(
        { ...withUser, messages: [...withUser.messages, { role: "agent", text: describe(r) }] },
        r.session_id,
      );
      setText("");
    } catch (e) {
      setError(userError(e));
    } finally {
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
      update({ ...ui, removed });
      setReply({ ...(fresh ?? reply), cart });
    } catch (e) {
      setError(userError(e));
    } finally {
      setBusy(false);
    }
  }

  async function onCheckout() {
    if (!reply || busy) return;
    setBusy(true);
    setError(null);
    const id = reply.session_id;
    try {
      const voucher = await checkout({ session_id: id, confirm_total_rub: reply.cart.total_rub });
      const fresh = await getSession(id).catch(() => null);
      setReply(fresh?.voucher ? fresh : { ...reply, stage: "booked", voucher });
      const text = `Бронь ${voucher.booking_id} оформлена. Оплата имитирована.`;
      update({ ...ui, messages: [...ui.messages, { role: "agent", text }] });
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

  function reset() {
    dropSid();
    setReply(null);
    setUi({ messages: [], removed: [] });
    setError(null);
    setText("");
  }

  const lastAgent = ui.messages.map((m) => m.role).lastIndexOf("agent");

  return (
    <div className="app">
      <section className="chat" aria-label="Чат">
        <header>
          <h1>О!дин запрос</h1>
          {reply && (
            <button type="button" className="link" onClick={reset} disabled={busy}>
              Новая поездка
            </button>
          )}
        </header>
        <div className="messages" role="log" aria-live="polite">
          {!ui.messages.length && !restoring && (
            <div className="hint">
              <p>Опишите поездку одним сообщением. Агент подберёт варианты и соберёт корзину.</p>
              {EXAMPLES.map((ex) => (
                <button key={ex} type="button" className="example" onClick={() => setText(ex)}>
                  {ex}
                </button>
              ))}
            </div>
          )}
          {restoring && <p className="muted">Восстанавливаю поездку…</p>}
          {ui.messages.map((m, i) => (
            <div
              key={i}
              className={`msg ${m.role}`}
              role={i === lastAgent ? "status" : undefined}
            >
              {m.text}
            </div>
          ))}
          {reply && reply.questions.length > 0 && (
            <div className="questions">
              {reply.questions.map((q) => (
                <div key={q.text}>
                  <p>{q.text}</p>
                  {q.options.map((o) => (
                    <button key={o} type="button" disabled={busy} onClick={() => send(o)}>
                      {o}
                    </button>
                  ))}
                </div>
              ))}
            </div>
          )}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <div ref={end} />
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void send(text);
          }}
        >
          <input
            aria-label="Запрос"
            placeholder="Например: море в ноябре, двое взрослых, до 100 тысяч"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <button type="submit" disabled={busy || !text.trim()}>
            {busy ? "Думаю…" : "Отправить"}
          </button>
        </form>
      </section>
      <main className="trip" aria-label="Доска поездки">
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
          <span>Цены и рейсы синтетические, оплата имитируется.</span>
        </footer>
      </main>
    </div>
  );
}
