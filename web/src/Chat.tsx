import { useEffect, useState } from "react";
import { Money } from "./Board";
import { VERBS, VERB_MS } from "./brand";
import type { Stage } from "./contracts";
import { Mascot } from "./Mascot";

// The five steps of the top bar; the seven stages of the backend fall into them.
const STEPS: { label: string; stages: Stage[] }[] = [
  { label: "Уточнение", stages: ["clarify"] },
  { label: "Вдохновение", stages: ["inspire", "select"] },
  { label: "Сравнение", stages: ["compare", "assemble"] },
  { label: "Корзина", stages: ["checkout"] },
  { label: "Бронь", stages: ["booked"] },
];

export function StagePills({ stage }: { stage: Stage | null }) {
  const at = stage ? STEPS.findIndex((s) => s.stages.includes(stage)) : -1;
  return (
    <ol className="stage" aria-label="Этапы поездки">
      {STEPS.map((s, i) => (
        <li
          key={s.label}
          className={i === at ? "now" : i < at ? "done" : undefined}
          aria-current={i === at ? "step" : undefined}
        >
          {s.label}
        </li>
      ))}
    </ol>
  );
}

// While /api/chat answers: the mascot bobs and a travel verb changes every VERB_MS.
export function Thinking() {
  const [i, setI] = useState(() => Math.floor(Math.random() * VERBS.length));
  useEffect(() => {
    const t = setInterval(() => setI((n) => (n + 1) % VERBS.length), VERB_MS);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="think" aria-label="Агент думает">
      <Mascot size={30} state="thinking" />
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
    return <p className="typing caret">{words.slice(0, shown).join("")}</p>;
  }
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
