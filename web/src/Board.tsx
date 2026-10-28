import { type CSSProperties, type ReactNode, useEffect, useRef, useState } from "react";
import { motionOk } from "./brand";
import type {
  Bundle,
  Cart,
  ChatReply,
  Destination,
  Guest,
  HotelOffer,
  Offer,
  Plan,
  Priority,
  Stage,
  TransportOffer,
  TripSpec,
  Voucher,
} from "./contracts";
import {
  MEALS,
  MODES,
  browserTime,
  changeValue,
  clock,
  duration,
  fieldLabel,
  guestsText,
  hours,
  localDate,
  localTime,
  movePriority,
  ordinal,
  PRIORITIES,
  priorityMessage,
  rub,
  zonedTime,
  zoneLabel,
} from "./format";
import { HotelFilters } from "./HotelFilters";
import { ParamsBar } from "./ParamsBar";
import { otherAssumptions } from "./params";

export type BoardProps = {
  reply: ChatReply;
  busy: boolean;
  removed: Offer[]; // items removed from the cart in this session, to put back
  onSend: (text: string) => void;
  onCart: (action: "add" | "remove", offerId: string) => void;
  onCheckout: (guest: Guest | null) => void;
};

// The source of a real price (#547). The contract has no source field: derive it from the kind,
// for transport from the mode (backend api/answers.py PRICE_SOURCES, #567).
// The chip and its tooltip tell where a non-synthetic number comes from.
const PRICE_SOURCES: Record<string, { chip: string; hint: string }> = {
  hotel: { chip: "101hotels", hint: "итог по цене «от» 101hotels; питание и тариф — оценка" },
  flight: { chip: "Авиасейлс", hint: "самый дешёвый билет из кэша поиска Авиасейлс, не цена брони" },
  train: { chip: "tutu", hint: "цена и расписание поезда с tutu.ru, не цена брони" },
};
const OTHER_SOURCE = { chip: "реальные данные", hint: "цена не синтетическая" };
// A sum of real and synthetic prices (TRAP-7, #567): neither «синтетика» nor a source alone.
const MIXED = "часть цен — синтетика";
export type PriceSource = { chip: string; hint: string };
type Priced = { kind: string; mode?: string; synthetic?: boolean };
const keyOf = (o: Priced) => (o.kind === "transport" ? (o.mode ?? "flight") : o.kind);
export const sourceOf = (items: Priced[]): PriceSource => {
  const real = items.filter((o) => !o.synthetic);
  if (real.length && real.length < items.length) {
    return { chip: MIXED, hint: `реальные цены: ${sourceOf(real).chip}; остальные — синтетика` };
  }
  const kinds = [...new Set(items.map(keyOf))];
  if (kinds.length === 1) return PRICE_SOURCES[kinds[0]] ?? OTHER_SOURCE;
  const known = kinds.map((k) => PRICE_SOURCES[k] ?? OTHER_SOURCE);
  return { chip: known.map((x) => x.chip).join(" + "), hint: known.map((x) => x.hint).join("; ") };
};

// Every price carries the "синтетика" label when the data is mock (TRAP-7); a real price carries
// the chip of its source instead of nothing (#547); a sum of both carries MIXED (#567).
export function Price({ value, synthetic = true, source }: { value: number; synthetic?: boolean; source?: PriceSource }) {
  return (
    <span className="price">
      {rub(value)}
      {synthetic && <span className="synth">синтетика</span>}
      {!synthetic && (
        <span className={source?.chip === MIXED ? "synth" : "synth synth-real"} title={(source ?? OTHER_SOURCE).hint}>
          {(source ?? OTHER_SOURCE).chip}
        </span>
      )}
    </span>
  );
}

// Free text from the backend ("дороже на 3 300 ₽", "было 6 400 ₽"): every price in it gets the
// same label as a price (TRAP-7). The text holds prices of the offers, so the label follows them.
// Not marked: a budget (the number the user typed) and a sum that already has the label.
// #567: a label of the backend after the sum («7 900 ₽ (101hotels)», api/answers.py price_mark)
// becomes the chip of that price, so a real price in a mixed message is not marked «синтетика».
const AMOUNT = /\d[\d\s\u00a0]*\s?₽/g;
const SOURCE = "(?:101hotels|Авиасейлс|tutu|реальные данные)";
// The hint of a backend mark of real sources, as at the board chip: «101hotels + tutu».
const hintOf = (mark: string) =>
  mark
    .split(" + ")
    .map((chip) => Object.values(PRICE_SOURCES).find((x) => x.chip === chip)?.hint ?? OTHER_SOURCE.hint)
    .join("; ");
const MARK = new RegExp(`^ \\((синтетика|${MIXED}|${SOURCE}(?: \\+ ${SOURCE})*)\\)`);
const AFTER_BUDGET = /бюджет[а-яё]*(\s+отеля)?\s*$/i;
const LABELLED = /^\s*\(?синтетика/;
// A destination tradeoff of a car with nights on the road starts with it (api/inspire.py, #321).
const ROAD_LINE = "за рулём ";
export function Money({ text, synthetic = true }: { text: string; synthetic?: boolean }) {
  const out: ReactNode[] = [];
  let at = 0;
  for (const m of text.matchAll(AMOUNT)) {
    const end = m.index + m[0].length;
    const mark = MARK.exec(text.slice(end));
    if (mark) {
      const real = mark[1] !== "синтетика" && mark[1] !== MIXED;
      out.push(
        text.slice(at, m.index),
        <span key={m.index} className="price">
          {m[0]}
          <span className={real ? "synth synth-real" : "synth"} title={real ? hintOf(mark[1]) : undefined}>
            {mark[1]}
          </span>
        </span>,
      );
      at = end + mark[0].length;
      continue;
    }
    if (!synthetic || AFTER_BUDGET.test(text.slice(0, m.index)) || LABELLED.test(text.slice(end))) continue;
    out.push(text.slice(at, m.index));
    out.push(
      <span key={m.index} className="price">
        {m[0]}
        <span className="synth">синтетика</span>
      </span>,
    );
    at = end;
  }
  out.push(text.slice(at));
  return <>{out}</>;
}

// plan.changes rows "бюджет" and "бюджет отеля" hold the number of the user.
const isBudget = (what: string) => /^бюджет/i.test(fieldLabel(what));

// The text of these items has no label per price: one synthetic item marks every sum (TRAP-7).
const isSynthetic = (items: Offer[]) => items.length === 0 || items.some((o) => o.synthetic);
// A sum of these items: «синтетика» only when all are synthetic; a mix gets MIXED (sourceOf).
export const allSynthetic = (items: Offer[]) => items.every((o) => o.synthetic);

// The strip of trip parameters above the chat (#434). Origin, dates, guests and budget are the
// editable chips of ParamsBar, then the hotel filters (#431); the other fields of the spec are read-only chips after them.
export function TripParams({ spec, locked, onSend }: {
  spec: TripSpec;
  locked: boolean;
  onSend: (text: string) => void;
}) {
  const rows: [string, string][] = [];
  if (spec.destinations.length) rows.push(["Куда", spec.destinations.join(", ")]);
  if (spec.nights) rows.push(["Ночей", String(spec.nights)]);
  if (spec.pets) rows.push(["Питомец", "да"]);
  if (spec.own_transport) {
    const times = [
      spec.arrive_at && `прилёт ${localTime(spec.arrive_at)}`,
      spec.leave_at && `вылет ${localTime(spec.leave_at)}`,
    ].filter(Boolean);
    rows.push(["Билеты", ["свои", ...times].join(", ")]);
  }
  if (spec.wishes.length) rows.push(["Пожелания", spec.wishes.join(", ")]);
  return (
    <section className="strip" aria-label="Параметры поездки">
      <h2 className="sr-only">Параметры поездки</h2>
      <ParamsBar spec={spec} disabled={locked} onSend={onSend} />
      <HotelFilters spec={spec} disabled={locked} onSend={onSend} />
      {rows.length > 0 && (
        <dl className="spec">
          {rows.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

function Assumptions({ spec }: { spec: TripSpec }) {
  const list = otherAssumptions(spec);
  if (!list.length) return null;
  return (
    <section className="sec assumptions" aria-label="Допущения агента">
      <h2>Допущения агента</h2>
      <ul>
        {list.map((a) => (
          <li key={a}>{a}</li>
        ))}
      </ul>
    </section>
  );
}

// The order of importance as chips. A move sends one chat message: the agent changes only
// the priorities and ranks the options again (S9). After the cart is built the order is fixed.
function PriorityChips({ order, stage, busy, onSend }: {
  order: Priority[];
  stage: Stage;
  busy: boolean;
  onSend: (t: string) => void;
}) {
  const editable = stage === "compare" || stage === "assemble";
  const absent = (Object.keys(PRIORITIES) as Priority[]).filter((p) => !order.includes(p));
  if (!order.length && !editable) return null;
  return (
    <section className="sec" aria-label="Приоритеты">
      <h2>Что для вас важнее</h2>
      {order.length > 0 ? (
        <ol className="chips">
          {order.map((p, i) => (
            <li key={p} className="chip">
              <span>
                {i + 1}. {PRIORITIES[p]}
              </span>
              {editable && (
                <>
                  <button
                    type="button"
                    disabled={busy || i === 0}
                    aria-label={`Поднять приоритет ${PRIORITIES[p]}`}
                    onClick={() => onSend(priorityMessage(movePriority(order, i, -1)!))}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    disabled={busy || i === order.length - 1}
                    aria-label={`Опустить приоритет ${PRIORITIES[p]}`}
                    onClick={() => onSend(priorityMessage(movePriority(order, i, 1)!))}
                  >
                    ↓
                  </button>
                </>
              )}
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">Порядок не задан: агент считает по умолчанию, цена важнее остального.</p>
      )}
      {editable && absent.length > 0 && (
        <p className="chips-add">
          <span className="muted">Добавить:</span>
          {absent.map((p) => (
            <button
              key={p}
              type="button"
              disabled={busy}
              aria-label={`Добавить приоритет ${PRIORITIES[p]}`}
              onClick={() => onSend(priorityMessage([...order, p]))}
            >
              + {PRIORITIES[p]}
            </button>
          ))}
        </p>
      )}
      {editable && order.length > 0 && (
        <p className="muted">
          Первый приоритет важнее всех. Сдвиньте чип: агент пересчитает порядок вариантов
          {stage === "assemble" ? " и вернёт вас к сравнению: план нужно будет собрать заново." : "."}
        </p>
      )}
    </section>
  );
}

// Before a destination is picked its cards are open under the answer, after it — folded in «Варианты».
export const destinationsInChat = (stage: Stage) => stage === "inspire" || stage === "select";

// Tags of a card: only facts of the reply (the cheapest, the fastest, over the user budget).
function destTags(d: Destination, all: Destination[], budget: number | null) {
  const tags: { text: string; kind?: "best" | "warn" }[] = [];
  if (all.length > 1) {
    if (d.est_total_rub === Math.min(...all.map((x) => x.est_total_rub))) tags.push({ text: "лучшая цена", kind: "best" });
    if (d.travel_hours === Math.min(...all.map((x) => x.travel_hours))) tags.push({ text: "быстрее всех" });
  }
  if (budget !== null && d.est_total_rub > budget) tags.push({ text: "дороже бюджета", kind: "warn" });
  if (d.violates.length) tags.push({ text: "нарушает требования", kind: "warn" });
  return tags;
}

// #458: the places of the earlier «ещё» batches (the note «уже показаны» of the spec) that the
// current cards do not show. The note is internal state: it never goes to the text.
export function earlierNames(plan: Plan): string[] {
  const note = plan.spec.assumptions.find((a) => a.startsWith("уже показаны:"));
  const now = plan.destinations.map((d) => d.name);
  return note ? note.split(": ", 2)[1].split(", ").filter((n) => !now.includes(n)) : [];
}

export function Destinations({ plan, stage, busy, onSend, more = true }: {
  plan: Plan;
  stage: Stage;
  busy: boolean;
  onSend: (t: string) => void;
  more?: boolean; // false in an older answer: «ещё» is understood only in the current block
}) {
  const before = more && stage === "inspire" ? earlierNames(plan) : [];
  return (
    <section className="dests" aria-label="Направления">
      <h2>Направления</h2>
      <div className="cards">
        {plan.destinations.map((d: Destination, i) => {
          const word = ordinal(i);
          const tags = destTags(d, plan.destinations, plan.spec.budget_rub);
          // #321: a car with nights on the road: the hours at the wheel (api/inspire.py ROAD_LINE)
          const road = d.tradeoffs.find((t) => t.startsWith(ROAD_LINE));
          const minuses = d.tradeoffs.filter((t) => t !== road);
          return (
            <article key={d.name} className="card dest">
              <h3>
                <span className="dest-no">{i + 1}. </span>
                {d.name} <small>{d.country}</small>
              </h3>
              <p>
                <Money text={d.why} synthetic={d.synthetic} />
              </p>
              <p>
                от <Price value={d.est_total_rub} synthetic={d.synthetic} /> на всех
              </p>
              <p className="muted">
                {road ?? `в пути ${hours(d.travel_hours)}`}
                {d.air_c !== null && `, днём ${d.air_c} °C`}
                {d.water_c !== null && `, вода ${d.water_c} °C`}
                {(d.air_c !== null || d.water_c !== null) && " (оценка)"}
              </p>
              {minuses.length > 0 && (
                <p className="muted">
                  Минусы: <Money text={minuses.join("; ")} synthetic={d.synthetic} />
                </p>
              )}
              {d.violates.length > 0 && (
                <p className="bad">Нарушает требования: {d.violates.join("; ")}</p>
              )}
              {d.risks.length > 0 && <p className="warn">Риски: {d.risks.join("; ")}</p>}
              {tags.length > 0 && (
                <p className="tags">
                  {tags.map((t) => (
                    <span key={t.text} className={`tag${t.kind ? ` ${t.kind}` : ""}`}>
                      {t.text}
                    </span>
                  ))}
                </p>
              )}
              {stage === "inspire" && word && (
                <button
                  type="button"
                  disabled={busy}
                  aria-label={`Выбрать направление ${d.name}`}
                  onClick={() => onSend(d.name)}
                >
                  Выбрать
                </button>
              )}
            </article>
          );
        })}
      </div>
      {before.length > 0 && (
        <p className="earlier-names muted">
          Ранее предлагал:{" "}
          {before.map((n) => (
            <button key={n} type="button" className="chip" disabled={busy} onClick={() => onSend(n)}>
              {n}
            </button>
          ))}
        </p>
      )}
      {/* #451: the next places of the same pool, without the shown ones (api/model.py MORE_RE) */}
      {stage === "inspire" && more && (
        <button type="button" className="more-dests" disabled={busy} onClick={() => onSend("ещё")}>
          Показать ещё
        </button>
      )}
    </section>
  );
}

// Zone of a transport end: smaller text that may wrap apart from the time (TRAP-5, #455).
function zoneTag(iso: string) {
  const zone = zoneLabel(iso);
  return zone ? <span className="zone"> ({zone})</span> : null;
}

// One row per hotel, as in the chat: the selected tariff of the hotel, else its first one.
// The backend orders the tariffs of a hotel best first for the spec (optimizer.hotel_rows).
export function hotelRows(plan: Plan): HotelOffer[] {
  const rows = new Map<string, HotelOffer>();
  for (const o of plan.offers) {
    if (o.kind !== "hotel") continue;
    const key = `${o.title}|${o.city}`;
    const row = rows.get(key);
    if (!row || (plan.selected.includes(o.offer_id) && !plan.selected.includes(row.offer_id))) {
      rows.set(key, o);
    }
  }
  return [...rows.values()];
}

function Hotels({ plan, stage, busy, onSend }: {
  plan: Plan;
  stage: Stage;
  busy: boolean;
  onSend: (t: string) => void;
}) {
  const hotels = hotelRows(plan);
  if (!hotels.length) return null;
  return (
    <section className="sec" aria-label="Сравнение отелей">
      <h2>Отели</h2>
      <div className="scroll">
        <table className="grid-table">
          <thead>
            <tr>
              <th className="num">№</th>
              <th>Отель</th>
              <th className="num">Цена за всё</th>
              <th className="num">Звёзды</th>
              <th className="num">Рейтинг</th>
              <th>Питание</th>
              <th>Отмена</th>
              <th className="num">До центра</th>
              <th>Заезд / выезд</th>
              <th>Питомцы</th>
              <th className="act">
                <span className="sr-only">Действие</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {hotels.map((h, i) => {
              const chosen = plan.selected.includes(h.offer_id);
              const word = ordinal(i);
              return (
                <tr key={h.offer_id} className={chosen ? "chosen" : undefined}>
                  <td className="num no">{i + 1}</td>
                  <td className="name">
                    {h.title}
                    {chosen && <span className="tag">выбран</span>}
                  </td>
                  <td className="num cost">
                    <Price value={h.price_rub} synthetic={h.synthetic} source={sourceOf([h])} />
                  </td>
                  <td className="num">{h.stars !== null ? `${h.stars}★` : "—"}</td>
                  <td className="num" data-label="Рейтинг">{h.rating ?? "—"}</td>
                  <td>{MEALS[h.meal]}</td>
                  <td>
                    {h.cancellation.refundable
                      ? h.cancellation.free_until
                        ? `бесплатно до ${localTime(h.cancellation.free_until)}`
                        : "бесплатная"
                      : "невозвратный"}
                  </td>
                  <td className="num" data-label="До центра">
                    {h.distance_center_km !== null ? `${h.distance_center_km} км` : "—"}
                  </td>
                  <td data-label="Заезд / выезд">
                    {clock(h.check_in)} / {clock(h.check_out)}
                  </td>
                  <td data-label="Питомцы">{h.pets_allowed ? "можно" : "нет отметки"}</td>
                  <td className="act">
                    {stage === "compare" && word && !chosen && (
                      <button
                        type="button"
                        disabled={busy}
                        aria-label={`Выбрать ${h.title}`}
                        onClick={() => onSend(`беру ${word}`)}
                      >
                        Выбрать
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// The backend picks a leg by the row number of this table ("возьми рейс 2", S6).
function Transport({ plan, stage, busy, onSend }: {
  plan: Plan;
  stage: Stage;
  busy: boolean;
  onSend: (t: string) => void;
}) {
  const legs = plan.offers.filter((o): o is TransportOffer => o.kind === "transport");
  if (!legs.length) return null;
  return (
    <section className="sec" aria-label="Сравнение транспорта">
      <h2>{legs.every((t) => t.mode === "flight") ? "Перелёт" : "Транспорт"}</h2>
      <div className="scroll">
        <table className="grid-table">
          <thead>
            <tr>
              <th className="num">№</th>
              <th>Рейс</th>
              <th>Тип</th>
              <th>Отправление</th>
              <th>Прибытие</th>
              <th className="num">В пути</th>
              <th className="num">Пересадки</th>
              <th>Питомцы</th>
              <th className="num">Цена</th>
              <th className="act">
                <span className="sr-only">Действие</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {legs.map((t, i) => {
              const chosen = plan.selected.includes(t.offer_id);
              return (
                <tr key={t.offer_id} className={chosen ? "chosen" : undefined}>
                  <td className="num no">{i + 1}</td>
                  <td className="name">
                    {t.title}
                    {chosen && <span className="tag">выбран</span>}
                  </td>
                  <td>{MODES[t.mode]}</td>
                  <td data-label="Отправление">
                    <span className="when">{localTime(t.depart_at)}</span> <small>{t.origin}</small>{zoneTag(t.depart_at)}
                  </td>
                  <td data-label="Прибытие">
                    <span className="when">{localTime(t.arrive_at)}</span> <small>{t.destination}</small>{zoneTag(t.arrive_at)}
                  </td>
                  <td className="num" data-label="В пути">{duration(t.depart_at, t.arrive_at)}</td>
                  <td className={t.changes ? "num" : "num zero"} data-label="Пересадки">{t.changes || "нет"}</td>
                  {/* #475: the pet mark of a leg, as for the hotels */}
                  <td data-label="Питомцы">{t.pets_allowed ? "можно" : "нет отметки"}</td>
                  <td className="num cost">
                    <Price value={t.price_rub} synthetic={t.synthetic} source={sourceOf([t])} />
                  </td>
                  <td className="act">
                    {(stage === "compare" || stage === "assemble") && !chosen && (
                      <button
                        type="button"
                        disabled={busy}
                        aria-label={`Выбрать рейс ${i + 1}: ${t.title}`}
                        onClick={() => onSend(`возьми рейс ${i + 1}`)}
                      >
                        Выбрать
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted">Время местное для пункта отправления и прибытия.</p>
    </section>
  );
}

function Bundles({ plan }: { plan: Plan }) {
  if (!plan.bundles.length) return null;
  const title = (id: string) => plan.offers.find((o) => o.offer_id === id)?.title;
  const of = (b: Bundle) => plan.offers.filter((o) => b.offer_ids.includes(o.offer_id));
  // The total chip: «синтетика» only when all items are; the text of the minus marks every sum (TRAP-7).
  const synthetic = (b: Bundle) => allSynthetic(of(b));
  const kind = (id: string) => plan.offers.find((o) => o.offer_id === id)?.kind;
  const heading = plan.bundles.every((b) => b.offer_ids.some((id) => kind(id) === "transport"))
    ? "Варианты поездки целиком"
    : "Варианты проживания: дороги в плане нет";
  return (
    <section className="sec" aria-label={heading}>
      <h2>{heading}</h2>
      <div className="cards">
        {plan.bundles.map((b) => (
          <article key={b.bundle_id} className="card">
            <h3>{b.label}</h3>
            <p>
              <Price value={b.total_rub} synthetic={synthetic(b)} source={sourceOf(of(b))} />, в пути {hours(b.travel_hours)}
            </p>
            <ul>
              {b.offer_ids.flatMap((id) => {
                const t = title(id); // an id outside the plan is no text for the guest (#549)
                return t ? [<li key={id}>{t}</li>] : [];
              })}
            </ul>
            {b.tradeoffs.length > 0 && (
              <p className="muted">
                Минусы: <Money text={b.tradeoffs.join("; ")} synthetic={isSynthetic(of(b))} />
              </p>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

function Explanations({ plan }: { plan: Plan }) {
  if (!plan.explanations.length) return null;
  const title = (id: string) => plan.offers.find((o) => o.offer_id === id)?.title ?? "Вариант плана";
  const synth = isSynthetic(plan.offers);
  return (
    <section className="sec" aria-label="Почему этот выбор">
      <h2>Почему так</h2>
      <ul className="why">
        {plan.explanations.map((e) => (
          <li key={e.offer_id}>
            <b>{title(e.offer_id)}</b>: <Money text={e.why} synthetic={synth} />
            {e.tradeoffs.length > 0 && (
              <div className="muted">
                Чем пожертвовали: <Money text={e.tradeoffs.join("; ")} synthetic={synth} />
              </div>
            )}
            {e.risks.length > 0 && (
              <div className="warn">
                Риски: <Money text={e.risks.join("; ")} synthetic={synth} />
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Changes({ plan }: { plan: Plan }) {
  if (!plan.changes.length) return null;
  const synth = isSynthetic(plan.offers);
  return (
    <section className="sec" aria-label="Что изменилось">
      <h2>Что изменилось</h2>
      <ul className="plain">
        {plan.changes.map((c) => (
          <li key={c.what}>
            {fieldLabel(c.what)}: было{" "}
            <s>
              <Money text={changeValue(c.before)} synthetic={synth && !isBudget(c.what)} />
            </s>{" "}
            → стало{" "}
            <b>
              <Money text={changeValue(c.after)} synthetic={synth && !isBudget(c.what)} />
            </b>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Timeline({ plan, cart, marked, canAdd, busy, onCart }: {
  plan: Plan;
  cart: Cart;
  marked: boolean; // the cart is final or open: an item outside it is shown as removed
  canAdd: boolean;
  busy: boolean;
  onCart: BoardProps["onCart"];
}) {
  if (!plan.timeline.length && !plan.issues.length) return null;
  const offer = (id: string) => plan.offers.find((o) => o.offer_id === id);
  // The server keeps a removed item in `plan.selected` so it can come back, and the timeline
  // still has its events. Once the cart exists, an event of an item outside it is «removed».
  const inCart = new Set(cart.items.map((o) => o.offer_id));
  const gone = (id: string | null) => marked && id !== null && !inCart.has(id);
  // A fix for a removed item (an add-on of a removed hotel) is useless: the server refuses it.
  const dead = (id: string) => {
    const o = offer(id);
    return marked && o?.kind === "extra" && o.for_offer_id !== null && gone(o.for_offer_id);
  };
  return (
    <section className="sec" aria-label="Таймлайн">
      <h2>Стыковки</h2>
      <p className="muted">Время местное для места события.</p>
      <ol className="timeline">
        {plan.timeline.map((e, i) => (
          <li key={`${e.at}-${i}`} className={gone(e.offer_id) ? "gone" : undefined} style={{ "--i": i } as CSSProperties}>
            <time>{zonedTime(e.at)}</time> <span className="gone-title">
              <Money text={e.title} synthetic={isSynthetic(plan.offers)} />
            </span>
            {gone(e.offer_id) && <span className="gone-note"> — убрано из корзины</span>}
          </li>
        ))}
      </ol>
      {plan.issues.map((issue, i) => {
        const fixes = issue.fixes.filter((id) => !dead(id));
        if (issue.fixes.length > 0 && fixes.length === 0) return null; // only for a removed item
        return (
        <div key={i} className={`issue ${issue.severity}`} role="note">
          <b>{issue.severity === "error" ? "Ошибка стыковки" : "Внимание"}:</b> <Money text={issue.text} synthetic={isSynthetic(plan.offers)} />
          {fixes.length > 0 && (
            <ul>
              {fixes.map((id) => {
                const o = offer(id);
                if (!o) return null; // a fix outside the plan has no title: no raw id for the guest (#549)
                return (
                  <li key={id}>
                    {o.title}
                    {" — "}
                    <Price value={o.price_rub} synthetic={o.synthetic} source={sourceOf([o])} />
                    {canAdd && (
                      <button type="button" disabled={busy} onClick={() => onCart("add", id)}>
                        В корзину
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        );
      })}
    </section>
  );
}

// The total counts up to a new value (no motion: shows it at once).
function useCountUp(value: number): number {
  const [shown, setShown] = useState(value);
  const from = useRef(value);
  useEffect(() => {
    const start = from.current;
    from.current = value;
    if (!motionOk() || start === value) {
      setShown(value);
      return;
    }
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const k = Math.min(1, (t - t0) / 600);
      setShown(start + (value - start) * (1 - (1 - k) ** 3));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value]);
  return shown;
}

function CartView({ cart, spec, stage, removed, busy, onCart, onCheckout }: {
  cart: Cart;
  spec: TripSpec;
  stage: Stage;
  removed: Offer[];
  busy: boolean;
  onCart: BoardProps["onCart"];
  onCheckout: BoardProps["onCheckout"];
}) {
  const [guestName, setGuestName] = useState("");
  const [guestContact, setGuestContact] = useState("");
  const editable = stage === "checkout";
  const needName = guestContact.trim() !== "" && guestName.trim() === "";
  const shownTotal = useCountUp(cart.total_rub);
  const back = removed.filter((r) => !cart.items.some((o) => o.offer_id === r.offer_id));
  if (!cart.items.length && !back.length) return null;
  const over = spec.budget_rub !== null && cart.total_rub > spec.budget_rub;
  const hotel = cart.items.find((o) => o.kind === "hotel");
  const hotelOver =
    hotel && spec.hotel_budget_rub !== null && hotel.price_rub > spec.hotel_budget_rub
      ? hotel.price_rub - spec.hotel_budget_rub
      : 0;
  const synthetic = allSynthetic(cart.items);
  const mark = synthetic ? "синтетика" : cart.items.some((o) => o.synthetic) ? MIXED : "";
  return (
    <section className="sec" aria-label="Корзина">
      <h2>Корзина</h2>
      <p className="muted">Гости: {guestsText(spec)}</p>
      <ul className="plain">
        {cart.items.map((o) => (
          <li key={o.offer_id} className="row">
            <span>{o.title}</span>
            <Price value={o.price_rub} synthetic={o.synthetic} source={sourceOf([o])} />
            {editable && (
              <button
                type="button"
                disabled={busy}
                aria-label={`Убрать ${o.title}`}
                onClick={() => onCart("remove", o.offer_id)}
              >
                Убрать
              </button>
            )}
          </li>
        ))}
      </ul>
      {editable && back.length > 0 && (
        <ul className="plain removed">
          {back.map((o) => (
            <li key={o.offer_id} className="row">
              <s>{o.title}</s>
              <button
                type="button"
                disabled={busy}
                aria-label={`Вернуть ${o.title}`}
                onClick={() => onCart("add", o.offer_id)}
              >
                Вернуть
              </button>
            </li>
          ))}
        </ul>
      )}
      <p className="total" data-testid="cart-total">
        <span>Итого: </span>
        <b>
          <Price value={shownTotal} synthetic={synthetic} source={sourceOf(cart.items)} />
        </b>
      </p>
      {mark && <span className="sub">{synthetic ? "все цены" : "часть цен"} — синтетика · оплата имитируется</span>}
      {over && (
        <p className="warn" role="note">
          Итог больше бюджета {rub(spec.budget_rub!)} на{" "}
          <Price value={cart.total_rub - spec.budget_rub!} synthetic={synthetic} source={sourceOf(cart.items)} />.
        </p>
      )}
      {hotelOver > 0 && (
        <p className="warn" role="note">
          Отель дороже бюджета отеля {rub(spec.hotel_budget_rub!)} на{" "}
          <Price value={hotelOver} synthetic={hotel!.synthetic} source={sourceOf([hotel!])} />.
        </p>
      )}
      {editable && cart.items.length > 0 && (
        <div className="guest-form">
          <label>
            Имя гостя (необязательно)
            <input
              type="text"
              maxLength={100}
              autoComplete="name"
              aria-describedby="guest-note"
              value={guestName}
              onChange={(e) => setGuestName(e.target.value)}
            />
          </label>
          <label>
            Контакт: телефон или e-mail (необязательно)
            <input
              type="text"
              maxLength={100}
              autoComplete="off"
              aria-describedby="guest-note"
              value={guestContact}
              onChange={(e) => setGuestContact(e.target.value)}
            />
          </label>
          <span className="sub" id="guest-note">
            имитация: данные попадут только в ваучер, в систему бронирования не уходят
          </span>
          {needName && (
            <span className="warn" role="note">
              Укажите имя
            </span>
          )}
        </div>
      )}
      {editable && cart.items.length > 0 && (
        <button
          type="button"
          className="primary"
          disabled={busy || needName}
          onClick={() => {
            const name = guestName.trim();
            onCheckout(name ? { name, contact: guestContact.trim() } : null);
          }}
        >
          Подтвердить бронь на {rub(cart.total_rub)}
          {mark && ` (${mark})`} · оплата имитируется
        </button>
      )}
    </section>
  );
}

const BOOKING_STATUS: Record<Voucher["status"], string> = {
  created: "создана, ждёт подтверждения",
  confirmed: "подтверждена",
  cancelled: "отменена",
};

function VoucherView({ voucher }: { voucher: Voucher }) {
  const s = voucher.spec;
  return (
    <section className="sec voucher" aria-label="Ваучер">
      <h2>Ваучер {voucher.booking_id}</h2>
      {voucher.simulated ? (
        <p className="warn">Бронь и оплата имитированы. Это не настоящий заказ.</p>
      ) : (
        <p>Статус брони: {BOOKING_STATUS[voucher.status]}.</p>
      )}
      <dl className="spec">
        <div>
          <dt>Номер брони</dt>
          <dd>{voucher.booking_id}</dd>
        </div>
        <div>
          <dt>Даты</dt>
          <dd>
            {localDate(s.date_from)} – {localDate(s.date_to)}
          </dd>
        </div>
        <div>
          <dt>Гости</dt>
          <dd>{guestsText(s)}</dd>
        </div>
        <div>
          <dt>Оформлено</dt>
          <dd>{browserTime(voucher.created_at)}</dd>
        </div>
      </dl>
      <p className="guest-line">
        Гость:{" "}
        {voucher.guest
          ? [voucher.guest.name, voucher.guest.contact].filter(Boolean).join(", ")
          : `имя не введено${voucher.simulated ? " (имитация)" : ""}`}
      </p>
      <ul className="plain">
        {voucher.items.map((o) => (
          <li key={o.offer_id} className="row" data-offer-id={o.offer_id}>
            <span>{o.title}</span>
            <Price value={o.price_rub} synthetic={o.synthetic} source={sourceOf([o])} />
          </li>
        ))}
      </ul>
      <p className="total">
        Итого: <Price value={voucher.total_rub} synthetic={allSynthetic(voucher.items)} source={sourceOf(voucher.items)} />
      </p>
    </section>
  );
}

const NEXT: Partial<Record<Stage, { label: string; text: string }>> = {
  compare: { label: "Беру этот план", text: "беру" },
  assemble: { label: "Положить в корзину", text: "да" },
};

// What the compact block «Подробнее» holds, in one line for its summary.
function moreLine(reply: ChatReply, editable: boolean): string {
  const { spec, changes, explanations } = reply.plan;
  const parts: string[] = [];
  if (changes.length) parts.push(`изменено: ${changes.length}`);
  if (spec.priorities.length) parts.push(`важнее: ${spec.priorities.map((p) => PRIORITIES[p]).join(" → ")}`);
  else if (editable) parts.push("что для вас важнее");
  const assumed = otherAssumptions(spec).length;
  if (assumed) parts.push(`допущений: ${assumed}`);
  if (explanations.length) parts.push("почему так");
  return parts.join(" · ");
}

// The choices of an older answer stay under it, folded: the user can scroll up and pick an earlier
// option without retyping (#450). Same cards and buttons as in the block of the stage.
export function EarlierChoices({ reply, current, busy, onSend }: {
  reply: ChatReply;
  current: ChatReply;
  busy: boolean;
  onSend: (t: string) => void;
}) {
  const { plan, stage } = reply;
  const rows = (r: ChatReply) =>
    r.plan.offers.filter((o) => o.kind === "hotel" || o.kind === "transport").map((o) => o.offer_id).join();
  const n = plan.destinations.length + plan.offers.filter((o) => o.kind === "hotel" || o.kind === "transport").length;
  if (!n || stage === "checkout" || stage === "booked") return null;
  // «беру второй» and «третий: Сочи» are parsed against the CURRENT list by the row number: a button
  // is live only while the stage and the rows (the destination names) are the same as in the current plan.
  const rowsLive = stage === current.stage && rows(reply) === rows(current);
  // #458: a destination is sent by its NAME, which the agent finds in the whole pool shown in the
  // session: an old batch stays live while the trip is still at the choice of a place.
  const destLive = stage === "inspire" && (current.stage === "inspire" || current.stage === "select");
  const stale = (!rowsLive && rows(reply) !== "") || (!destLive && plan.destinations.length > 0 && stage === "inspire");
  return (
    <details className="more options earlier" open={destLive && plan.destinations.length > 0 ? true : undefined}>
      <summary>
        <b>Варианты</b>
        <span>выбор из прошлого ответа</span>
      </summary>
      {stale && <p className="muted">План изменился — выберите в текущем блоке.</p>}
      {plan.destinations.length > 0 && (
        <Destinations plan={plan} stage={destLive ? stage : "booked"} busy={busy} onSend={onSend} more={false} />
      )}
      <Hotels plan={plan} stage={rowsLive ? stage : "booked"} busy={busy} onSend={onSend} />
      <Transport plan={plan} stage={rowsLive ? stage : "booked"} busy={busy} onSend={onSend} />
    </details>
  );
}

// The block of the current stage under the last agent message (#434): the cards of the stage
// first, then the compact «Подробнее» (priorities, assumptions, changes, reasons). The options
// the user already chose from fold into «Варианты» after the cart is built. Every section stays
// in the DOM: a folded one is in a closed <details>.
export function Board({ reply, busy, removed, onSend, onCart, onCheckout }: BoardProps) {
  const { plan, stage, cart, voucher } = reply;
  const next = NEXT[stage];
  const past = stage === "checkout" || stage === "booked";
  const destinations = plan.destinations.length > 0 && (
    <Destinations plan={plan} stage={stage} busy={busy} onSend={onSend} />
  );
  const choices = (
    <>
      <Hotels plan={plan} stage={stage} busy={busy} onSend={onSend} />
      <Transport plan={plan} stage={stage} busy={busy} onSend={onSend} />
      <Bundles plan={plan} />
    </>
  );
  // Folded: the destinations after one is picked; after the cart, the hotels and transport too.
  const folded = !destinationsInChat(stage) && (destinations || past);
  const timeline = (
    <Timeline
      plan={plan}
      cart={cart}
      marked={past}
      canAdd={stage === "checkout"}
      busy={busy}
      onCart={onCart}
    />
  );
  const cartView = (
    <CartView
      cart={cart}
      spec={plan.spec}
      stage={stage}
      removed={removed}
      busy={busy}
      onCart={onCart}
      onCheckout={onCheckout}
    />
  );
  const editable = stage === "compare" || stage === "assemble";
  const more = moreLine(reply, editable);
  // The sum of the chosen plan, before the cart exists: the same offers the cart will hold.
  const picked = cart.items.length ? cart.items : plan.offers.filter((o) => plan.selected.includes(o.offer_id));
  // #556: on compare a plan with no selected hotel is a dead end: "беру" is off. The same rule as
  // backend `_hotel_selected` (model.py), #532. The hint is built from the facts of the plan: the
  // pet filtered the hotels out (#550), the hotel source gave none (#532), or none is picked.
  const noHotel = stage === "compare" && !plan.offers.some((o) => o.kind === "hotel" && plan.selected.includes(o.offer_id));
  const hasHotels = plan.offers.some((o) => o.kind === "hotel");
  const noHotelHint = !hasHotels
    ? "Отеля в плане нет — источник отелей не ответил. Повторите запрос, выберите другие даты или направление"
    : plan.spec.pets
      ? "В плане нет отеля — выберите отель, другой город или «Без питомца»"
      : "Выберите отель из списка";
  const nextStep = next && (
    <section className="sec next" aria-label="Следующий шаг">
      <button
        type="button"
        className="buy"
        disabled={busy || noHotel}
        title={noHotel ? noHotelHint : undefined}
        onClick={() => onSend(next.text)}
      >
        {next.label}
        {stage === "assemble" && picked.length > 0 && (
          <>
            {" · "}
            <Price value={picked.reduce((t, o) => t + o.price_rub, 0)} synthetic={allSynthetic(picked)} source={sourceOf(picked)} />
          </>
        )}
      </button>
      {noHotel && <p className="muted no-hotel">{noHotelHint}</p>}
    </section>
  );
  return (
    <div className="board">
      {!reply.llm && (
        <p className="badge" role="note">
          без ИИ — упрощённый режим: модель недоступна, агент работает по правилам
        </p>
      )}
      {destinationsInChat(stage) && destinations}
      {voucher && <VoucherView voucher={voucher} />}
      {past && (
        <>
          {timeline}
          {cartView}
        </>
      )}
      {/* The plan is built: its timeline, the options, the cart; the main action is last (#450). */}
      {stage === "assemble" && (
        <>
          {timeline}
          {choices}
          {cartView}
          {nextStep}
        </>
      )}
      {!past && stage !== "assemble" && (
        <>
          {choices}
          {timeline}
          {cartView}
          {nextStep}
        </>
      )}
      {(more || editable) && (
        <details className="more" open={plan.changes.length > 0 || undefined}>
          <summary>
            <b>Подробнее</b>
            {more && <span>{more}</span>}
          </summary>
          <Changes plan={plan} />
          <PriorityChips order={plan.spec.priorities} stage={stage} busy={busy} onSend={onSend} />
          <Assumptions spec={plan.spec} />
          <Explanations plan={plan} />
        </details>
      )}
      {folded && (
        <details className="more options">
          <summary>
            <b>Варианты</b>
            <span>{past ? "направления, отели и транспорт, из которых выбирали" : "другие направления"}</span>
          </summary>
          {destinations}
          {past && choices}
        </details>
      )}
    </div>
  );
}
