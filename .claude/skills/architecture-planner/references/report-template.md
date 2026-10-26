# Final report template — ARCHITECTURE-REPORT.md

Write the report in the user's language. Structure:

```markdown
# Architecture options: <task name>

## Decided without you   (autopilot runs, or team runs with open `assumed` items; omit otherwise)
Every decision taken on autopilot (from AUTOPILOT-DECISIONS.md), every assumed grill answer, and every TEAM-DECISIONS.md item still with status `assumed`, ordered by how much it can move the ranking (critical weights from `score.py sensitivity`). Hard stops first: what still needs the user (budget, ADR acceptance, writing to the repo).

## One-page summary
The whole decision on one screen: the options one line each, the comparison table, how robust the leader is ("C3 wins 78 % of weight perturbations, C7 19 %"), the conditional recommendation. If the baseline prototype came within 10 points of the leader, say so here first. Someone who reads only this section can choose.

## The task in two paragraphs
Essence, key constraints, what success means, criterion weights (including Safety / blast radius) and how they were set (swing weighting or defaults).

## Comparison
| # | Architecture | Wave | One-line essence | Total, % | Win share | Confidence | Critical scenarios passed | Build time | Main risk |
|---|---|---|---|---|---|---|---|---|---|

Every number comes from `score.py` (`table --format md`, `sensitivity`), never from mental arithmetic. Total = Σ((median − 1)/4 × weight), 0–100 %.

Per-criterion score table (median panel scores; evidence tag M/R/W/A from the `evidence` field). Then the robustness block: win shares, verdict (robust ≥ 80 %, likely ≥ 60 %, tie < 60 %) and the critical weights that would change the leader.

Where the options came from: which finalists and selected options came from wave A (blind) and which from wave B (after prior art).

## Scenario coverage
Matrix: architecture × scenario → ✅ handles / ⚠️ partial / ❌ fails, critical scenarios marked. Prototype results, where they exist, replace walkthrough results and are marked as measured. Holdout scenarios in separate columns, with whether the holdout was sealed by a subagent or written later in the same context.

## Prototype results
Leader vs baseline (control group), plus the runner-up if built: pass rate (overall / critical / holdout), latency, cost per run, failure modes, what was harder than expected. The leader's riskiest assumption: confirmed or refuted. **If the baseline came within 10 points of the leader, lead with that.** Build speed observed in a Claude spike is labelled "measured (Claude spike)" and not taken as the team's speed. How the measurements changed the ranking. If prototyping was skipped, say why (e.g. "no budget approved").

## What a jury will ask
The 4–6 questions an expert juror who reads the repo would ask (numbers under favourable conditions, complex vs simple, tuned and tested on the same sample, demo surface, additional sources), each with the honest answer for the recommended option and the measurement behind it.

## Stage impact
Paste `STAGE-IMPACT.md`: what each stage changed, and which stages changed nothing.

## Recommendation
- If <X> matters most → option <A>, because …
- If <Y> matters most → option <B>, because …
  (Build these conditions from the critical weights: "if latency weighs more than ~18 instead of 9, B leads".)
- What to validate next (the riskiest remaining assumption) and how to do it in the least time.
- The scenarios in `02-scenarios.md` are ready to use as the first eval set for the implementation.

## Option 1: <name>
### Essence and core bet
For a hybrid: which parts come from which parent options, and its result against P+ (best parent + cheapest fix) and on the holdout scenarios, shown separately from its visible-scenario scores.
### Diagram
Mermaid flowchart or ASCII: components, data flow, where the LLM is called, where tools are, where checks happen.
### Components
Each component: role, input/output, model or technology.
### Actions & permissions
What it can do in the outside world, tool rights, what is reversible, where confirmations sit; its Safety / blast radius score.
### Walkthrough: one typical and one hard scenario
### Pros (detailed, tied to the task and to findings)
### Cons (detailed, with severity: blocker / serious / tolerable)
### Failure scenarios and mitigations
### Rubric scores (median score, anchor applied, evidence level from `scores.json`, justification per criterion)
### Where the critics disagreed (if anywhere)
### Build plan
Build order: what to do first to get a working end-to-end skeleton as early as possible; effort estimate per step.
### Choose it when / avoid it when

(… remaining options in the same format …)

## Rejected candidates
Table of all cut candidates (including user vetoes at the screening checkpoint): name | essence | reason for rejection.
Separately: ideas from rejected candidates worth borrowing into the finalists.

## Critic panel
The lenses used and why (reliability & security is mandatory when the system acts on the outside world). How independent the panel really was: separate subagents or sequential roles in one context; one model or several; anonymised labels and shuffle seed. The most important disagreements (spread ≥ 2) and how they were resolved.

## Research
Key findings that affected the scores, with sources and confidence. Questions researched in VOI order; questions not researched (VOI < 0.05 or limit reached) listed with their VOI. Questions that went unanswered.

## Assumptions and open questions
```

Requirements:
- Match length to what the decision needs; no filler sections. A section with nothing task-specific to say is one line or omitted.
- Pros and cons are the heart of the report. Every point explains why it matters for this task specifically.
- Diagrams are mandatory for all final options.
- In express mode the report covers 3 options, and sections for skipped stages say briefly why they were skipped.
- The number of final options follows the run: up to 5 (express 3); fewer if the solution space was narrow.
- Do not repeat the same content across sections; cross-reference instead.

The ADR and build plan for the chosen option are written in the `decide` stage; see `decision.md`.
