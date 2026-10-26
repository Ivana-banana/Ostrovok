# Prototype

Goal: replace estimates with measurements where they can change the decision. A 30–60 minute spike often overturns the paper ranking — an elegant loop that keeps choosing the wrong tool, or a "too simple" pipeline that passes 9 of 10 scenarios. Prototyping only the top two would mostly confirm what is already in front; the control group is what makes the measurement informative.

## What to prototype

1. **The leader** (by `score.py aggregate`) — built around its **riskiest assumption**, not "a bit of everything". Name the assumption before writing code ("the router classifies ≥ 90 % of tickets correctly with one cheap call"), and build the smallest spike that confirms or refutes it.
2. **The minimal baseline** — the simplest candidate, as a control group. Always, unless it was cut for violating a hard constraint (then record why in `09-prototype/baseline/README.md`).
3. **P+ (or the simple alternative of the leader's riskiest component).** When the leader is a hybrid, measure P1 too: in earlier runs only measuring P+ showed that the gain came from one shared part (context features, one "same customer" signal), not from the hybrid's extra machinery. If the leader has a complex component (a model, a filter), measure it against its simple alternative from the craft pass (mean of two sensors, persistence, frequency); if it loses, the design ships the simple one by default.
4. **Source yield.** If the design depends on external sources, measure the usable share on ≥ 10 real queries (free, local, polite).
5. **The runner-up** — last, only if time and budget allow. In team mode: up to `min(N, 3)` options.

## Before building (checkpoint)

Propose to the user, in a few lines: which options, the leader's riskiest assumption, what each spike includes and excludes, which scenarios run (holdout included), the time box (default 45 minutes per spike), and the expected API cost (tokens × runs × price). `wait` for approval. Skip, with a note, if the user declines, there is no model or API access, or the environment cannot run code. Never spend the user's API budget without approval.

**Autopilot:** spend only up to the `--budget` approved at launch. With no budget, skip the stage with the note "no budget approved" (`progress.py stage prototype skip --note "no budget approved"`). If the estimate exceeds the budget, prototype only what fits (leader first) or skip — this is a hard stop, not a judgement call.

## Building a spike

- **Minimal end-to-end:** only what the core bet (for the leader: the riskiest assumption) needs. Stub the rest (fixed responses for external services, an in-memory store). The question is "does the core idea work?", not "is it production-ready?".
- Same model, stack, scenarios and pass criteria for every spike, so the comparison is fair.
- Folders: `09-prototype/<cand-id>/` and `09-prototype/baseline/` (the baseline's id is named in its README), each with code, a README of 3–5 lines (what is real vs stubbed) and `results.md`.
- Log every LLM call (prompt size, output, latency) so failures can be diagnosed.

**Sanity-run the stand first.** Run the scenarios once on a trivial stub and check that the harness itself produces no false failures (e.g. a constant synthetic signal flagged as "frozen"). Fix the stand, then count.

**Judge conditions.** Run at least once exactly as the judges will (signals available, unseen sessions, the official checker if any). Report that number first; numbers under easier conditions are labelled as such.

## Running and recording

For each spike: `proto set <id> running`, run the scenarios (critical first), then write `results.md`:

```markdown
# Results: <id>
Pass rate: x/y overall, a/b critical, h/k holdout
Latency: p50 …, worst …
Cost per run: …
Failure modes: <mode> — seen in S4, S9
Harder to build than the card suggested: …

## Riskiest assumption: confirmed / refuted     (leader only)
The assumption, the evidence, what it means for the design.
```

`proto set <id> done --result "7/10 pass (3/3 critical), p50 2.4 s, ~$0.01/run"`. A spike that could not be finished in the time box is `failed`, with why — itself evidence about the design's complexity.

## Feeding back

- Add measured entries to `scores.json` (lens `prototype`, evidence `measured`) for quality, reliability, latency and cost as measured, then re-run `score.py aggregate` and `sensitivity`. Explain any cell that moved by 2+ points.
- **Speed of implementation is not measured by a spike.** How fast Claude built it says little about how fast this team will. Record it in the justification as "measured (Claude spike): built in 35 min", but keep the build_speed entry at evidence `walkthrough` or `assumption`, adjusted for the team's skills from the brief.
- **Baseline headline:** if the baseline's scenario pass rate is within **10 percentage points** of the leader's (or above it), that is the main finding of the prototype stage and goes at the top of the report's prototype section and into the one-page summary.
- Keep paper and code apart: the report shows the holdout as `paper` (stage 12) and `measured` (here) in separate columns.
- Be honest about the limits: a spike on 10 scenarios is a signal, not a benchmark.
