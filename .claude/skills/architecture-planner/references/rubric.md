# Scoring rubric

Contents: Criteria and default weights · Absolute anchors · Asking for weights (swing weighting) · Formula · Evidence levels · Ties and robustness · Notes

All arithmetic (medians, totals, sensitivity, VOI) is done by `scripts/score.py`; see `scoring.md`. Never compute a total by hand, and never write a total in a report that did not come from `score.py`.

## Criteria and default weights

Weights come from `01-task-brief.md`, set with the user during grill (swing weighting below). Use a default only when the user gives no answer, and flag it in the report. Criteria weighted 0 are not scored.

| Criterion (id) | What is assessed | Default, no external actions | Default, system acts on the outside world |
|---|---|---|---|
| Result quality (`quality`) | How well it solves the core task on typical and hard cases | 24 | 23 |
| Reliability (`reliability`) | Behaviour under LLM/tool errors, no compounding errors | 14 | 14 |
| Speed of implementation (`build_speed`) | Can **this team** ship it on time; moving parts | 14 | 14 |
| Cost (`cost`) | Tokens, calls, infrastructure at expected volume | 10 | 9 |
| Latency (`latency`) | Response time against the requirement | 9 | 9 |
| Debuggability (`debuggability`) | Can you tell why it did what it did, and fix it fast | 9 | 9 |
| Evaluability (`evaluability`) | How easily the scenarios can be run and measured | 5 | 4 |
| Extensibility (`extensibility`) | Adding capabilities without breaking old ones | 5 | 4 |
| Demo impact (`demo`) | For hackathons and pitches; raise it for a hackathon | 5 | 4 |
| Safety / blast radius (`safety`) | Worst thing it can do on a model error or prompt injection; reversibility; confirmations; tool permissions | 5 | 10 |

"Acts on the outside world" = writes, sends, pays, deletes, books, posts, or changes anything outside its own sandbox. `score.py init --defaults --actions yes|no` writes exactly these numbers.

### Hackathon: the jury's criteria 1:1

When the input has judging criteria with points, do not squeeze them into the ten defaults. Write `criteria.json` with **one criterion per jury criterion** (weight = its points, scaled) plus `build_speed` and `reliability`, then `score.py init --criteria-file criteria.json`. Swing weighting then adjusts only build_speed and reliability against the jury block. The report's comparison table then reads directly in jury terms.

## Absolute anchors

Every level of the scale is **absolute**: it describes the architecture against the task's thresholds, never against other candidates. Adding or removing a candidate must never change anyone else's score.

In stage 3, write an anchor table into `01-task-brief.md` for every criterion with weight > 0, filling the thresholds from the brief (target latency, budget, deadline, scenario counts). Store the same anchors in `scores.json → criteria[].anchors`; `score.py validate` fails if any level 1–5 is missing and warns while placeholders like `<target>` remain. Critics cite the anchor they applied ("anchor 4: p95 1.8 s ≤ target 2 s").

Default templates (adapt the numbers to the brief):

| Criterion | 5 | 4 | 3 | 2 | 1 |
|---|---|---|---|---|---|
| quality | All critical and ≥ 90 % of all scenarios pass | All critical, ≥ 75 % | All critical, ≥ 60 % | Fails 1 critical or < 60 % | Fails 2+ critical |
| reliability | Every LLM/tool error caught and handled; no unchecked multi-step chain | Main path handled; ≤ 1 unguarded chain | Retries exist, some failures reach the user as wrong output | Several unguarded chains; loops or drift possible | One bad LLM output can silently corrupt the result or loop forever |
| build_speed | Team builds end-to-end in ≤ 25 % of the time available | ≤ 50 % | ≤ 75 % | ≤ 100 %, no slack | Needs more time or skills than the team has |
| cost | ≤ 25 % of budget at expected volume | ≤ 50 % | ≤ 75 % | ≤ 100 % | Over budget |
| latency | p95 ≤ target/2 | p95 ≤ target | ≤ 1.5 × target | ≤ 2 × target | > 2 × target |
| debuggability | Every step's input/output logged and replayable; failure located in minutes | All LLM and tool calls logged | Partial logging; some failures need re-running | Hidden state or free-form loops make failures hard to reproduce | No way to tell why it did what it did |
| evaluability | Scenarios run automatically with checkable pass criteria | Automatic, some criteria need an LLM judge | Semi-manual | Manual spot checks only | Outputs not comparable across runs |
| extensibility | New capability = new module/tool, nothing existing changes | Touches one component | Touches several | Requires restructuring control flow | Effectively a rewrite |
| demo | Visible, working, original result on the judging criteria in < 2 min | Working demo, clear on most criteria | Working but unremarkable | Needs explanation or mock-ups | Nothing convincing to show |
| safety | Read-only, or every external action reversible and confirmed | Irreversible actions confirmed; tools least-privilege | Reversible actions unconfirmed; irreversible ones confirmed | Some irreversible actions unconfirmed, but no untrusted input reaches them | Irreversible external actions without confirmation while untrusted input is present |

**Measurable tasks (ML, estimation, ranking, forecasting): anchor quality on the metric, not on the share of scenarios.** Use the trivial answer from `00b-probe.md` as the bar and the judge's conditions as the setting:

| quality | 5 | 4 | 3 | 2 | 1 |
|---|---|---|---|---|---|
| metric task | Beats the bar by ≥ <big margin>, measured on unseen sessions under the judge's conditions, significant (bootstrap) | Beats the bar by ≥ <margin>, measured or researched on a comparable setup | Plausibly beats the bar, not measured | Unclear whether it beats the bar | Likely at or below the bar |

A scenario share can be a second quality criterion (`scenario_coverage`), not a substitute.

**Gates raise the ceiling.** Reliability 4–5 and safety 4–5 require the named gates from the craft pass (`craft.md`): independent re-check before action, evidence gate, freshness per signal, rejection with a code. Evaluability 5 requires a locked test with a logged access command, dev/validation/test, and the simple baseline in the same script.

Qualitative criteria (debuggability, extensibility, safety, reliability) are anchored in **observable** properties of the design — what is logged, what needs confirmation, how many components a change touches — so two critics reading the same card land on the same level.

Speed of implementation is about the **team** in the brief. A Claude-built prototype shows the design is buildable, not how fast this team will build it (see `prototype.md`).

## Asking for weights (swing weighting)

People are bad at "split 100 % between nine criteria". In grill, ask instead:

1. "Imagine an option that is the worst on every criterion. You may improve **one** criterion from worst to best. Which one first? Which second?" — continue until the order is clear (ties allowed; criteria the user does not care about get 0).
2. "The first improvement is worth 100 points. How many is the second worth? The third?" (Or as ratios: "how many times more valuable is the first than the second?")
3. Convert with `score.py weights --swing quality=100 latency=60 safety=40 …` → integer weights summing to 100. Write them into the brief and read them back to the user in one line.

Alternative when the user prefers comparisons: ask pairwise "which matters more, A or B, and roughly how much more?" for the top 4–5 criteria and turn the answers into points the same way.

## Formula

Total = Σ((median score − 1) / 4 × weight) over criteria with weight > 0, with weights summing to 100 → **0–100 %**. All 1s give 0 %, all 3s 50 %, all 5s 100 %.

## Evidence levels

Every score carries one, in `scores.json → entries[].evidence`; the report shows it from there:
- **measured** — from a prototype run on the scenarios. For a cell (candidate × criterion), measured entries override everything below.
- **researched** — backed by a finding in `06-research.md`.
- **walkthrough** — follows from a scenario walkthrough.
- **assumption** — general knowledge or judgement only.

Confidence in a total is the weight share of measured + researched cells: high ≥ 60 %, medium ≥ 30 %, else low (computed by `score.py aggregate`).

## Ties and robustness

A single total hides how fragile it is. `score.py sensitivity` perturbs every weight by ×0.7–1.3 (2000 runs) and moves uncertain scores (assumption ±1, walkthrough ±0.5), then reports each candidate's **win share** and the **critical weights** — the smallest change of one weight that changes the leader.

- The leader wins ≥ 80 % of perturbations → robust; 60–80 % → likely; **< 60 % → tie**. The old "gap under 5 points" rule is replaced by this.
- In a tie, the recommendation is conditional and built from the critical weights: "if latency matters about twice as much as you said, C7 leads".

## Notes

- Hard constraints are a filter, not a criterion. A violation means rejection; no score compensates.
- If the system acts on the outside world, the reliability/security lens is mandatory in the critic panel and at least two adversarial scenarios are critical (`critic.md`, `scenarios.md`).
