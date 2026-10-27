import { useRef, useState } from "react";
import type { ReactNode } from "react";
import type { TripSpec } from "./contracts";
import {
  budgetMessage,
  datesMessage,
  originMessage,
  paramChips,
  partyMessage,
  type ParamChip,
  type ParamId,
} from "./params";

const CITIES = ["Москва", "Санкт-Петербург", "Казань", "Екатеринбург", "Новосибирск", "Сочи"];
const BUDGETS = [50000, 100000, 200000];

type EditorProps = { spec: TripSpec; chip: ParamChip; done: (message: string | null) => void };

function Form({ title, valid, done, children }: {
  title: string;
  valid: boolean;
  done: (send: boolean) => void;
  children: ReactNode;
}) {
  return (
    <form
      className="editor"
      aria-label={`Изменить: ${title}`}
      onSubmit={(e) => {
        e.preventDefault();
        done(true);
      }}
    >
      {children}
      <div className="editor-actions">
        <button type="submit" disabled={!valid}>
          Готово
        </button>
        <button type="button" className="link" onClick={() => done(false)}>
          Отмена
        </button>
      </div>
    </form>
  );
}

// A value the agent chose is sent back even when it stays: the user confirmed it.
function OriginEditor({ spec, chip, done }: EditorProps) {
  const [city, setCity] = useState(spec.origin ?? "");
  const text = city.trim();
  return (
    <Form
      title={chip.label}
      valid={text !== ""}
      done={(send) => done(send && (chip.assumed || text !== spec.origin) ? originMessage(text) : null)}
    >
      <label>
        Город вылета
        <input autoFocus value={city} onChange={(e) => setCity(e.target.value)} />
      </label>
      <div className="options">
        {CITIES.map((c) => (
          <button key={c} type="button" className="alt" onClick={() => setCity(c)}>
            {c}
          </button>
        ))}
      </div>
    </Form>
  );
}

function DatesEditor({ spec, chip, done }: EditorProps) {
  const [from, setFrom] = useState(spec.date_from ?? "");
  const [to, setTo] = useState(spec.date_to ?? "");
  const today = new Date().toLocaleDateString("sv");
  const changed = from !== spec.date_from || to !== spec.date_to;
  return (
    <Form
      title={chip.label}
      valid={!!from && !!to && to > from}
      done={(send) => done(send && (chip.assumed || changed) ? datesMessage(from, to) : null)}
    >
      <label>
        Заезд
        <input autoFocus type="date" min={today} value={from} onChange={(e) => setFrom(e.target.value)} />
      </label>
      <label>
        Выезд
        <input type="date" min={from || today} value={to} onChange={(e) => setTo(e.target.value)} />
      </label>
    </Form>
  );
}

function PartyEditor({ spec, chip, done }: EditorProps) {
  const [adults, setAdults] = useState(spec.adults);
  const [ages, setAges] = useState(spec.children_ages.map(String));
  const numbers = ages.map((a) => (a.trim() === "" ? NaN : Number(a)));
  const valid = numbers.every((n) => Number.isInteger(n) && n >= 0 && n <= 17);
  const same = adults === spec.adults && numbers.join() === spec.children_ages.join();
  return (
    <Form
      title={chip.label}
      valid={valid}
      done={(send) => done(send && valid && (chip.assumed || !same) ? partyMessage(adults, numbers) : null)}
    >
      <div className="stepper">
        <span id="adults-label">Взрослые</span>
        <button type="button" className="alt" aria-label="Меньше взрослых" disabled={adults <= 1}
          onClick={() => setAdults(adults - 1)}>
          −
        </button>
        <output aria-labelledby="adults-label">{adults}</output>
        <button type="button" className="alt" aria-label="Больше взрослых" disabled={adults >= 9}
          onClick={() => setAdults(adults + 1)}>
          +
        </button>
      </div>
      {ages.map((a, i) => (
        <div key={i} className="stepper">
          <label>
            Возраст ребёнка {i + 1}
            <input type="number" min={0} max={17} inputMode="numeric" placeholder="0–17"
              value={a} onChange={(e) => setAges(ages.map((x, j) => (j === i ? e.target.value : x)))} />
          </label>
          <button type="button" className="alt" aria-label={`Убрать ребёнка ${i + 1}`}
            onClick={() => setAges(ages.filter((_, j) => j !== i))}>
            ×
          </button>
        </div>
      ))}
      <button type="button" className="alt" onClick={() => setAges([...ages, ""])}>
        + Ребёнок
      </button>
      {ages.length > 0 && <p className="muted">От возраста зависят цена и номер.</p>}
    </Form>
  );
}

function BudgetEditor({ spec, done, chip }: EditorProps) {
  const [value, setValue] = useState(spec.budget_rub === null ? "" : String(spec.budget_rub));
  const n = Number(value);
  return (
    <Form
      title={chip.label}
      valid={Number.isFinite(n) && n > 0}
      done={(send) => done(send && n !== spec.budget_rub ? budgetMessage(n) : null)}
    >
      <label>
        На всю поездку, ₽
        <input autoFocus type="number" min={1} inputMode="numeric" value={value}
          onChange={(e) => setValue(e.target.value)} />
      </label>
      <div className="options">
        {BUDGETS.map((b) => (
          <button key={b} type="button" className="alt" onClick={() => setValue(String(b))}>
            до {b.toLocaleString("ru-RU")} ₽
          </button>
        ))}
      </div>
    </Form>
  );
}

const EDITORS: Record<ParamId, (p: EditorProps) => ReactNode> = {
  origin: OriginEditor,
  dates: DatesEditor,
  party: PartyEditor,
  budget: BudgetEditor,
};

// The parameters the agent used for the plan. A value it chose itself is marked; a click opens a
// small editor and «Готово» sends the change to the chat like a typed message.
export function ParamsBar({ spec, disabled, onSend }: {
  spec: TripSpec;
  disabled: boolean;
  onSend: (text: string) => void;
}) {
  const [open, setOpen] = useState<ParamId | null>(null);
  const buttons = useRef<Partial<Record<ParamId, HTMLButtonElement | null>>>({});
  const chips = paramChips(spec);

  function close(id: ParamId, message: string | null) {
    setOpen(null);
    buttons.current[id]?.focus();
    if (message) onSend(message);
  }

  return (
    <div
      className="params"
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) close(open, null);
      }}
    >
      <ul>
        {chips.map((c) => (
          <li key={c.id}>
            <button
              type="button"
              ref={(el) => {
                buttons.current[c.id] = el;
              }}
              className={`param${c.assumed ? " assumed" : ""}`}
              aria-expanded={open === c.id}
              disabled={disabled}
              title={c.assumed ? `Агент выбрал сам. ${c.why.join("; ")}` : undefined}
              onClick={() => setOpen(open === c.id ? null : c.id)}
            >
              <span className="param-name">{c.label}</span>
              <strong className="param-value">{c.value}</strong>
              {c.assumed && <span className="param-tag">агент выбрал</span>}
            </button>
          </li>
        ))}
      </ul>
      {chips.some((c) => c.assumed) && (
        <p className="muted">Пунктир — значения, которые агент выбрал сам. Нажмите на параметр, чтобы изменить.</p>
      )}
      {chips.map((c) => {
        const Editor = EDITORS[c.id];
        return open === c.id ? <Editor key={c.id} spec={spec} chip={c} done={(m) => close(c.id, m)} /> : null;
      })}
    </div>
  );
}
