import { Price, hotelRows, sourceOf } from "./Board";
import type { ChatReply, HotelOffer, TransportOffer } from "./contracts";
import { MEALS, duration, ordinal, zonedTime } from "./format";

// #489: an item of the proposed trip opens the other options of its kind from plan.offers.
// A pick is the same chat message as the button of the compare tables (Board.tsx): no new API.
export type Choice = { reply: ChatReply; busy: boolean; onSend: (text: string) => void };

type Option = HotelOffer | TransportOffer;
export type Badge = "cheap" | "fast" | "rating" | "center";

export const BADGES: Record<Badge, string> = {
  cheap: "самый дешёвый",
  fast: "самый быстрый",
  rating: "лучший рейтинг",
  center: "ближе к центру",
};

const minutes = (t: TransportOffer) => (Date.parse(t.arrive_at) - Date.parse(t.depart_at)) / 60000;

// What each badge measures; the sign turns «max» into «min». Only offer facts (TRAP-4).
const RULES: { badge: Badge; kind: Option["kind"] | null; value: (o: Option) => number | null }[] = [
  { badge: "cheap", kind: null, value: (o) => o.price_rub },
  { badge: "fast", kind: "transport", value: (o) => (o.kind === "transport" ? minutes(o) : null) },
  { badge: "rating", kind: "hotel", value: (o) => (o.kind === "hotel" && o.rating !== null ? -o.rating : null) },
  { badge: "center", kind: "hotel", value: (o) => (o.kind === "hotel" ? o.distance_center_km : null) },
];

// The badges of each option: a badge goes to the one option with the best value, only when at
// least two options have a value and no other option shares the best one.
export function badges(options: Option[]): Record<string, Badge[]> {
  const out: Record<string, Badge[]> = Object.fromEntries(options.map((o) => [o.offer_id, []]));
  for (const rule of RULES) {
    const scored = options.flatMap((o) => {
      const v = rule.kind === null || rule.kind === o.kind ? rule.value(o) : null;
      return v !== null && Number.isFinite(v) ? [{ id: o.offer_id, v }] : [];
    });
    if (scored.length < 2) continue;
    const best = Math.min(...scored.map((s) => s.v));
    const top = scored.filter((s) => s.v === best);
    if (top.length === 1) out[top[0].id].push(rule.badge);
  }
  return out;
}

// The options of an item and the chat message that picks each one (null: no pick now).
// «Туда» are the legs to the hotel city, «Обратно» the legs from it (TransportProvider);
// «возьми рейс N» counts the whole transport table (backend model.switch_transport).
export function optionsOf(label: string, reply: ChatReply): { option: Option; pick: string | null }[] {
  const { plan, stage } = reply;
  if (label === "Отель") {
    // the rows of the hotel table: «беру второй» counts them (backend optimizer.hotel_rows)
    return hotelRows(plan).map((h, i) => {
      const word = ordinal(i);
      return { option: h, pick: stage === "compare" && word ? `беру ${word}` : null };
    });
  }
  if (label !== "Туда" && label !== "Обратно") return [];
  const table = plan.offers.filter((o): o is TransportOffer => o.kind === "transport");
  const hotel = plan.offers.find((o) => o.kind === "hotel" && plan.selected.includes(o.offer_id));
  const city = hotel?.kind === "hotel" ? hotel.city : plan.spec.destinations[0];
  const live = stage === "compare" || stage === "assemble";
  return table.flatMap((t, i) =>
    (label === "Туда" ? t.destination === city : t.origin === city)
      ? [{ option: t, pick: live ? `возьми рейс ${i + 1}` : null }]
      : [],
  );
}

function facts(o: Option): string {
  if (o.kind === "transport") {
    const changes = o.changes ? `пересадок: ${o.changes}` : "без пересадок";
    return `${zonedTime(o.depart_at)} → ${zonedTime(o.arrive_at)} · в пути ${duration(o.depart_at, o.arrive_at)} · ${changes}`;
  }
  return [
    o.stars !== null ? `${o.stars}★` : null,
    o.rating !== null ? `рейтинг ${o.rating}` : null,
    o.distance_center_km !== null ? `${o.distance_center_km} км до центра` : null,
    MEALS[o.meal],
  ]
    .filter(Boolean)
    .join(" · ");
}

export function Alternatives({ id, label, choice }: { id: string; label: string; choice: Choice }) {
  const rows = optionsOf(label, choice.reply);
  const marks = badges(rows.map((r) => r.option));
  const { selected } = choice.reply.plan;
  return (
    <ul className="alts" id={id} aria-label={`Другие варианты: ${label}`}>
      {rows.map(({ option: o, pick }) => {
        const chosen = selected.includes(o.offer_id);
        return (
          <li key={o.offer_id} className={chosen ? "alt chosen" : "alt"}>
            <div className="alt-body">
              <div className="alt-title">
                {o.title}
                {marks[o.offer_id].map((b) => (
                  <span key={b} className={`alt-badge ${b}`}>
                    {BADGES[b]}
                  </span>
                ))}
              </div>
              <div className="alt-facts">{facts(o)}</div>
            </div>
            <div className="alt-end">
              <Price value={o.price_rub} synthetic={o.synthetic} source={sourceOf([o])} />
              {chosen ? (
                <span className="alt-mark">выбран</span>
              ) : (
                pick && (
                  <button
                    type="button"
                    className="alt-pick"
                    disabled={choice.busy}
                    aria-label={`Выбрать ${o.title}`}
                    onClick={() => choice.onSend(pick)}
                  >
                    Выбрать
                  </button>
                )
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
