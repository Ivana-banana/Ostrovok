import { type ReactNode, useEffect, useId, useState } from "react";
import { Money, Price } from "./Board";
import { VERBS, VERB_MS } from "./brand";
import type { ChatReply, Mode, Stage } from "./contracts";
import { MODES, offerParts, type Point, type Ride } from "./format";
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
    <div className={`think ${variant}`} role="status" aria-busy="true" aria-label="Агент думает">
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

// Highlights of a backend line: temperatures marked, prices with «синтетика» (TRAP-7).
function Rich({ text, synthetic }: { text: string; synthetic: boolean }) {
  return (
    <>
      {text.split(TEMP).map((part, i) =>
        i % 2 ? <mark key={i}>{part}</mark> : <Money key={i} text={part} synthetic={synthetic} />,
      )}
    </>
  );
}

function Line({ text, synthetic }: { text: string; synthetic: boolean }) {
  return (
    <p className={ASIDE.test(text) ? "aside" : undefined}>
      <Rich text={text} synthetic={synthetic} />
    </p>
  );
}

// #476: the route line ●━○ between places and times; screen readers and a copy get «→».
// The gradient and the filter need an id that is unique on the page: many lines are drawn.
function Arrow() {
  const id = useId().replace(/:/g, "");
  return (
    <>
      <svg className="arrow" viewBox="0 0 60 16" aria-hidden="true">
        <defs>
          <linearGradient id={`g${id}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" className="route-hi" />
            <stop offset="1" className="route-lo" />
          </linearGradient>
          <filter id={`f${id}`} x="-20%" y="-80%" width="140%" height="260%">
            <feDropShadow dx="0" dy="1" stdDeviation="0.8" floodColor="#000" floodOpacity=".25" />
          </filter>
        </defs>
        <g filter={`url(#f${id})`}>
          <rect x="8" y="5" width="44" height="6" rx="3" fill={`url(#g${id})`} />
          <circle className="route-from" cx="6" cy="8" r="5" />
          <circle className="route-to" cx="54" cy="8" r="4.2" />
        </g>
      </svg>
      <span className="sr-only">→</span>
    </>
  );
}

// Line icons of the items, 24×24, stroke in currentColor.
const ICONS: Record<"hotel" | Mode | "transfer" | "extra" | "included", ReactNode> = {
  hotel: <path d="M3 6v13M3 15h18v4M21 15v-3a3 3 0 0 0-3-3h-8v6M6.5 12.5h.01" />,
  flight: <path d="M21 4 3 11l7 2 2 7 9-16ZM10 13l5-5" />,
  train: <path d="M7 3h10a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2ZM5 10h14M9 13h.01M15 13h.01M8 21l2-5M16 21l-2-5" />,
  car: <path d="M4 16v-4l2-5h12l2 5v4H4ZM4 12h16M7.5 19a1.5 1.5 0 0 0 0-3M16.5 19a1.5 1.5 0 0 0 0-3M7.5 16v3M16.5 16v3" />,
  transfer: <path d="M3 15V9l2-3h10l4 3h2v6H3ZM3 11h18M7 18a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM17 18a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z" />,
  extra: <path d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 8v8M8 12h8" />,
  included: <path d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM8 12l3 3 5-6" />,
};

function Icon({ name }: { name: keyof typeof ICONS }) {
  return (
    <svg className="pt-ic" viewBox="0 0 24 24" aria-hidden="true">
      {ICONS[name]}
    </svg>
  );
}

function Route({ ride }: { ride: Ride }) {
  return (
    <>
      {ride.from} <Arrow /> {ride.to}
    </>
  );
}

const facts = (list: string[]) => list.join(" · ");
const LABELS = { hotel: "Отель", transfer: "Трансферы", extra: "Доп. услуга", included: "Включено" };

// One item of the proposed trip: icon, label and price on the first row, the facts below.
function PointRow({ point: p, synthetic }: { point: Point; synthetic: boolean }) {
  const icon = p.kind === "leg" ? p.mode : p.kind;
  const label = p.kind === "leg" ? p.label : LABELS[p.kind];
  return (
    <li className="pt">
      <Icon name={icon} />
      <div className="pt-body">
        <div className="pt-row">
          <span className="pt-name">
            <b>{label}</b>
            {p.kind === "hotel" && <> {p.name}</>}
            {p.kind === "leg" && <span className="pt-mode"> · {MODES[p.mode]}</span>}
          </span>
          {"price" in p && (
            <span className="pt-price">
              <Rich text={p.price} synthetic={synthetic} />
            </span>
          )}
        </div>
        {p.kind === "hotel" && <div className="pt-sub">{facts(p.facts)}</div>}
        {p.kind === "included" && <div className="pt-sub">{facts(p.facts)}</div>}
        {p.kind === "extra" && <div className="pt-sub">{p.name}</div>}
        {p.kind === "leg" && (
          <>
            <div className="pt-sub">
              <Route ride={p.ride} />
            </div>
            <div className="pt-sub">
              {p.start}
              {p.zones.length > 1 && ` ${p.zones[0]}`} <Arrow /> {p.end} · {p.zones.at(-1)}
            </div>
            {p.ride.facts.length > 0 && <div className="pt-sub">{facts(p.ride.facts)}</div>}
          </>
        )}
        {p.kind === "transfer" &&
          p.rides.map((r, i) => (
            <div key={i} className="pt-sub">
              <Route ride={r} /> · {facts(r.facts)}
            </div>
          ))}
      </div>
    </li>
  );
}

// The text of an agent message. It appears whole and formatted (highlights, «синтетика»):
// until the answer is ready the chat shows only the Thinking indicator (#450).
// #468: the proposed trip is a list, one item per line; every price has a small «синтетика»
// chip (TRAP-7). The details are folded under «Подробнее».
export function AgentText({ text, synthetic }: { text: string; synthetic: boolean }) {
  const parts = offerParts(text);
  if (!parts) return <Lines text={text} synthetic={synthetic} />;
  const lines = (part: string[]) => part.map((l, i) => <Line key={i} text={l} synthetic={synthetic} />);
  return (
    <>
      {lines(parts.head)}
      <p>Предлагаю:</p>
      {parts.points ? (
        <ul className="plain offer points">
          {parts.points.map((p, i) => (
            <PointRow key={i} point={p} synthetic={synthetic} />
          ))}
          <li className="pt sum">
            <span className="pt-ic" />
            <div className="pt-row">
              <b>Итого</b>
              <span className="pt-price">
                <Rich text={parts.total} synthetic={synthetic} />
              </span>
            </div>
          </li>
        </ul>
      ) : (
        <ul className="plain offer">
          {parts.items.map((it, i) => (
            <li key={i}>
              <b>{it.label}:</b> <Rich text={it.text} synthetic={synthetic} />
            </li>
          ))}
          <li>
            <b>Итого</b> <Rich text={parts.total} synthetic={synthetic} />
          </li>
        </ul>
      )}
      {parts.more.length > 0 && (
        <details className="more">
          <summary>Подробнее</summary>
          {lines(parts.more)}
        </details>
      )}
      {lines(parts.tail)}
    </>
  );
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
