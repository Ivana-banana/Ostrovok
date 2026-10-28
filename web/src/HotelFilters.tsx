import { useRef, useState } from "react";
import type { Meal, TripSpec } from "./contracts";
import { MEALS, rub } from "./format";
import { MEAL_MESSAGES, REFUNDABLE_MESSAGE, hotelBudgetMessage, nightBudget } from "./params";

type FilterId = "meal" | "refund" | "budget";

const MEAL_ORDER: Meal[] = ["BB", "HB", "FB", "AI", "RO"];
const NIGHT_BUDGETS = [5000, 10000, 20000];
const TOTAL_BUDGETS = [30000, 60000, 100000];

function budgetValue(spec: TripSpec): string {
  if (spec.hotel_budget_rub === null) return "не задан";
  const night = nightBudget(spec);
  return night === null ? `${rub(spec.hotel_budget_rub)} за все ночи` : `${rub(night)} за ночь`;
}

function BudgetEditor({ spec, done }: { spec: TripSpec; done: (message: string | null) => void }) {
  const perNight = !!spec.nights;
  const start = perNight ? nightBudget(spec) : spec.hotel_budget_rub;
  const [value, setValue] = useState(start === null ? "" : String(start));
  const n = Number(value);
  return (
    <form
      className="editor"
      aria-label="Изменить: бюджет отеля"
      onSubmit={(e) => {
        e.preventDefault();
        done(n !== start ? hotelBudgetMessage(n, perNight) : null);
      }}
    >
      <label>
        {perNight ? "Отель за ночь, ₽" : "Отель за все ночи, ₽"}
        <input autoFocus type="number" min={1} inputMode="numeric" value={value}
          onChange={(e) => setValue(e.target.value)} />
      </label>
      <div className="options">
        {(perNight ? NIGHT_BUDGETS : TOTAL_BUDGETS).map((b) => (
          <button key={b} type="button" className="alt" onClick={() => setValue(String(b))}>
            до {b.toLocaleString("ru-RU")} ₽
          </button>
        ))}
      </div>
      <div className="editor-actions">
        <button type="submit" disabled={!Number.isFinite(n) || n <= 0}>
          Готово
        </button>
        <button type="button" className="link" onClick={() => done(null)}>
          Отмена
        </button>
      </div>
    </form>
  );
}

// Hotel filters next to ParamsBar (#431): meal, free cancellation, hotel budget. A choice goes to
// the chat like a typed message; the agent re-ranks the hotels and the cart, the search stays.
// The spec has no «any» value for the meal and the cancellation: a chosen filter is cleared in the chat.
export function HotelFilters({ spec, disabled, onSend }: {
  spec: TripSpec;
  disabled: boolean;
  onSend: (text: string) => void;
}) {
  const [open, setOpen] = useState<FilterId | null>(null);
  const buttons = useRef<Partial<Record<FilterId, HTMLButtonElement | null>>>({});
  const ref = (id: FilterId) => (el: HTMLButtonElement | null) => {
    buttons.current[id] = el;
  };

  function close(id: FilterId, message: string | null) {
    setOpen(null);
    buttons.current[id]?.focus();
    if (message) onSend(message);
  }

  const refundable = spec.refundable === true;
  return (
    <div
      className="params hotel-filters"
      role="group"
      aria-label="Фильтры отелей"
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) close(open, null);
      }}
    >
      <ul>
        <li>
          <button type="button" ref={ref("meal")} className={`param${spec.meal ? " chosen" : ""}`}
            aria-expanded={open === "meal"} disabled={disabled}
            onClick={() => setOpen(open === "meal" ? null : "meal")}>
            <span className="param-name">Питание</span>
            <strong className="param-value">{spec.meal ? MEALS[spec.meal] : "любое"}</strong>
          </button>
        </li>
        <li>
          <button type="button" ref={ref("refund")} className={`param${refundable ? " chosen" : ""}`}
            aria-pressed={refundable} disabled={disabled || refundable}
            title={refundable ? "Фильтр включён. Снять его можно сообщением в чате." : undefined}
            onClick={() => onSend(REFUNDABLE_MESSAGE)}>
            <span className="param-name">Условия отмены</span>
            <strong className="param-value">{refundable ? "бесплатная" : "любая"}</strong>
          </button>
        </li>
        <li>
          <button type="button" ref={ref("budget")}
            className={`param${spec.hotel_budget_rub !== null ? " chosen" : ""}`}
            aria-expanded={open === "budget"} disabled={disabled}
            onClick={() => setOpen(open === "budget" ? null : "budget")}>
            <span className="param-name">Бюджет отеля</span>
            <strong className="param-value">{budgetValue(spec)}</strong>
          </button>
        </li>
      </ul>
      {open === "meal" && (
        <div className="editor" role="group" aria-label="Изменить: питание">
          <div className="options">
            {MEAL_ORDER.map((m) => (
              <button key={m} type="button" className="alt" aria-pressed={spec.meal === m}
                onClick={() => close("meal", spec.meal === m ? null : MEAL_MESSAGES[m])}>
                {MEALS[m].replace(/^./, (c) => c.toUpperCase())}
              </button>
            ))}
          </div>
        </div>
      )}
      {open === "budget" && <BudgetEditor spec={spec} done={(m) => close("budget", m)} />}
    </div>
  );
}
