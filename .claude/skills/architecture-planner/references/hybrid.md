# Hybrid

Goal: check whether combining the strongest parts of several options gives a design that beats each of them — for example, "the cheap deterministic pipeline from C2 for typical cases, plus the agent loop from C6 only when the classifier is unsure".

The hybrid has a built-in advantage: it is designed **after** reading the critique, aimed at the exact weaknesses the critics found, and would win on the same scenarios by construction. This procedure removes that advantage.

## Procedure

1. **Design.** From `07-critique.md`, list each finalist's best-scoring components and the scenarios where it shone; scan the cut candidates in `04-screening.md` for good ideas lost to a weak whole. Look for complementary parts (routing / cascade, a guardrail layer borrowed into another option, offline precomputation feeding an online loop, a swapped memory strategy). Check coherence: shared data formats, no pile of glue, no hard-constraint violation. If nothing coherent exists, skip the stage with a note on why. At most two hybrids (H1, H2). Write the usual card, including "Actions & permissions" and which parts come from which parent. **Do not read the holdout scenarios** while designing.
2. **SCREEN.** Run H1 through the same SCREEN checks as every candidate (`critic.md`): hard constraints, number of moving parts, whether this team can build it by the deadline. A hybrid that fails here stops here.
3. **Build P+.** Take the best parent by total and apply the **cheapest fix for its main weakness** (one component or one guardrail, not a redesign). P+ (id `P1`) is the honest competitor: "what if we just patched the leader?". Write its card too.
4. **Blind review — one round for everyone.** The same cards scored by two panel runs differ by 7–22 points (seen in four of four runs), which is larger than the 5-point rule below. So the panel **re-scores every finalist together with H1 and P1 in one round**: `score.py anonymize --add H1 P1`, `score.py cards --cards cards.json` for fresh neutral cards of all of them, then each lens imports with `score.py import <file> --round 2`. `validate` and `sensitivity` print a `drift_warning` if compared candidates come from different rounds — resolve it, never ignore it. Report the drift (round 1 vs round 2 total for each finalist) in `08-hybrid.md`. Then `score.py aggregate` and `score.py sensitivity --cands H1,P1`.
5. **Holdout check (paper).** A walkthrough on cards rewards verbose cards: a missing line reads as a failure. Label it `paper` and never let it override a measured holdout result from the prototype.
    Walk H1, P1 and the current leader through the **holdout scenarios** (`scenarios.md`) — with a subagent if available, otherwise as the last step after both cards are frozen. Record handles / partial / fails per scenario.
6. **Decide.** H1 keeps its place only if it beats P1 by **at least 5 percentage points** either (a) on total with a win share ≥ 60 % in `sensitivity --cands H1,P1`, or (b) on the holdout pass rate. Otherwise **P1 takes H1's place** in selection. Either way, both go into the report.

## `08-hybrid.md` structure

```markdown
# Hybrid

## Design
Card for H1 (and H2): parents of each part, Actions & permissions, core bet.

## SCREEN
Hard constraints, moving parts, deadline fit → pass / fail.

## P+ comparison
P1 card (best parent + cheapest fix). Totals and win shares H1 vs P1 from score.py
(paste the output), decision under the 5-point rule.

## Holdout results
| Scenario (holdout) | H1 | P1 | Leader | Note |
Pass rates; whether the holdout was sealed by a subagent or written after the cards were frozen.

## Verdict
H1 kept / replaced by P1, and why, in two sentences.
```

In the report, the hybrid's (or P1's) holdout results are shown separately from its scores on the visible scenarios.
