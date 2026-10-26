#!/usr/bin/env python3
"""Scoring for architecture-planner. All arithmetic lives here, never in the model's head.

State: <workdir>/scores.json
  {
    "criteria":  [{"id": "latency", "name": "Latency", "weight": 10,
                   "anchors": {"1": "...", "2": "...", "3": "...", "4": "...", "5": "..."}}],
    "finalists": ["C1", "C3", ...],            # optional; else every cand with entries
    "entries":   [{"cand": "C1" | "A", "criterion": "latency", "lens": "builder",
                   "score": 1..5, "evidence": "measured|researched|walkthrough|assumption",
                   "justification": "anchor 4: p95 1.8 s <= target 2 s", "source": "06-research.md R2"}]
  }
Derived: <workdir>/scores-aggregate.json (aggregate, sensitivity, voi results).
If <workdir>/critique-map.json exists, anonymous labels (A, B, ...) in entries are
mapped back to candidate ids.

Scale drift: the same card scored by two panel runs can differ by 10-20 points. validate and
sensitivity warn when compared candidates were last scored by the panel in different rounds.

Total = sum((median - 1) / 4 * weight)  over criteria with weight > 0  -> 0..100 %.
Per cell, only entries at the strongest evidence level present are used
(a measured score overrides estimates for that cell). Entries with lens "screen"
(preliminary screening scores) are ignored once any other lens has scored the cell.

Commands (all take --workdir DIR; default $ARCH_WORKDIR, then ./_arch):
  score.py init --defaults [--actions yes|no]        default criteria, weights, anchor templates
  score.py init --criteria-file criteria.json         criteria from the brief
  score.py weights --swing quality=100 latency=60 cost=30 ...
        swing weighting: points for each criterion's worst->best swing relative to the
        top one (100); writes integer weights summing to 100 (unlisted criteria -> 0)
  score.py finalists C1 C3 C6 ...                     set the finalist list
  score.py anonymize --lenses reliability,builder,stakeholder [--seed N] [--add H1]
        neutral labels + a shuffled order per lens -> critique-map.json
  score.py anchors --file anchors.json                set anchors {criterion: {"1": "..", .., "5": ".."}}
  score.py add --cand C1 --criterion latency --lens builder --score 4
               --evidence researched --justification "..." [--source "..."] [--round N]
  score.py import entries.json [--round N]            list of entries or {"entries": [...]}
        an entry with the same (cand, criterion, lens) REPLACES the old one; the old one
        is kept in scores.json "history". "round" = which panel run produced the entry.
  score.py cards --cards cards.json [--only H1,P1] [--out-suffix X]
        neutral per-lens cards in each lens's shuffled order -> cards-<lens>.md
  score.py validate                                   exit 1 on errors
  score.py aggregate [--no-progress]                  medians, totals, disagreements, confidence;
        also updates progress.json scores (via progress.py) when it exists
  score.py table [--format md|json]                   tables for 07-critique.md and the report
  score.py sensitivity [--runs 2000] [--seed 42] [--cands H1,P1]
        Monte Carlo over weights and score uncertainty (optionally on a subset)
  score.py voi --plan research-plan.json [--runs 2000] [--seed 42]
        rank open research questions by value of information
  score.py diversity (--md 03-candidates.md | --json matrix.json) [--min-axes 3]
        pairs of candidates that differ on fewer than --min-axes axes

Stdlib only; Linux, macOS, Windows.
"""
import argparse
import json
import os
import random
import re
import statistics
import subprocess
import sys

EVIDENCE = ["assumption", "walkthrough", "researched", "measured"]  # weakest -> strongest
RANK = {e: i for i, e in enumerate(EVIDENCE)}
NOISE = {"assumption": 1.0, "walkthrough": 0.5, "researched": 0.0, "measured": 0.0}
P_YES = {"low": 0.25, "medium": 0.5, "high": 0.75}
TIE_SHARE = 0.60  # leader wins in < 60 % of perturbations -> tie

DEFAULT_CRITERIA = [
    ("quality", "Result quality", 25, {
        "5": "Passes all critical and >= 90 % of all scenarios",
        "4": "Passes all critical and >= 75 % of all scenarios",
        "3": "Passes all critical and >= 60 % of all scenarios",
        "2": "Fails 1 critical scenario or passes < 60 %",
        "1": "Fails 2+ critical scenarios"}),
    ("reliability", "Reliability", 15, {
        "5": "Every LLM/tool error is caught and handled; no step can compound errors unchecked",
        "4": "Errors handled on the main path; at most one unguarded multi-step chain",
        "3": "Retries exist but some failures surface to the user as wrong output",
        "2": "Several unguarded multi-step chains; loops or drift possible",
        "1": "A single bad LLM output can silently corrupt the result or loop forever"}),
    ("build_speed", "Speed of implementation", 15, {
        "5": "This team can build an end-to-end version in <= 25 % of the time available",
        "4": "<= 50 % of the time available",
        "3": "<= 75 % of the time available",
        "2": "<= 100 % of the time available, no slack",
        "1": "Needs more time or skills than the team has"}),
    ("cost", "Cost", 10, {
        "5": "<= 25 % of the budget at expected volume (<budget>)",
        "4": "<= 50 % of the budget",
        "3": "<= 75 % of the budget",
        "2": "<= 100 % of the budget",
        "1": "Over budget"}),
    ("latency", "Latency", 10, {
        "5": "p95 <= target/2 (target: <target>)",
        "4": "p95 <= target",
        "3": "p95 <= 1.5 x target",
        "2": "p95 <= 2 x target",
        "1": "p95 > 2 x target"}),
    ("debuggability", "Debuggability and observability", 10, {
        "5": "Every step's input/output is logged and replayable; a failure is located in minutes",
        "4": "All LLM and tool calls logged; failures located with some reading",
        "3": "Partial logging; some failures need re-running to understand",
        "2": "Hidden state or free-form agent loops make failures hard to reproduce",
        "1": "No way to tell why the system did what it did"}),
    ("evaluability", "Evaluability", 5, {
        "5": "Scenarios run automatically end to end with checkable pass criteria",
        "4": "Automatic run, some criteria need an LLM judge",
        "3": "Semi-manual eval possible",
        "2": "Only manual spot checks",
        "1": "Output not comparable across runs"}),
    ("extensibility", "Extensibility", 5, {
        "5": "New capability = new module/tool, no change to existing parts",
        "4": "New capability touches one existing component",
        "3": "Touches several components",
        "2": "Requires restructuring the control flow",
        "1": "Effectively a rewrite"}),
    ("demo", "Demo impact", 5, {
        "5": "Shows a visible, working, original result on the judges' criteria in < 2 minutes",
        "4": "Working demo, clear on most judging criteria",
        "3": "Working but unremarkable demo",
        "2": "Demo needs explanation or mock-ups",
        "1": "Nothing convincing to show"}),
]
SAFETY = ("safety", "Safety / blast radius", {
    "5": "Read-only, or every external action is reversible and confirmed",
    "4": "Irreversible actions require confirmation; tools have least privilege",
    "3": "Reversible actions without confirmation; irreversible ones confirmed",
    "2": "Some irreversible actions without confirmation, but no untrusted input reaches them",
    "1": "Irreversible external actions without confirmation while untrusted input is present"})


# ------------------------------------------------------------------ io

def wd(a):
    return os.path.abspath(a.workdir or os.environ.get("ARCH_WORKDIR") or "_arch")


def jload(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def jsave(p, obj):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def load_scores(path):
    s = jload(os.path.join(path, "scores.json"))
    if s is None:
        sys.exit(f"No scores.json in {path}. Run `score.py init` first.")
    s.setdefault("criteria", [])
    s.setdefault("entries", [])
    return s


def save_scores(path, s):
    jsave(os.path.join(path, "scores.json"), s)


def load_map(path):
    return jload(os.path.join(path, "critique-map.json"), {}) or {}


def resolve(cand, cmap):
    return (cmap.get("labels") or {}).get(cand, cand)


def normalise_weights(items, target=100):
    """Scale weights to integers that sum to `target` (largest remainder)."""
    total = sum(w for _, w in items)
    raw = [(k, w * target / total) for k, w in items]
    base = {k: int(v) for k, v in raw}
    rest = target - sum(base.values())
    for k, v in sorted(raw, key=lambda kv: kv[1] - int(kv[1]), reverse=True)[:rest]:
        base[k] += 1
    return base


# ------------------------------------------------------------------ core maths

def total_of(cells, weights):
    """cells: {criterion: score 1..5}; weights: {criterion: w}, sum 100 or any positive sum."""
    wsum = sum(w for w in weights.values() if w > 0)
    if wsum <= 0:
        return 0.0
    return sum((cells[c] - 1) / 4 * w for c, w in weights.items() if w > 0) * 100 / wsum


def cell_table(s, cmap=None):
    """Aggregate entries into {cand: {criterion: {"score", "evidence", "lens_scores", "entries"}}}."""
    cmap = cmap or {}
    grouped = {}
    for e in s["entries"]:
        if check_entry(e, set()):  # malformed entries are reported by validate, never counted
            continue
        cand = resolve(e["cand"], cmap)
        grouped.setdefault(cand, {}).setdefault(e["criterion"], []).append(e)
    cells = {}
    for cand, crits in grouped.items():
        for crit, es in crits.items():
            # preliminary screening scores only stand in until the panel has scored the cell
            if any(e.get("lens") != "screen" for e in es):
                es = [e for e in es if e.get("lens") != "screen"]
            best = max(RANK.get(e.get("evidence"), -1) for e in es)
            top = [e for e in es if RANK.get(e.get("evidence"), -1) == best]
            cells.setdefault(cand, {})[crit] = {
                "score": statistics.median(float(e["score"]) for e in top),
                "evidence": EVIDENCE[best] if best >= 0 else "assumption",
                "lens_scores": {e.get("lens", "?"): e["score"] for e in es},
                "spread": max(e["score"] for e in es) - min(e["score"] for e in es),
            }
    return cells


def weights_of(s):
    return {c["id"]: float(c.get("weight", 0)) for c in s["criteria"]}


def finalists_of(s, cells):
    return list(s.get("finalists") or sorted(cells))


def complete(cand, cells, weights):
    return all(c in cells.get(cand, {}) for c, w in weights.items() if w > 0)


def totals(s, cells, cands=None):
    w = weights_of(s)
    res = {}
    for cand in cands or finalists_of(s, cells):
        if complete(cand, cells, w):
            res[cand] = total_of({c: v["score"] for c, v in cells[cand].items()}, w)
        else:
            res[cand] = None
    return res


def leader_of(tot):
    valid = {k: v for k, v in tot.items() if v is not None}
    if not valid:
        return None
    return sorted(valid.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def simulate(s, cells, cands, runs=2000, seed=42, vary_weights=True, vary_scores=True):
    """Monte Carlo. Returns win share per candidate. Common random numbers: the
    same seed gives identical draws, so results are comparable across what-ifs."""
    rng = random.Random(seed)
    w = {c: v for c, v in weights_of(s).items() if v > 0}
    crits = sorted(w)
    wins = {c: 0.0 for c in cands}
    for _ in range(runs):
        ww = {c: w[c] * (rng.uniform(0.7, 1.3) if vary_weights else 1.0) for c in crits}
        best, best_v = [], None
        for cand in cands:
            sc = {}
            for c in crits:
                cell = cells[cand][c]
                n = NOISE[cell["evidence"]] if vary_scores else 0.0
                d = rng.uniform(-n, n) if n else 0.0
                sc[c] = min(5.0, max(1.0, cell["score"] + d))
            v = total_of(sc, ww)
            if best_v is None or v > best_v + 1e-9:
                best, best_v = [cand], v
            elif abs(v - best_v) <= 1e-9:
                best.append(cand)
        for b in best:
            wins[b] += 1.0 / len(best)
    return {c: round(wins[c] / runs, 4) for c in cands}


def critical_weights(s, cells, cands, leader):
    """For each criterion: the smallest multiplicative change of its weight
    (others fixed, then renormalised) that makes another candidate lead."""
    w = {c: v for c, v in weights_of(s).items() if v > 0}
    res = []
    factors_up = [1 + i * 0.05 for i in range(1, 101)]          # x1.05 .. x6
    factors_down = [1 - i * 0.05 for i in range(1, 20)] + [0.0]  # x0.95 .. x0
    for c in sorted(w):
        for direction, factors in (("up", factors_up), ("down", factors_down)):
            for f in factors:
                ww = dict(w)
                ww[c] = w[c] * f
                tot = {k: total_of({cc: cells[k][cc]["score"] for cc in w}, ww) for k in cands}
                new = leader_of(tot)
                if new != leader:
                    res.append({"criterion": c, "direction": direction, "factor": round(f, 2),
                                "weight_from": w[c], "weight_to": round(w[c] * f, 1), "new_leader": new})
                    break
    res.sort(key=lambda r: abs(1 - r["factor"]))
    return res


# ------------------------------------------------------------------ commands

def cmd_init(a, path):
    if a.criteria_file:
        crit = jload(a.criteria_file)
        if isinstance(crit, dict):
            crit = crit.get("criteria", [])
    elif a.defaults:
        # Safety: 10 % when the system acts on the outside world, else 5 %;
        # the other defaults are rescaled to fill the rest.
        safety_w = 10 if a.actions == "yes" else 5
        ws = normalise_weights([(cid, w) for cid, _, w, _ in DEFAULT_CRITERIA], 100 - safety_w)
        ws["safety"] = safety_w
        crit = [{"id": cid, "name": name, "weight": ws[cid], "anchors": anchors}
                for cid, name, _, anchors in DEFAULT_CRITERIA]
        crit.append({"id": SAFETY[0], "name": SAFETY[1], "weight": ws["safety"], "anchors": SAFETY[2]})
    else:
        sys.exit("Give --defaults or --criteria-file.")
    s = jload(os.path.join(path, "scores.json"), {"entries": []})
    s["criteria"] = crit
    save_scores(path, s)
    out({"ok": True, "criteria": {c["id"]: c["weight"] for c in crit},
         "weight_sum": sum(c["weight"] for c in crit)})


def cmd_weights(a, path):
    s = load_scores(path)
    pts = {}
    for item in a.swing:
        k, _, v = item.partition("=")
        try:
            pts[k.strip()] = float(v)
        except ValueError:
            sys.exit(f"--swing expects criterion=points, got {item!r}")
    known = {c["id"] for c in s["criteria"]}
    unknown = sorted(set(pts) - known)
    if unknown:
        sys.exit(f"unknown criteria: {', '.join(unknown)}; known: {', '.join(sorted(known))}")
    if not pts or max(pts.values()) <= 0:
        sys.exit("give at least one positive swing")
    ws = normalise_weights([(k, v) for k, v in pts.items() if v > 0])
    for c in s["criteria"]:
        c["weight"] = ws.get(c["id"], 0)
    save_scores(path, s)
    out({"ok": True, "weights": {c["id"]: c["weight"] for c in s["criteria"]}})


def cmd_finalists(a, path):
    s = load_scores(path)
    s["finalists"] = a.ids
    save_scores(path, s)
    out({"ok": True, "finalists": a.ids})


def cmd_anonymize(a, path):
    s = load_scores(path)
    cmap = load_map(path)
    labels = dict(cmap.get("labels") or {})
    letters = [chr(ord("A") + i) for i in range(26)]
    seed = a.seed if a.seed is not None else (cmap.get("seed") or random.SystemRandom().randrange(10 ** 6))
    rng = random.Random(seed)
    ids = a.add or s.get("finalists") or []
    if not ids:
        sys.exit("Set finalists first (score.py finalists ...) or pass --add.")
    pool = [l for l in letters if l not in labels]
    rng.shuffle(pool)  # label assignment itself is random, so A is not always the top candidate
    for cid in ids:
        if cid not in labels.values():
            labels[pool.pop(0)] = cid
    lenses = a.lenses.split(",") if a.lenses else (cmap.get("lenses") or ["panel"])
    orders = {}
    for lens in lenses:
        order = sorted(labels)
        random.Random(f"{seed}:{lens}:{len(labels)}").shuffle(order)
        orders[lens] = order
    cmap = {"seed": seed, "labels": dict(sorted(labels.items())), "lenses": lenses, "orders": orders,
            "note": "Give critics only the labels and the per-lens order. Reveal ids after aggregate."}
    jsave(os.path.join(path, "critique-map.json"), cmap)
    out({"ok": True, "seed": seed, "orders": orders, "labels_hidden_from_critics": True,
         "map_file": os.path.join(path, "critique-map.json")})


def norm_entry(e):
    e = dict(e)
    try:
        sc = float(e["score"])
        e["score"] = int(sc) if sc.is_integer() else sc
    except (KeyError, TypeError, ValueError):
        pass
    return e


def upsert(s, entries):
    key = lambda e: (e.get("cand"), e.get("criterion"), e.get("lens"))
    idx = {key(e): i for i, e in enumerate(s["entries"])}
    added = replaced = 0
    for e in entries:
        e = norm_entry(e)
        if key(e) in idx:
            s.setdefault("history", []).append(s["entries"][idx[key(e)]])
            s["entries"][idx[key(e)]] = e
            replaced += 1
        else:
            idx[key(e)] = len(s["entries"])
            s["entries"].append(e)
            added += 1
    return added, replaced


def cmd_add(a, path):
    s = load_scores(path)
    e = {"cand": a.cand, "criterion": a.criterion, "lens": a.lens, "score": a.score,
         "evidence": a.evidence, "justification": a.justification, "source": a.source or ""}
    if a.round is not None:
        e["round"] = a.round
    errs = check_entry(e, {c["id"] for c in s["criteria"]})
    if errs:
        out({"ok": False, "errors": errs})
        sys.exit(1)
    added, replaced = upsert(s, [e])
    save_scores(path, s)
    out({"ok": True, "added": added, "replaced": replaced})


def cmd_import(a, path):
    s = load_scores(path)
    data = jload(a.file)
    entries = data.get("entries", []) if isinstance(data, dict) else data
    if a.round is not None:
        for e in entries:
            e.setdefault("round", a.round)
    crit_ids = {c["id"] for c in s["criteria"]}
    errs = [f"entry {i}: {m}" for i, e in enumerate(entries) for m in check_entry(e, crit_ids)]
    if errs:
        out({"ok": False, "errors": errs})
        sys.exit(1)
    added, replaced = upsert(s, entries)
    save_scores(path, s)
    out({"ok": True, "added": added, "replaced": replaced})


def check_entry(e, crit_ids):
    errs = []
    for k in ("cand", "criterion", "lens", "score", "evidence", "justification"):
        if e.get(k) in (None, ""):
            errs.append(f"missing {k}")
    sc = e.get("score")
    try:
        if not 1 <= float(sc) <= 5:
            errs.append(f"score {sc} outside 1..5")
    except (TypeError, ValueError):
        if sc not in (None, ""):
            errs.append(f"score {sc!r} is not a number")
    if e.get("evidence") not in RANK and e.get("evidence") not in (None, ""):
        errs.append(f"evidence {e.get('evidence')!r} not in {EVIDENCE}")
    if crit_ids and e.get("criterion") not in crit_ids and e.get("criterion"):
        errs.append(f"unknown criterion {e.get('criterion')!r}")
    return errs


PANEL_EXCLUDED = ("screen", "prototype")


def panel_rounds(s, cmap=None, cands=None):
    """{cand: latest panel round} over panel lenses (not screen/prototype). No round -> 1."""
    cmap = cmap or {}
    res = {}
    for e in s.get("entries", []):
        if e.get("lens") in PANEL_EXCLUDED or check_entry(e, set()):
            continue
        cand = resolve(e["cand"], cmap)
        if cands and cand not in cands:
            continue
        try:
            r = int(e.get("round", 1))
        except (TypeError, ValueError):
            r = 1
        res[cand] = max(res.get(cand, 0), r)
    return res


def drift_warning(s, cmap=None, cands=None):
    rounds = panel_rounds(s, cmap, cands)
    if len(set(rounds.values())) > 1:
        return ("scale drift risk: compared candidates were last scored by the panel in different rounds "
                + json.dumps(dict(sorted(rounds.items())), ensure_ascii=False)
                + ". Re-score all of them in one panel round before comparing (hybrid.md, step 4).")
    return None


def validate(s, cmap=None):
    errors, warnings = [], []
    crit_ids = {c["id"] for c in s["criteria"]}
    if not s["criteria"]:
        errors.append("no criteria defined")
    wsum = sum(float(c.get("weight", 0)) for c in s["criteria"])
    if s["criteria"] and abs(wsum - 100) > 0.01:
        errors.append(f"weights sum to {wsum:g}, not 100")
    for c in s["criteria"]:
        if float(c.get("weight", 0)) < 0:
            errors.append(f"criterion {c['id']}: negative weight")
        if float(c.get("weight", 0)) > 0:
            anchors = c.get("anchors") or {}
            missing = [lvl for lvl in "12345" if not str(anchors.get(lvl, "")).strip()]
            if missing:
                errors.append(f"criterion {c['id']}: anchors missing for levels {','.join(missing)}")
            elif any(re.search(r"<[a-z_ ]+>", str(v)) for v in anchors.values()):
                warnings.append(f"criterion {c['id']}: anchors still contain placeholders like <target>; "
                                "fill them from the brief")
    for i, e in enumerate(s["entries"]):
        for m in check_entry(e, crit_ids):
            errors.append(f"entry {i} ({e.get('cand')}/{e.get('criterion')}/{e.get('lens')}): {m}")
    cells = cell_table(s, cmap)
    w = weights_of(s)
    dw = drift_warning(s, cmap, set(finalists_of(s, cells)))
    if dw:
        warnings.append(dw)
    for cand in finalists_of(s, cells):
        for c, wt in w.items():
            if wt > 0 and c not in cells.get(cand, {}):
                errors.append(f"hole: finalist {cand} has no score for {c} (weight {wt:g})")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def cmd_anchors(a, path):
    s = load_scores(path)
    data = jload(a.file)
    if not isinstance(data, dict):
        sys.exit("anchors file must be {criterion: {\"1\": .., \"5\": ..}}")
    known = {c["id"]: c for c in s["criteria"]}
    unknown = sorted(k for k in data if k not in known)
    if unknown:
        out({"ok": False, "errors": [f"unknown criterion {k!r}" for k in unknown]})
        sys.exit(1)
    for cid, anchors in data.items():
        known[cid]["anchors"] = {str(k): v for k, v in anchors.items()}
    save_scores(path, s)
    out({"ok": True, "updated": sorted(data)})


CARD_FIELDS = {
    "reliability": ["components", "axes", "bet", "risk", "actions"],
    "builder": ["components", "axes", "risk", "actions"],
    "stakeholder": ["summary", "axes", "bet", "risk", "actions"],
}
CARD_TITLES = {"components": "Components and data flow", "summary": "Summary", "axes": "Axis values",
               "bet": "What must be true", "risk": "Main risk", "actions": "Actions & permissions"}


def cmd_cards(a, path):
    """Neutral per-lens cards: same text for every candidate, neutral labels, the lens's shuffled order.
    cards.json: {"C1": {"components": .., "summary": .., "axes": .., "bet": .., "risk": .., "actions": ..},
                 "_titles": {field: title in the user's language}, "_fields": {lens: [fields]},
                 "_header": "intro line"}"""
    cmap = load_map(path)
    if not cmap.get("labels"):
        sys.exit("Run score.py anonymize first.")
    cards = jload(a.cards)
    titles = dict(CARD_TITLES, **cards.get("_titles", {}))
    fields = dict(CARD_FIELDS, **cards.get("_fields", {}))
    header = cards.get("_header", "Options carry neutral labels. The order is random.")
    only = set(a.only.split(",")) if a.only else None
    written, missing = [], []
    for lens in cmap.get("lenses") or ["panel"]:
        lines = [f"# Cards — lens {lens}", "", header, ""]
        for lab in cmap["orders"].get(lens, sorted(cmap["labels"])):
            cid = cmap["labels"][lab]
            if only and cid not in only:
                continue
            if cid not in cards:
                missing.append(cid)
                continue
            lines += [f"## Option {lab}", ""]
            for f in fields.get(lens, fields.get("reliability")):
                if cards[cid].get(f):
                    lines += [f"**{titles.get(f, f)}:** {cards[cid][f]}", ""]
        suffix = a.out_suffix or ("-" + "-".join(sorted(only)) if only else "")
        name = f"cards-{lens}{suffix}.md"
        with open(os.path.join(path, name), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        written.append(name)
    out({"ok": not missing, "written": written, "missing_cards": sorted(set(missing))})


def cmd_validate(a, path):
    r = validate(load_scores(path), load_map(path))
    out(r)
    if not r["ok"]:
        sys.exit(1)


def confidence(cand, cells, weights):
    wsum = sum(w for w in weights.values() if w > 0)
    strong = sum(w for c, w in weights.items()
                 if w > 0 and RANK[cells[cand][c]["evidence"]] >= RANK["researched"])
    share = strong / wsum if wsum else 0
    level = "high" if share >= 0.6 else "medium" if share >= 0.3 else "low"
    return level, round(share, 3)


def aggregate(s, cmap):
    cells = cell_table(s, cmap)
    w = weights_of(s)
    cands = finalists_of(s, cells)
    tot = totals(s, cells, cands)
    res = {"formula": "sum((median-1)/4*weight)", "candidates": {}, "disagreements": []}
    for cand in cands:
        info = {"total": None if tot[cand] is None else round(tot[cand], 1), "complete": tot[cand] is not None}
        if tot[cand] is not None:
            info["confidence"], info["strong_evidence_share"] = confidence(cand, cells, w)
        info["cells"] = cells.get(cand, {})
        res["candidates"][cand] = info
        for crit, cell in cells.get(cand, {}).items():
            if cell["spread"] >= 2:
                res["disagreements"].append({"cand": cand, "criterion": crit,
                                             "lens_scores": cell["lens_scores"], "spread": cell["spread"]})
    res["leader"] = leader_of(tot)
    res["ranking"] = [k for k, v in sorted(((k, v) for k, v in tot.items() if v is not None),
                                           key=lambda kv: (-kv[1], kv[0]))]
    return res


def sync_progress(path, agg):
    """Push totals to progress.json through progress.py so dashboard and report agree."""
    if not os.path.exists(os.path.join(path, "progress.json")):
        return "no progress.json"
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "progress.py")
    done = []
    for cand, info in agg["candidates"].items():
        if info["total"] is None:
            continue
        r = subprocess.run([sys.executable, script, "--workdir", path, "cand", "set", cand,
                            "--score", f"{info['total']:.1f}"], capture_output=True,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0:
            done.append(cand)
    return f"updated {len(done)} candidates"


def cmd_aggregate(a, path):
    s = load_scores(path)
    cmap = load_map(path)
    v = validate(s, cmap)
    agg = aggregate(s, cmap)
    agg["validation"] = v
    store = jload(os.path.join(path, "scores-aggregate.json"), {}) or {}
    store["aggregate"] = agg
    jsave(os.path.join(path, "scores-aggregate.json"), store)
    if not a.no_progress:
        agg["progress"] = sync_progress(path, agg)
    brief = {k: {kk: vv for kk, vv in info.items() if kk != "cells"} for k, info in agg["candidates"].items()}
    out({"ok": v["ok"], "leader": agg["leader"], "ranking": agg["ranking"], "candidates": brief,
         "disagreements": agg["disagreements"], "errors": v["errors"], "warnings": v["warnings"],
         "progress": agg.get("progress")})


def sensitivity(s, cmap, runs, seed, subset=None):
    cells = cell_table(s, cmap)
    pool = subset or finalists_of(s, cells)
    cands = [c for c in pool if complete(c, cells, weights_of(s))]
    if not cands:
        sys.exit("No complete candidates to analyse. Run validate.")
    tot = totals(s, cells, cands)
    leader = leader_of(tot)
    combined = simulate(s, cells, cands, runs, seed, True, True)
    weights_only = simulate(s, cells, cands, runs, seed, True, False)
    scores_only = simulate(s, cells, cands, runs, seed, False, True)
    share = combined[leader]
    return {
        "runs": runs, "seed": seed, "leader": leader,
        "win_share": dict(sorted(combined.items(), key=lambda kv: -kv[1])),
        "win_share_weights_only": weights_only, "win_share_scores_only": scores_only,
        "leader_win_share": share,
        "verdict": "robust" if share >= 0.8 else "likely" if share >= TIE_SHARE else "tie",
        "tie_rule": f"tie if the leader wins < {int(TIE_SHARE * 100)} % of perturbations",
        "critical_weights": critical_weights(s, cells, cands, leader),
        "drift_warning": drift_warning(s, cmap, set(cands)),
    }


def cmd_sensitivity(a, path):
    s = load_scores(path)
    subset = [c.strip() for c in a.cands.split(",")] if a.cands else None
    res = sensitivity(s, load_map(path), a.runs, a.seed, subset)
    store = jload(os.path.join(path, "scores-aggregate.json"), {}) or {}
    store["sensitivity" if not subset else "sensitivity_" + "_vs_".join(subset)] = res
    jsave(os.path.join(path, "scores-aggregate.json"), store)
    out(res)


def voi(s, cmap, plan, runs, seed):
    cells = cell_table(s, cmap)
    w = weights_of(s)
    cands = [c for c in finalists_of(s, cells) if complete(c, cells, w)]
    base_tot = totals(s, cells, cands)
    leader = leader_of(base_tot)
    base = simulate(s, cells, cands, runs, seed)
    base_lose = 1 - base[leader]
    qs = plan.get("questions", plan) if isinstance(plan, dict) else plan
    results = []
    for q in qs:
        if q.get("status") in ("answered", "not_found"):
            continue
        p = q.get("p_yes", "medium")
        p = P_YES.get(p, p) if isinstance(p, str) else float(p)
        outcomes = {}
        for side, prob in (("yes", p), ("no", 1 - p)):
            alt = {k: {c: dict(v) for c, v in cc.items()} for k, cc in cells.items()}
            for af in q.get("affects", []):
                cand = resolve(af["cand"], cmap)
                if cand in alt and af["criterion"] in alt[cand]:
                    alt[cand][af["criterion"]]["score"] = float(af[f"if_{side}"])
                    alt[cand][af["criterion"]]["evidence"] = "researched"
            t = {k: total_of({c: alt[k][c]["score"] for c in w if w[c] > 0}, w) for k in cands}
            sim = simulate(s, alt, cands, runs, seed)
            outcomes[side] = {"p": round(prob, 3), "leader": leader_of(t),
                              "leader_win_share": sim[leader]}
        # probability the answer changes the leader, beyond the baseline uncertainty
        p_change = sum(o["p"] * max(0.0, (1 - o["leader_win_share"]) - base_lose) for o in outcomes.values())
        exp_shift = sum(o["p"] * abs(o["leader_win_share"] - base[leader]) for o in outcomes.values())
        flips = [side for side, o in outcomes.items() if o["leader"] != leader]
        results.append({"id": q.get("id"), "text": q.get("text", ""), "voi": round(p_change, 4),
                        "expected_leader_share_change": round(exp_shift, 4),
                        "flips_leader_if": flips, "outcomes": outcomes})
    results.sort(key=lambda r: (-r["voi"], -r["expected_leader_share_change"], str(r["id"])))
    stop = all(r["voi"] < 0.05 for r in results)
    return {"leader": leader, "leader_win_share": base[leader], "questions": results,
            "stop_research": stop,
            "stop_rule": "stop when every remaining question has voi < 0.05 or the question limit is reached"}


def cmd_voi(a, path):
    s = load_scores(path)
    plan = jload(a.plan)
    if plan is None:
        sys.exit(f"No research plan at {a.plan}")
    res = voi(s, load_map(path), plan, a.runs, a.seed)
    store = jload(os.path.join(path, "scores-aggregate.json"), {}) or {}
    store["voi"] = res
    jsave(os.path.join(path, "scores-aggregate.json"), store)
    out(res)


def names(path, cmap):
    prog = jload(os.path.join(path, "progress.json"), {}) or {}
    return {cid: c.get("name", cid) for cid, c in (prog.get("candidates") or {}).items()}


def tables_md(path, s, cmap):
    store = jload(os.path.join(path, "scores-aggregate.json"), {}) or {}
    agg = aggregate(s, cmap)
    sens = store.get("sensitivity") or {}
    nm = names(path, cmap)
    tag = {"measured": "M", "researched": "R", "walkthrough": "W", "assumption": "A"}
    crit = [c for c in s["criteria"] if float(c.get("weight", 0)) > 0]
    order = agg["ranking"] + [c for c in agg["candidates"] if c not in agg["ranking"]]
    lines = ["### Totals", "", "| # | Candidate | Total, % | Win share | Confidence |", "|---|---|---|---|---|"]
    for i, cand in enumerate(order, 1):
        info = agg["candidates"][cand]
        ws = sens.get("win_share", {}).get(cand)
        total_s = "—" if info["total"] is None else f"{info['total']:.1f}"
        ws_s = "—" if ws is None else f"{ws * 100:.0f} %"
        lines.append(f"| {i} | {cand} {nm.get(cand, '')} | {total_s} | {ws_s} | {info.get('confidence', '—')} |")
    if sens:
        lines += ["", f"Leader {sens['leader']} wins {sens['leader_win_share'] * 100:.0f} % of "
                      f"{sens['runs']} perturbations (seed {sens['seed']}): **{sens['verdict']}**."]
    lines += ["", "### Scores by criterion (median; M measured, R researched, W walkthrough, A assumption)", "",
              "| Criterion | Weight | " + " | ".join(order) + " |",
              "|---|---|" + "---|" * len(order)]
    for c in crit:
        row = [f"{c.get('name', c['id'])}", f"{float(c['weight']):g}"]
        for cand in order:
            cell = agg["candidates"][cand]["cells"].get(c["id"])
            row.append("—" if not cell else f"{cell['score']:g} ({tag[cell['evidence']]})")
        lines.append("| " + " | ".join(row) + " |")
    if agg["disagreements"]:
        lines += ["", "### Critic disagreements (spread >= 2)", "",
                  "| Candidate | Criterion | Scores by lens | Spread |", "|---|---|---|---|"]
        for d in agg["disagreements"]:
            ls = ", ".join(f"{k}: {v}" for k, v in d["lens_scores"].items())
            lines.append(f"| {d['cand']} | {d['criterion']} | {ls} | {d['spread']:g} |")
    if sens.get("critical_weights"):
        lines += ["", "### Critical weights", "", "| Criterion | Change | New leader |", "|---|---|---|"]
        for r in sens["critical_weights"][:6]:
            lines.append(f"| {r['criterion']} | weight {r['weight_from']:g} → {r['weight_to']:g} "
                         f"(x{r['factor']}) | {r['new_leader']} |")
    return "\n".join(lines) + "\n"


def cmd_table(a, path):
    s = load_scores(path)
    cmap = load_map(path)
    if a.format == "json":
        out(aggregate(s, cmap))
    else:
        sys.stdout.write(tables_md(path, s, cmap))


# ------------------------------------------------------------------ diversity

def parse_matrix_md(text):
    """Find the first markdown table whose header starts with Candidate/ID and has
    at least 3 more columns; rows -> {cand: {axis: value}}."""
    rows = [l.strip() for l in text.splitlines()]
    for i, l in enumerate(rows):
        if l.startswith("|") and i + 1 < len(rows) and re.match(r"^\|[\s:\-|]+\|$", rows[i + 1]):
            head = [h.strip() for h in l.strip("|").split("|")]
            if not re.match(r"(?i)^(candidate|id|cand)", head[0]) or len(head) < 4:
                continue
            m = {}
            for r in rows[i + 2:]:
                if not r.startswith("|"):
                    break
                cells = [c.strip() for c in r.strip("|").split("|")]
                cid = re.match(r"[A-Z]+\d*", cells[0])
                if cid:
                    m[cid.group(0)] = {head[j]: cells[j].lower() for j in range(1, min(len(head), len(cells)))}
            if m:
                return m
    return {}


def diversity(matrix, min_axes=3):
    ids = sorted(matrix)
    pairs, nearest = [], {}
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            axes = set(matrix[a]) | set(matrix[b])
            diff = sum(1 for x in axes if matrix[a].get(x) != matrix[b].get(x))
            for x, y in ((a, b), (b, a)):
                if x not in nearest or diff < nearest[x][1]:
                    nearest[x] = (y, diff)
            if diff < min_axes:
                pairs.append({"a": a, "b": b, "differ_on": diff})
    return {"ok": not pairs, "min_axes": min_axes, "too_similar": pairs,
            "nearest": {k: {"cand": v[0], "differ_on": v[1]} for k, v in sorted(nearest.items())}}


def cmd_diversity(a, path):
    if a.json:
        matrix = jload(a.json)
    else:
        src = a.md or os.path.join(path, "03-candidates.md")
        with open(src, encoding="utf-8") as f:
            matrix = parse_matrix_md(f.read())
    if not matrix:
        sys.exit("No candidate x axis matrix found (markdown table whose first column is 'Candidate').")
    res = diversity(matrix, a.min_axes)
    res["candidates"] = len(matrix)
    out(res)


# ------------------------------------------------------------------ main

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Scoring for architecture-planner")
    ap.add_argument("--workdir")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("--defaults", action="store_true")
    p.add_argument("--actions", choices=["yes", "no"], default="no"); p.add_argument("--criteria-file")
    p = sub.add_parser("weights"); p.add_argument("--swing", nargs="+", required=True)
    p = sub.add_parser("finalists"); p.add_argument("ids", nargs="+")
    p = sub.add_parser("anonymize"); p.add_argument("--lenses"); p.add_argument("--seed", type=int)
    p.add_argument("--add", nargs="*")
    p = sub.add_parser("add")
    for k in ("cand", "criterion", "lens", "evidence", "justification"):
        p.add_argument("--" + k, required=True)
    p.add_argument("--score", type=float, required=True); p.add_argument("--source")
    p.add_argument("--round", type=int)
    p = sub.add_parser("import"); p.add_argument("file"); p.add_argument("--round", type=int)
    p = sub.add_parser("anchors"); p.add_argument("--file", required=True)
    p = sub.add_parser("cards"); p.add_argument("--cards", required=True); p.add_argument("--only")
    p.add_argument("--out-suffix")
    sub.add_parser("validate")
    p = sub.add_parser("aggregate"); p.add_argument("--no-progress", action="store_true")
    p = sub.add_parser("table"); p.add_argument("--format", choices=["md", "json"], default="md")
    p = sub.add_parser("sensitivity"); p.add_argument("--runs", type=int, default=2000); p.add_argument("--seed", type=int, default=42)
    p.add_argument("--cands", help="comma-separated subset, e.g. H1,P1")
    p = sub.add_parser("voi"); p.add_argument("--plan", required=True)
    p.add_argument("--runs", type=int, default=2000); p.add_argument("--seed", type=int, default=42)
    p = sub.add_parser("diversity"); p.add_argument("--md"); p.add_argument("--json")
    p.add_argument("--min-axes", type=int, default=3)
    a = ap.parse_args()
    path = wd(a)
    {"init": cmd_init, "weights": cmd_weights, "finalists": cmd_finalists, "anonymize": cmd_anonymize, "add": cmd_add,
     "import": cmd_import, "validate": cmd_validate, "aggregate": cmd_aggregate, "table": cmd_table,
     "sensitivity": cmd_sensitivity, "voi": cmd_voi, "diversity": cmd_diversity,
     "anchors": cmd_anchors, "cards": cmd_cards}[a.cmd](a, path)


if __name__ == "__main__":
    main()
