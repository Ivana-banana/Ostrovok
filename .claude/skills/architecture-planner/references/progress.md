# Progress tracking (solo mode)

This file covers solo runs (`--team 1`). Team runs use `scripts/team.py`; see `team.md`.

The user wants to follow the process live: what is done, what is happening now, what comes next. `scripts/progress.py` keeps the state in `<workdir>/progress.json` and re-renders two views after every command:

- `progress.html` — a dashboard (pipeline track, "now" and "up next", candidate funnel 15 → 8 → 5 with a status table, research questions with findings, saved files, activity log). It auto-refreshes every 4 seconds while the run is in progress and stops refreshing when all stages are done.
- `PROGRESS.md` — the same information as plain text.

## Commands

```bash
# use python3 / python / py, whichever exists; pass --workdir before the command
P="python <skill-dir>/scripts/progress.py --workdir /path/to/_arch"

$P init --task "Support-ticket triage agent" --lang ru --mode full   # [--autopilot yes --budget 5]
$P stage read start
$P now "Reading task.md and 6 research notes"
$P next "Write the inventory" "Start grill round 1"
$P artifact /path/to/_arch/00-inventory.md --label "Inventory"
$P stage read done --note "9 files, 2 contradictions found"
$P impact read --note "telemetry referenced by the spec is missing" --minutes 6

$P cand add C1 "Single ReAct loop" "One agent, 6 tools, no memory" --wave A
$P cand add C9 "Glue: vendor API + rules" "Ready ticket-routing API, rules on top" --wave B
$P cand set C4 screened_out --reason "Violates 2 s latency limit"
$P cand set C1 finalist
# scores are written by `score.py aggregate` (number 0..100); by hand only if needed:
$P cand set C1 --score 62.5
$P cand set C1 selected

$P q add R1 "Does <model> keep tool-call accuracy with 30+ tools?" --cand C1,C6 --voi 0.18
$P q set R1 searching
$P q set R1 answered --answer "Degrades past ~20 tools; grouping helps" --confidence medium

$P wait "Review the screening: revive, veto or add an idea?"
$P proto add C1
$P proto set C1 running
$P proto set C1 done --result "7/10 pass (3/3 critical), p50 2.4 s, ~$0.01/run"

# after every stage: what it changed (feeds STAGE-IMPACT.md and the report)
$P impact research --leader-before C9 --leader-after C2 --added "gate:evidence" --final yes --minutes 14 --note "backtest: baseline 0.45"
$P impact craft --added "C3:non-headless browser,C5:two keys" --final unknown --note "4 gates added to all finalists"

$P status          # JSON: first unfinished stage, open questions, waiting checkpoint
$P log "User changed the latency budget to 5 s"
$P autopilot yes --budget 5      # user left; checkpoints now take the recommended option
$P log "AUTOPILOT: kept C6 as finalist — strongest non-agentic option"   # also -> AUTOPILOT-DECISIONS.md
```

Stage ids: `read probe grill brief scenarios wavea priorart generate craft screen research critique hybrid prototype select report decide`. `stage <id> start` also sets "now" to the stage name. `wait` shows a highlighted "Waiting for you" banner (and marks the active task "(waiting for you)") until the next `now` or `stage` command; on autopilot it does not block, it records the checkpoint and reminds you to log the decision. The dashboard shows an AUTOPILOT badge, a Wave column for candidates, and research questions sorted by VOI.

## When to update

- Stage start and stage done (always with a one-line `--note` result).
- Before any action that takes noticeable time: reading a large file, each research query, running the critic, writing the report.
- Whenever the plan changes, refresh `next`.
- At every checkpoint and grill round, use `wait` with what you need, e.g. "Answer round 2 (4 questions)".
- Prototypes: `proto add` when proposed, `proto set … running` / `done` / `failed` with measured results.
- Every saved stage file → `artifact`.
- After every stage → `impact <stage>`: leader before/after (from the last `aggregate`, empty before scores exist), what it added or removed, whether that reached the final design (`--final yes|no|unknown`; set it again in stage 14 for stages left `unknown`), minutes spent. Be honest: "changed nothing" is a useful record.

## Showing it to the user

- **Claude Code / desktop:** after `init`, tell the user the path to `progress.html` once so they can open it in a browser or the VS Code side panel; it refreshes itself. Also keep the built-in task list in sync (next section).
- **claude.ai / mobile:** files are not live. Present `progress.html` with `present_files` at three points: after the brief is confirmed, after screening, and at the end (copy it to `/mnt/user-data/outputs/` first).
- **Everywhere:** at each stage boundary post a short status block in chat, in the user's language, for example:

```
✅ 3. Generated 15 candidates (4 agentic loops, 3 pipelines, 2 multi-agent, …)
▶️ 4. Critic screening — checking hard constraints
⏳ Next: research 6 open questions → deep critique
```

Keep it to 3 lines; the dashboard holds the details.

## Built-in task list (Claude Code, Cowork)

The task list is shown right in the terminal, so for Claude Code users it is the primary live view. `progress.py todos` turns the current state into a ready-made list:

- 15 stage items (`completed` / `in_progress` / `pending`), completed ones carrying their one-line result.
- While the research stage runs: one sub-item per research question (answered, not found, pending).
- While the critique stage runs: one sub-item per finalist, completed once it has a score.
- While the prototype stage runs: one sub-item per prototype, with its measured result once done.
- Exactly one `in_progress` item, as task tools expect: the active stage. A sub-item being worked on is labelled "(in progress)" instead.

```bash
python <skill-dir>/scripts/progress.py todos
# → {"todos": [{"content": "...", "status": "in_progress", "activeForm": "..."}, ...]}
```

How to sync:
- **TodoWrite:** call it with the `todos` array exactly as printed. It replaces the whole list each time.
- **Separate create / update task tools:** create one task per item on the first sync; on later syncs, update only items whose status changed and create items that appeared (new research questions, finalists).
- Sync after `init`, after every `stage` command, after each research question is answered, and after each finalist is scored. Do not wait until the end of a stage — a list that lags behind the real work is worse than none.

The `progress.py` state is the single source of truth: change it first, then regenerate the list, rather than editing the task list by hand.
