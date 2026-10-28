import { type ReactNode, useEffect, useState } from "react";
import { Money, Price } from "./Board";
import { VERBS, VERB_MS } from "./brand";
import type { ChatReply, Stage } from "./contracts";
import { partyText } from "./params";
import { Mascot } from "./Mascot";

// The five steps of the trip roadmap; the seven stages of the backend fall into them.
export const STEPS: { label: string; what: string; stages: Stage[] }[] = [
  { label: "Уточнение", what: "Расскажите, куда и когда", stages: ["clarify"] },
  { label: "Вдохновение", what: "Выберите направление", stages: ["inspire", "select"] },
  { label: "Сравнение", what: "Сравните отели и транспорт", stages: ["compare"] },
  // assemble: the plan is built and the chat asks «Оформляем?» — the step of the cart
  { label: "Корзина", what: "Проверьте план и стыковки", stages: ["assemble", "checkout"] },
  { label: "Бронь", what: "Оформление — имитация", stages: ["booked"] },
];

export const stepOf = (stage: Stage | null) => (stage ? STEPS.findIndex((s) => s.stages.includes(stage)) : -1);

// What the user does now, by stage (the current step of the roadmap).
const NOW: Record<Stage, string> = {
  clarify: "ответьте на вопросы агента",
  inspire: "выберите карточку направления",
  select: "выберите направление",
  compare: "выберите отель и рейс или соберите план",
  assemble: "проверьте стыковки и положите план в корзину",
  checkout: "проверьте корзину и подтвердите бронь",
  booked: "поездка оформлена, ваучер в чате",
};

const shortDate = (iso: string | null) => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}` : "");

// The outcome of a done step in a few words, only from the facts of the reply.
function outcome(i: number, r: ChatReply): ReactNode {
  const { spec, offers, selected } = r.plan;
  if (i === 0) {
    const dates = spec.date_from && spec.date_to ? `${shortDate(spec.date_from)}–${shortDate(spec.date_to)}` : null;
    return [partyText(spec.adults, spec.children_ages), dates].filter(Boolean).join(" · ");
  }
  if (i === 1) return spec.destinations.join(", ") || null;
  if (i === 2) {
    const hotel = offers.find((o) => o.kind === "hotel" && selected.includes(o.offer_id));
    const legs = offers.filter((o) => o.kind === "transport");
    const rows = legs.flatMap((o, n) => (selected.includes(o.offer_id) ? [n + 1] : []));
    const way = rows.length ? `${rows.length > 1 ? "рейсы" : "рейс"} ${rows.join(", ")}` : null;
    return [hotel?.title, way].filter(Boolean).join(" · ") || null;
  }
  if (i === 3) {
    const items = r.cart.items.length ? r.cart.items : offers.filter((o) => selected.includes(o.offer_id));
    if (!items.length) return null;
    const total = r.cart.items.length ? r.cart.total_rub : items.reduce((a, o) => a + o.price_rub, 0);
    return <Price value={total} synthetic={items.some((o) => o.synthetic)} />;
  }
  return r.voucher ? `Бронь ${r.voucher.booking_id}` : null;
}

// The roadmap of the trip: where the user is and what is done (#434). An ordered list, not
// buttons: only a done step with a known message is a link back to that place of the chat.
export function Roadmap({ reply, doneAt = [], onJump }: {
  reply: ChatReply | null;
  doneAt?: (number | null)[]; // index of the message that finished the step
  onJump?: (message: number) => void;
}) {
  const stage = reply?.stage ?? null;
  const at = stepOf(stage);
  return (
    <ol className="road" aria-label="Этапы поездки">
      {STEPS.map((s, i) => {
        const done = i < at || stage === "booked";
        const now = i === at;
        const result = reply && done ? outcome(i, reply) : null;
        const jump = done && !now ? doneAt[i] : null;
        return (
          <li
            key={s.label}
            className={[done && "done", now && "now"].filter(Boolean).join(" ") || undefined}
            aria-current={now ? "step" : undefined}
          >
            <span className="dot" aria-hidden="true">
              {done ? "✓" : i + 1}
            </span>
            <div className="road-text">
              {jump !== null && jump !== undefined && onJump ? (
                <button type="button" className="label link" title="Показать в чате" onClick={() => onJump(jump)}>
                  {s.label}
                </button>
              ) : (
                <b className="label">{s.label}</b>
              )}
              {done && <span className="sr-only"> — пройден</span>}
              <span className="what">{s.what}</span>
              {result && <span className="result">{result}</span>}
              {now && stage && <span className="nowline">Сейчас: {NOW[stage]}</span>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

// Below 1100 px the roadmap column hides: the caption of the step and a thin bar open it.
export function StepCaption({ stage, onOpen }: { stage: Stage | null; onOpen: () => void }) {
  const at = stepOf(stage);
  if (at < 0) return null;
  return (
    <button type="button" className="step-caption" aria-controls="roadmap" onClick={onOpen}>
      <span>
        Шаг {at + 1} из {STEPS.length} · {STEPS[at].label}
      </span>
      <span className="step-bar" aria-hidden="true">
        <i style={{ width: `${((at + 1) / STEPS.length) * 100}%` }} />
      </span>
    </button>
  );
}

// Routes of the paper plane in a 64×32 box; the plane flies nose first along the dash.
export const ROUTES = [
  "M4 26 Q32 -8 60 26",
  "M2 18 C16 2 24 34 38 18 S56 4 62 18",
  "M6 26 C6 2 58 2 58 26",
  "M2 24 L62 8",
];

function Plane() {
  const [d] = useState(() => ROUTES[Math.floor(Math.random() * ROUTES.length)]);
  return (
    <span className="route">
      <svg className="path" viewBox="0 0 64 32">
        <path d={d} />
      </svg>
      <svg className="plane" style={{ offsetPath: `path("${d}")` }} viewBox="-12 -12 24 24">
        <path d="M11 0L-2 -2.5L-6 -10L-8.5 -10L-6 -2L-10 -1.5L-12 -5L-13 -5L-12 0L-13 5L-12 5L-10 1.5L-6 2L-8.5 10L-6 10L-2 2.5Z" />
      </svg>
    </span>
  );
}

// While /api/chat answers: a tossed banana or a paper plane (they take turns between answers)
// and a travel verb that changes every VERB_MS.
export function Thinking({ variant = "banana" }: { variant?: "banana" | "plane" }) {
  const [i, setI] = useState(() => Math.floor(Math.random() * VERBS.length));
  useEffect(() => {
    const t = setInterval(() => setI((n) => (n + 1) % VERBS.length), VERB_MS);
    return () => clearInterval(t);
  }, []);
  return (
    <div className={`think ${variant}`} aria-label="Агент думает">
      {variant === "plane" ? <Plane /> : <Mascot size={30} state="thinking" />}
      <span>
        <span className="verb">{VERBS[i]}</span>
        <span className="dots" />
      </span>
    </div>
  );
}

// Lines of the backend text that say what the agent assumed or did not check: a muted line.
const ASIDE = /^(Допущения|Не проверяю)/;
const TEMP = /(\d+(?:[.,]\d+)?\s?°C)/;

function Line({ text, synthetic }: { text: string; synthetic: boolean }) {
  return (
    <p className={ASIDE.test(text) ? "aside" : undefined}>
      {text.split(TEMP).map((part, i) =>
        i % 2 ? <mark key={i}>{part}</mark> : <Money key={i} text={part} synthetic={synthetic} />,
      )}
    </p>
  );
}

const WORD_MS = 35;
const TYPE_MAX_MS = 1500;

// The text of an agent message. A new one appears word by word, then gets its highlights.
export function AgentText({ text, synthetic, typing, onTick, onDone }: {
  text: string;
  synthetic: boolean;
  typing: boolean;
  onTick?: () => void;
  onDone?: () => void;
}) {
  const words = text.split(/(\s+)/); // separators kept: new lines stay
  const [shown, setShown] = useState(typing ? 0 : words.length);
  useEffect(() => {
    if (!typing) return;
    const total = Math.ceil(words.length / 2);
    const step = Math.min(WORD_MS, TYPE_MAX_MS / Math.max(total, 1));
    let n = 0;
    const t = setInterval(() => {
      n += 2;
      setShown(n);
      onTick?.();
      if (n >= words.length) {
        clearInterval(t);
        onDone?.();
      }
    }, step);
    return () => clearInterval(t);
  }, [typing]); // the text of a typing message does not change
  if (typing && shown < words.length) {
    // A screen reader gets the whole text at once, not the pieces of the typing.
    return (
      <>
        <div className="sr-only">
          <Lines text={text} synthetic={synthetic} />
        </div>
        <p className="typing caret" aria-hidden="true">
          {words.slice(0, shown).join("")}
        </p>
      </>
    );
  }
  return <Lines text={text} synthetic={synthetic} />;
}

function Lines({ text, synthetic }: { text: string; synthetic: boolean }) {
  return (
    <>
      {text
        .split("\n")
        .filter((l) => l.trim())
        .map((l, i) => (
          <Line key={i} text={l} synthetic={synthetic} />
        ))}
    </>
  );
}
