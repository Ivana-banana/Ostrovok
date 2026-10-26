# Scoring with `score.py`

Contents: Rule · Files · Workflow by stage · Research plan and VOI · Diversity check · Command reference

## Rule

The model judges; the script counts. Critics produce **entries** (one score per candidate × criterion × lens, with evidence and justification). Medians, totals, disagreements, confidence, win shares and VOI come only from `scripts/score.py`, and every number in `07-critique.md`, `08-hybrid.md` and the report is copied from its output (`score.py table --format md` gives ready tables). If you notice yourself adding up scores, stop and run the script.

All commands take `--workdir <dir>` (same as `progress.py`).

## Files (in the working directory)

| File | Written by | Content |
|---|---|---|
| `scores.json` | `init`, `weights`, `finalists`, `add`, `import` | criteria (weights + anchors), finalists, entries |
| `critique-map.json` | `anonymize` | seed, label → candidate id, shuffled order per lens |
| `scores-aggregate.json` | `aggregate`, `sensitivity`, `voi` | derived results; never edit by hand |
| `research-plan.json` | the agent, stage 8 | open questions in machine-readable form (below) |

Entry format (for `import`, one JSON list per lens):

```json
[{"cand": "B", "criterion": "latency", "lens": "builder", "score": 4,
  "evidence": "researched", "justification": "anchor 4: p95 1.8 s <= target 2 s (R2)",
  "source": "06-research.md R2"}]
```

`cand` may be an anonymous label from `critique-map.json`; the script maps it back.

## Workflow by stage

- **Stage 2–3 (grill, brief):** `init --defaults --actions yes|no` (hackathon with jury points: `init --criteria-file criteria.json`, see `rubric.md`), then `weights --swing …`; write the brief's anchor table to `anchors.json` and run `anchors --file anchors.json`. `validate` must pass apart from finalists.
- **Stage 9 (screen):** `finalists C1 C3 …` after the checkpoint.
- **Stage 10 (research):** `voi --plan research-plan.json` after preliminary scores exist (import the screening scores as `assumption` entries with lens `screen`); research in VOI order; after each answer update the affected entries (`evidence: researched`) and re-run `voi`.
- **Stage 11 (critique):** `anonymize --lenses <l1>,<l2>,<l3>`; write `cards.json` (one object per finalist: components, summary, axes, bet, risk, actions; `_titles` in the user's language) and run `cards --cards cards.json` for neutral per-lens cards; each lens imports with `import <file> --round 1` before the next lens starts; then `validate`, `aggregate`, `sensitivity`, `table --format md`.
- **Stage 12 (hybrid):** `anonymize --add H1 P1`, `cards` again, and the panel re-scores **all** finalists plus H1 and P1 with `--round 2` (`hybrid.md`). `validate` / `sensitivity` print `drift_warning` when compared candidates come from different rounds.
- **Stage 13 (prototype):** add measured entries (lens `prototype`, evidence `measured`) for what was measured; re-run `aggregate` and `sensitivity`. Build speed stays the team estimate (see `prototype.md`).
- **Stage 15 (report):** `table --format md` for the comparison; win shares and critical weights from `sensitivity`; skipped research questions with their VOI from the last `voi`.

**Replacing entries.** `add` / `import` with the same (cand, criterion, lens) replaces the old entry; the old one is kept in `scores.json → history`. **Screen entries** stand in for a cell only until any other lens scores it. **Prototype entries** (evidence `measured`) override every other level in their cell.

**Partial answers in VOI.** If a research answer is "partly", set the affected scores to the in-between values by hand with `add` (evidence `researched`) and mark the question answered; note it in `06-research.md`.

## Research plan and VOI

`research-plan.json`, written next to `05-research-plan.md`:

```json
{"questions": [
  {"id": "R1", "text": "Does <model> keep tool-call accuracy with 30+ tools?",
   "p_yes": "medium",
   "affects": [{"cand": "C1", "criterion": "reliability", "if_yes": 4, "if_no": 2},
               {"cand": "C6", "criterion": "quality", "if_yes": 4, "if_no": 3}],
   "status": "open"}
]}
```

`p_yes`: low / medium / high (0.25 / 0.5 / 0.75) or a number. Set `status` to `answered` or `not_found` when done.

`voi` re-runs the sensitivity analysis under the "yes" and "no" outcomes (same random draws, so an irrelevant question scores exactly 0) and reports for each question:
- `voi` — probability that the answer changes the leader, beyond the uncertainty already there;
- `expected_leader_share_change`, `flips_leader_if` (`yes`, `no`, both or neither).

Research in descending `voi`. **Stop** when every remaining question has `voi < 0.05` (`stop_research: true`) or the question limit is reached (express: 4). Pass each `voi` to the dashboard with `progress.py q add … --voi` / `q set … --voi`. Unresearched questions go into the report with their VOI.

## Diversity check

`score.py diversity --md 03-candidates.md` reads the candidate × axis matrix (a markdown table whose first column is `Candidate`, one column per axis) and lists pairs that differ on fewer than 3 axes, plus each candidate's nearest neighbour. Run it after every few candidates during generation (it drives the stop rule in `diversity-axes.md`) and paste the final report into `03-candidates.md`.

## Command reference

```
score.py init --defaults [--actions yes|no] | --criteria-file criteria.json
score.py weights --swing quality=100 latency=60 ...
score.py finalists C1 C3 C6
score.py anonymize --lenses reliability,builder,stakeholder [--seed N] [--add H1 P1]
score.py anchors --file anchors.json
score.py cards --cards cards.json [--only H1,P1] [--out-suffix -r2]
score.py add --cand C1 --criterion latency --lens builder --score 4 --evidence researched --justification "..." [--source "..."] [--round N]
score.py import entries.json [--round N]
score.py validate
score.py aggregate [--no-progress]
score.py table [--format md|json]
score.py sensitivity [--runs 2000] [--seed 42] [--cands H1,P1]
score.py voi --plan research-plan.json [--runs 2000] [--seed 42]
score.py diversity (--md 03-candidates.md | --json matrix.json) [--min-axes 3]
```
