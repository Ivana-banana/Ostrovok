# Changelog

## 2026-10-13 — team mode: decisions spread across the team

Why: members escalated every small question to the lead's human, who just said yes. The lead became a bottleneck without adding judgement.

1. **Who decides what** (`references/team.md`): a question that changes only the member's own task output is decided by the member and logged under `## Decisions` (what, why, how to revert).
2. Conceptual questions never block: the member takes the ★ option as `assumed`, logs it under `## For the checkpoint`, and finishes the task. Irreversible ones (budget, final option, writes outside the run, pushes, and the holdout outside stage 12, prototypes and the report) are never done: their `assumed` answer is the safe one.
3. Merge tasks collect both sections into `TEAM-DECISIONS.md` with a status (`decided`, `assumed`, `confirmed`, `reverted`); the lead only flags conflicts. The grill stays the lead's interview; the team confirms it at the brief checkpoint.
4. Checkpoints belong to the team: the lead's session writes `checkpoint-<stage>.md` and syncs it before waiting, any member may answer, an unsettled dispute keeps ★, and the lead's session records who answered. Reverts go through the lead's merge or a new task — never by editing another person's task file. Open `assumed` items reach the report's "Decided without you". The lead stays the single writer of shared files, so sync safety is unchanged.

## 2026-10-08 — v2 (after blind comparison with 4 hackathon teams)

Why: in blind judging the v1 design beat the team twice and lost twice. Teams won on facts from real data and sites, craft and safety gates, evaluation honesty; expert feedback added weak "additional sources", a weak 2D editor and thin domain (chemistry) knowledge. Panel runs drifted by 7–22 points between rounds.

1. **New stage 1 `probe`** (`references/probe.md`): 15–30 min on the real data and sources — traps, the trivial answer (the bar), what the judges' checker sees, one or two real requests per external source; **domain primer** `00c-domain.md` for deep domains.
2. **Grill through `/grilling`** (`references/grill.md`): rated options, one ★, and a deep recommendation per question (why, what it changes, risk if wrong, when another option wins); autopilot writes the same rounds to `01a-grill.md`.
3. **Independent scenario writer** writes all scenarios (not only the holdout); required trap, judge-conditions, recovery, jury-click and source-yield scenarios.
4. **New stage 8 `craft`** (`references/craft.md`): a practitioner subagent adds named tricks and safety gates to every candidate (first hour on real data, gates before action, complex vs simple, judges' conditions, recovery, interactive surface, source strategy with ≥ 3 access paths and measured yield).
5. **Prior art mines craft** from the closest projects; new diversity axes 10 (how quality is proven) and 11 (source access strategy).
6. **Scale drift control:** entries carry `round`; `validate` and `sensitivity` print `drift_warning` when compared candidates come from different panel rounds; stage 12 re-scores all finalists with H1 and P1 in one round. Replaced entries are kept in `scores.json → history`.
7. **Rubric:** jury criteria 1:1 via `--criteria-file`; metric-based quality anchors against the probe's bar; gates required for reliability/safety 4–5.
8. **Panel:** stakeholder plays an expert juror who reads the repo; optional domain-expert lens.
9. **Prototype:** leader + P+ (or the complex component vs its simple alternative) + baseline + source yield; sanity-run of the stand; one run under the judges' conditions; paper and measured holdout kept apart.
10. **Stage impact log:** `progress.py impact <stage>` → `STAGE-IMPACT.md` and a report section, to learn which stages earn their cost.
11. **Tools:** `score.py anchors --file`, `score.py cards` (neutral per-lens cards), `--round` on `add`/`import`; documented replace semantics and screen/prototype rules.
12. **Hygiene:** stage 0 labels files condition / research / solution (solutions never reach the waves); "what the judges will run" and "what is missing" in the brief; one-writer lock per working directory.

Stages are now 0–16: read, probe, grill, brief, scenarios, wavea, priorart, generate, craft, screen, research, critique, hybrid, prototype, select, report, decide.

## 2026-10-05

### 1. `team.py sync` no longer touches the user's branch
- Run state lives in an **orphan branch `arch/<run>`**, checked out (detached) as a **git worktree** in `.arch/<run>/`. Worktree rather than a separate clone: it shares objects, remotes and credentials with the user's repo, and refs are shared across worktrees, so sessions in several worktrees of one repo can exchange state through the local ref even without a remote (reasoning in a comment in `team.py`).
- `sync` commits, rebases and pushes **only** inside that worktree and pushes only `HEAD:refs/heads/arch/<run>`. No `pull --autostash`, no commits, rebases, stashes or pushes on the user's branch.
- `.arch/` is added to `.git/info/exclude` (resolved with `git rev-parse --git-path`, so it also works from a linked worktree); the user's `.gitignore` is not changed.
- Remote: `--remote` (on every command) > `ARCH_REMOTE` > `git config arch.remote` > the only remote > the current branch's upstream remote > `origin`; several remotes and no way to choose → clear error.
- `join` fetches `arch/<run>` and creates its own worktree. `ARCH_NO_GIT=1` shared-folder mode unchanged.
- Fallback git identity for the arch worktree when `user.email` is unset.
- `team.md` "Without a shared remote" rewritten: worktrees are the recommended way to run several sessions on one machine.

### 2. `next --wait` fits inside Bash timeouts
- Default `--wait 90` (was 0); hard cap **540 s**, reported as `wait_capped` in the JSON answer.
- A `wait` answer carries `retry_after` and a hint with the Bash timeout to use.
- `team.md`: call Bash with `timeout` ≥ wait + 30 s; never above 540 s.

### 3. Total formula fixed
- `Total = Σ((score − 1) / 4 × weight)` → 0–100 % (all 1 = 0 %, all 3 = 50 %, all 5 = 100 %).
- The "gap < 5 points = tie" rule is replaced by sensitivity analysis: tie when the leader wins < 60 % of perturbations.
- Updated in `rubric.md`, `scoring.md`, `report-template.md`, `critic.md`, `progress.md` examples.

### 4. New `scripts/score.py`: the script does the arithmetic
- `scores.json` (criteria with weights and anchors, finalists, entries with cand / criterion / lens / score / evidence / justification / source).
- Commands: `init`, `weights`, `finalists`, `anonymize`, `add`, `import`, `validate`, `aggregate`, `table`, `sensitivity`, `voi`, `diversity`.
- `validate` checks range 1–5, evidence values, weights sum to 100, justification present, anchors present, unknown criteria, and holes (finalist without a score on a weighted criterion).
- `aggregate`: per-cell median across lenses, strongest-evidence-wins (measured overrides), totals, disagreements (spread ≥ 2), confidence from the measured + researched weight share; pushes totals to `progress.json` through `progress.py cand set --score`.
- Preliminary screening scores (lens `screen`) stand in only until the panel scores a cell.
- `progress.py cand set --score` now requires a number 0–100.
- SKILL.md and `critic.md`: critics output entries; every number in critique and report comes from `score.py`; evidence tags in the report come from the `evidence` field.

### 5. Critic panel independence
- `score.py anonymize`: random neutral labels, per-lens shuffled order, seed, `critique-map.json`; labels resolved back on aggregation.
- Neutral cards (no author name, neutral core bet, no evaluative words); per-lens input table in `critic.md`; a different model for at least one lens when selectable.
- Without subagents: one file per lens, scores imported before the next lens, no rereading earlier lenses.
- `07-critique.md` and the report state the seed, label map and how independent the panel was.

### 6. Hybrid without built-in advantage
- H1 goes through SCREEN first; compared with **P+** (best parent + cheapest fix of its main weakness), scored blind; must beat P+ by ≥ 5 points (sensitivity on `--cands H1,P1`, or holdout pass rate), otherwise P+ takes its place.
- Holdout scenarios (20–30 %, `Holdout: yes`, sealed `02-scenarios-holdout.md`) kept out of generation, critique and hybrid design; written by a subagent when possible, otherwise after H1/P+ are frozen and flagged as weaker.
- `08-hybrid.md` has SCREEN, P+ comparison and Holdout results sections; the report shows holdout results separately.

### 7. Prototype tests the leader against a control group
- Default: leader (built around its named riskiest assumption) + minimal baseline; runner-up third if time and budget allow (team: min(N, 3)).
- `results.md` of the leader has "Riskiest assumption: confirmed / refuted"; `09-prototype/baseline/` always exists or explains why not.
- Baseline within 10 points of the leader is the headline finding.
- Build speed from a Claude spike is labelled "measured (Claude spike)" and not transferred to the team; the build_speed entry stays an estimate.

### 8. Absolute anchors
- Every scale level in `rubric.md` is absolute; default anchor templates for all criteria, observable properties for qualitative ones.
- Anchor table in the brief (`references/brief-template.md`) and in `scores.json → criteria[].anchors`; `validate` enforces them and warns on placeholders; critics cite the anchor.

### 9. Weights: swing weighting and sensitivity
- grill-me asks swing questions; `score.py weights --swing …` converts points to integer weights summing to 100.
- `score.py sensitivity`: 2000-run Monte Carlo (weights × U[0.7, 1.3], assumption ±1, walkthrough ±0.5), win share per candidate (combined, weights-only, scores-only), verdict robust / likely / tie, critical weights (smallest single-weight change that flips the leader). The conditional recommendation is built from them.

### 10. Two-wave generation against anchoring
- New stage `wavea` (blind, before prior art) in both scripts; `generate` is now wave B after prior art. Reordered because in solo mode a file already in context cannot be unseen.
- `progress.py cand add/set --wave A|B`; dashboard Wave column; report shows which wave finalists and selected options came from.

### 11. Hierarchical generation with a stop rule
- 4–6 families by control flow, then variants; stop when a new candidate and two further attempts stay within 3 axes of a neighbour, or at the ceiling (full 15, express 8); floor 8 / 5, below which the narrow space is reported honestly.
- Screening keeps about half; final selection at most 5 (express 3).
- `score.py diversity` reads the candidate × axis matrix from `03-candidates.md` and lists too-similar pairs and nearest neighbours.

### 12. Safety / blast radius criterion
- New criterion with absolute anchors; default weight 10 % when the system acts on the outside world, else 5 %, other defaults rescaled.
- grill-me asks which outside-world actions the system takes; the reliability & security lens becomes mandatory and two adversarial scenarios critical when it does; cards get an "Actions & permissions" line.

### 18. Research in value-of-information order
- `research-plan.json` (affected candidates and criteria, scores on yes/no, prior p_yes).
- `score.py voi`: probability that the answer changes the leader beyond existing uncertainty (common random numbers, so irrelevant questions score exactly 0), expected change of the leader's win share, which answer flips the leader; sorted; stop when all remaining VOI < 0.05 or at the limit.
- `progress.py q add/set --voi`; dashboard and task list sort questions by VOI; the report lists unresearched questions with their VOI.

### 25. Autopilot
- `--autopilot yes|no` flag (also `progress.py init --autopilot`, `progress.py autopilot yes|no`, `team.py init --autopilot`), optional `--budget`.
- On autopilot `progress.py wait` does not block; `log "AUTOPILOT: …"` also appends to `AUTOPILOT-DECISIONS.md`; dashboard badge.
- Hard stops: prototype spend beyond `--budget` (none → skipped "no budget approved"), writing outside the working directory, any push other than `arch/<run>`, accepting the final option (ADR `proposed`).
- Brief section "Assumptions taken on autopilot"; report opens with "Decided without you".

### Also
- Tests in `tests/` (`unittest`, 50 tests) with `tests/README.md`.
- Fixed a broken CSS block in the solo dashboard.

## Behaviour changes that break compatibility

- **Team runs from earlier versions** (state committed into the project branch under `.arch/<run>/`) are not picked up: start a new run. A leftover plain folder at `.arch/<run>/` is moved aside to `.arch/<run>.old-<timestamp>`; files the old version committed to your branch stay there until you remove them.
- `team.py next` waits 90 s by default (was 0) and never more than 540 s.
- `progress.py cand set --score` rejects non-numeric values and numbers outside 0–100 (previously any string was stored).
- Totals use the new formula, so they are not comparable with totals from earlier runs (old range was 20–100).
- The tie rule changed from "< 5 points" to "leader wins < 60 % of perturbations".
- Scale levels are absolute; "5 = strongest option" no longer exists.
- Pipeline has a new stage `wavea` between `scenarios` and `priorart`; stage numbers in SKILL.md from 4 on shifted by one, and `generate` now means wave B. Old `progress.json` files get the new stage as `todo` automatically.
- Default weights changed (safety added, others rescaled).
- Candidate counts follow a stop rule (8–15 / 5–8) instead of exactly 15 / 8; finalists are "about half" instead of 7–8 / 4.
- Default prototype set is leader + baseline instead of the top two.
- On autopilot, `progress.py wait` no longer sets the "Waiting for you" banner; it prints a JSON reminder instead.
- `team.py` picks the remote by the rules above instead of upstream-or-`origin`, and exits with an error when several remotes exist and none can be chosen.
