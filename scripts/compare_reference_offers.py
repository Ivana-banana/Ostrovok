#!/usr/bin/env python3
"""Compare the hotels of the agent with the real offers of eval/reference_offers.json (#263).

    cd backend && uv run --frozen python ../scripts/compare_reference_offers.py

The agent runs in the rules mode: no LLM key, no network, no live hotel source (TRAP-1, TRAP-6).
For each request of the reference and each of 3, 4, 5 stars it prints
- the agent night price (the median of its hotel offers of these stars; price_rub is the whole stay)
  against min / median / max night price of the real Booking offers of the same city and stay;
- whether the agent sells free cancellation and breakfast at these stars, against the real shares.
The last line of a request is the hotel the agent selected. Agoda offers (EUR) are in the
reference, but they are not compared: no exchange rate is applied. The script only measures.
Exit code is always 0 when the table is printed.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "eval" / "reference_offers.json"
STARS = (3, 4, 5)

sys.path.insert(0, str(ROOT / "backend" / "src"))

from api import catalog  # noqa: E402
from api.contracts import ChatReply, ChatRequest  # noqa: E402
from api.model import chat  # noqa: E402
from api.store import SessionStore  # noqa: E402


def _offline(url: str, params: dict) -> dict:
    raise OSError("no network")


def rules_mode() -> None:
    """The rules mode must not depend on the environment of the person who runs the script."""
    os.environ.pop("TRAVELPAYOUTS_TOKEN", None)
    os.environ["LIVE_HOTELS"] = "0"
    for name in ("LLM_API_KEY", "GIGACHAT_AUTH_KEY"):
        os.environ.pop(name, None)
    catalog._get = _offline  # lookup_online sees no network, as on a demo offline


def ask_agent(message: str) -> ChatReply:
    """A new session; the origin question, if any, is answered by the chip «Из Москвы»."""
    with tempfile.TemporaryDirectory() as tmp:
        store = SessionStore(Path(tmp) / "sessions.db")
        reply = chat(ChatRequest(session_id=None, message=message), store)
        if reply.stage == "clarify":
            reply = chat(ChatRequest(session_id=reply.session_id, message="Из Москвы"), store)
        return reply


def night(offer, nights: int) -> int:
    return round(offer.price_rub / nights)


def compare_request(req: dict, reply: ChatReply) -> list[dict]:
    """Rows for 3, 4, 5 stars and for the selected hotel; `agent` is None when there is no hotel."""
    hotels = [o for o in reply.plan.offers if o.kind == "hotel"]
    nights = req["nights"]
    rows = []
    for stars in STARS:
        ref = req["market"].get(str(stars))
        group = [h for h in hotels if h.stars == stars]
        rows.append(row_of(req, stars, group, ref, nights, "★"))
    chosen = [o for o in hotels if o.offer_id in reply.plan.selected]
    if chosen:
        stars = chosen[0].stars
        ref = req["market"].get(str(stars))
        rows.append(row_of(req, stars, chosen, ref, nights, "выбран"))
    return rows


def row_of(
    req: dict, stars: int | None, group: list, ref: dict | None, nights: int, kind: str
) -> dict:
    row = {
        "request": req["id"],
        "city": req["city"],
        "kind": kind,
        "stars": stars,
        "agent": None,
        "ref": ref,
    }
    if group:
        value = round(statistics.median(night(h, nights) for h in group))
        row["agent"] = value
        row["ratio"] = round(value / ref["median"], 2) if ref else None
        row["in_range"] = bool(ref) and ref["min"] <= value <= ref["max"]
        row["free_cancel"] = any(h.cancellation.refundable for h in group)
        row["breakfast"] = any(h.meal != "RO" for h in group)
    return row


def render(rows: list[dict]) -> str:
    head = (
        f"{'req':<4}{'город':<10}{'':<8}{'агент ₽/ночь':>13}{'ref min':>9}{'ref med':>9}{'ref max':>9}"
        f"{'агент/med':>10}{'в диапаз.':>10}     отмена агент/ref  завтрак агент/ref"
    )
    lines = [head, "-" * len(head)]
    for r in rows:
        label = f"{r['stars']}★" if r["kind"] == "★" else f"выбран {r['stars']}★"
        ref, agent = r["ref"], r["agent"]
        cells = [f"{r['request']:<4}{r['city']:<10}{label:<8}"]
        if agent is None:
            cells.append(f"{'нет отеля':>13}")
        else:
            cells.append(f"{agent:>13}")
        if ref:
            cells.append(f"{ref['min']:>9}{ref['median']:>9}{ref['max']:>9}")
        else:
            cells.append(f"{'—':>9}{'—':>9}{'—':>9}")
        if agent is not None and ref:
            cells.append(f"{r['ratio']:>10}{'да' if r['in_range'] else 'НЕТ':>10}")
            cells.append(
                f"   {'да' if r['free_cancel'] else 'нет':<4}/ {ref['free_cancel_share']:>4.0%}"
            )
            cells.append(f"{'да' if r['breakfast'] else 'нет':>13}/ {ref['breakfast_share']:>4.0%}")
        else:
            cells.append(f"{'—':>10}{'—':>10}")
        lines.append("".join(cells))
    return "\n".join(lines)


def run(reference: dict) -> list[dict]:
    rows = []
    for req in reference["requests"]:
        rows += compare_request(req, ask_agent(req["message"]))
    return rows


def main() -> None:
    rules_mode()
    reference = json.loads(REFERENCE.read_text("utf-8"))
    rows = run(reference)
    print(render(rows))
    graded = [r for r in rows if r["kind"] == "★" and r["agent"] is not None and r["ref"]]
    if graded:
        ratios = [r["ratio"] for r in graded]
        print(
            f"\nсравнено {len(graded)} пар город/звёзды; в диапазоне min..max: "
            f"{sum(r['in_range'] for r in graded)}; агент/median: "
            f"медиана {statistics.median(ratios):.2f}, от {min(ratios):.2f} до {max(ratios):.2f}"
        )


if __name__ == "__main__":
    main()
