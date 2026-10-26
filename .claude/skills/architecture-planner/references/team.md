# Team mode: several people, one run, one repository

Contents: Roles · Who decides what · Which stages run in parallel · Writing a task plan · The loop (and timeouts) · Starting and joining · Progress views · Safety properties · Windows · Without a shared remote

With `--team N` (N ≥ 2), N people each run this skill in their own Claude session on the same git repository. The pipeline is the same; the parallel stages are split into tasks that members work on at the same time. Coordination happens through files in `.arch/<run>/`, synced by git — no server needed.

**Your branch is never touched.** `.arch/<run>/` is a separate git worktree of an orphan branch `arch/<run>` (no project history). `team.py` commits, rebases and pushes only there; it never commits to, rebases, stashes or pushes your current branch, and `.arch/` is hidden from `git status` through `.git/info/exclude` (your `.gitignore` is not changed). Pushing anything else is a hard stop even on autopilot.

`scripts/team.py` does all coordination. In team mode use it **instead of** `progress.py`.

## Roles

- **Lead** — the person who started the run (`--team N`, no `--run`). The lead is the single *writer* of shared files, not the single *decider*. The lead's session does the lead-only stages, writes the shared stage files, creates task plans and merges results. The lead also takes tasks like everyone else.
- **Members** — join with `--run <id> --member <name>`. They work on tasks; everything they need is in the shared files. They never wait for the lead's human on a question (see "Who decides what").
- **Checkpoints** (brief, screen, prototype, decide) belong to the whole team. The grill is an interview in the lead's conversation: the lead's human answers it, teammates may help in the chat, and the whole team confirms the result at the brief checkpoint. Before it waits, the lead's session writes the question with its options and ★ recommendation to `checkpoint-<stage>.md` in the run folder, then runs `python scripts/team.py sync --run R --member <lead>` and only then waits. Without this sync the file stays local and members never see it. The team discusses it in its own chat; **any** team member's answer counts. Two different answers: the team settles it in chat; if it does not, the ★ option stands. The lead's session records the answer and who gave it (`stage … done --note "… — answered by <name>"`). On autopilot, the lead's session takes the recommended options and logs them in `AUTOPILOT-DECISIONS.md` in the run folder.

## Who decides what

Rule: **if the answer changes only your own task output, decide it yourself.** Do not send it to the lead.

| Question | Who decides | What to do |
|---|---|---|
| Inside your task: method, query, source, library to try, how to read an unclear instruction, format detail, where to stop within the time box | you (your session, with your human if needed) | decide, go on; log it under `## Decisions` in your task output: what, why, how to revert |
| Conceptual: goal, constraints, weights or anchors in the brief; cutting or reviving a candidate; anything that changes a shared file or another person's task | the team at the next checkpoint | **do not block.** Take the recommended option, mark it `assumed`, write it under `## For the checkpoint` (question, options, ★ and why), finish the task |
| Irreversible: spend beyond the approved budget, the final option, writing outside the run folder, any push other than `arch/<run>` (the hard stops in `modes-and-resume.md`); opening `02-scenarios-holdout.md` outside the stages that may use it (holdout check in stage 12, prototypes, report — `scenarios.md`) | the team at the next checkpoint | **do not do it.** The `assumed` answer is always the safe one: do not spend, do not open, ADR stays `proposed`. Finish the rest of the task and write the question under `## For the checkpoint` |

A wrong local decision is cheap: the merge shows it, and the team can revert it. A blocked member is expensive: the stage waits.

**How to revert a decision.** Never edit another person's task file: it breaks the single-writer rule; the edit may conflict on rebase or silently overwrite the author's output. Say it in the team chat; the lead's session records the revert in the merge output and in `TEAM-DECISIONS.md`, or adds a new task to the plan when the work must be redone.

## Which stages run in parallel

| Stage | Who | How it is split (task ids are examples) | Merge (lead) |
|---|---|---|---|
| read, grill, brief | lead | — | — |
| scenarios | everyone | one task per category: typical / hard / edge / adversarial (split further if N > 4), plus one **holdout** task given to a member who will not generate candidates if possible: it writes `02-scenarios-holdout.md` directly and reports only counts | dedupe the visible ones, number S1…Sn, write `02-scenarios.md`; never open the holdout file |
| wavea | everyone | the lead first names the families (`diversity-axes.md`); one task per family or pair of families; assign baseline, non-agentic and radical to specific tasks. Wave A tasks never receive prior art (it does not exist yet) | collect wave A cards (`--wave A`) |
| priorart | everyone | one task per search angle (the task itself, each major component, the domain); split the source categories in `sources.md` between tasks | merge into `02b-prior-art.md`, dedupe finds |
| generate (wave B) | everyone | one task per prior-art theme (ready-made parts, open-source forks, similar winners) | build `03-candidates.md`, axis matrix, `score.py diversity`, stop reason |
| screen | lead | — (team checkpoint) | — |
| research | everyone | one task per open question, in VOI order (`score.py voi`); stop assigning when all remaining VOI < 0.05 | write `06-research.md`, update entries, re-run `voi` |
| critique | everyone | one task per critic lens (labels and per-lens order from `critique-map.json`, inputs per the table in `critic.md`); each task writes `07-critique-<lens>.md` and a scores JSON; with N = 2, one member takes two lenses, finishing and importing one before starting the other | `score.py import` each lens, `aggregate`, `sensitivity`, `table` → `07-critique.md` |
| hybrid | lead | — | — |
| prototype | everyone | one task per option: leader (riskiest assumption), baseline, then runner-up — up to min(N, 3) options, within the approved cost | add measured entries, re-run `aggregate` |
| select | lead | — | — |
| report | everyone | one task per final option: write its full report section | assemble `ARCHITECTURE-REPORT.md`: summary, comparison, coverage matrix, recommendation |
| decide | lead | — (the team picks the option at the checkpoint) | — |

Every parallel stage ends with exactly one merge task (`"merge": true`), which unlocks only when all other tasks of the stage are done.

Every merge also copies each task's `## Decisions` and `## For the checkpoint` into `TEAM-DECISIONS.md` in the run folder (stage, task, author, decision, why, status). Status is `decided` (local), `assumed` (open for a checkpoint), `confirmed <stage>` or `reverted <stage>`; the lead's session updates it when a checkpoint closes the item. The lead does not re-judge local decisions; it only flags a conflict between two of them. Open `## For the checkpoint` items go into the next checkpoint question.

## Writing a task plan (lead)

Members do not see the lead's conversation. Each task must therefore be self-contained:

Write the plan to a JSON file in the run folder, then pass the file (this works the same in bash and PowerShell; inline JSON quoting breaks in PowerShell):

```json
[
  {"id":"R1","title":"Tool-calling accuracy with 30+ tools",
   "instructions":"Answer R1 from 05-research-plan.md for C1 and C6. Format per SKILL.md stage 10. Sources + confidence; give the scores for yes/no."},
  {"id":"R2","title":"p95 latency of the search API","instructions":"..."},
  {"id":"R-merge","title":"Compile 06-research.md","merge":true}
]
```

```bash
python scripts/team.py plan --run hack1 --member anna research --tasks-file .arch/hack1/plan-research.json
```

`instructions` names the shared files to read, the exact output expected and the format. Do not write "ask the lead" into a task: the member decides inside the task and logs it (see "Who decides what"). Non-merge tasks are dealt round-robin across joined members; merge tasks go to the lead.

The brief (`01-task-brief.md`) must be complete enough that someone who missed grill can work from it: members decide their own questions from it.

## The loop every session runs

```
next → (task) start → do it → write output → done → next …
```

1. `team.py next --run R --member ME --wait 90` returns one of:
   - `task` — do it. `start` it, read its `instructions`, the shared files it names and the relevant references, write the output to the path `start` returns (`tasks/<stage>/<task>.<me>.md`), then `done` with a one-line `--note` (and `--score` for critique tasks). A question on the way: decide or assume and log it ("Who decides what"); never stop to wait for the lead.
   - `lead_stage` (lead only) — do that lead-only stage as described in SKILL.md, save its file in the run folder, `stage … done --note`; or, for a parallel stage with no plan yet, create the plan.
   - `wait` — nothing to do yet (the lead is on a lead-only stage or a checkpoint, or others are finishing). Tell the user briefly who you are waiting for; at a checkpoint, show them `checkpoint-<stage>.md` and remind them that they can answer it in the team chat. Then call `next --wait 90` again (the answer's `retry_after` says how soon). Keep waiting until `finished`; do not end the session early.
   - `finished` — the run is complete; point the user to the report.
   **Timeouts:** Claude Code kills a Bash call after 120 s by default (600 s at most). Call `next` with a Bash `timeout` of at least (wait + 30) seconds — 120 000 ms for the default `--wait 90`. Never ask for more than 540 s; larger values are capped at 540 and the answer says so (`wait_capped`).
2. Work is first taken from your own assignments, then resumed if you had started something, then **stolen** from others who have not started theirs. So fast members automatically help slow ones. Merge tasks are never stolen.
3. Every command that changes state syncs automatically: commit inside the arch worktree, rebase on `<remote>/arch/<run>`, push only `arch/<run>`. The remote is taken from `--remote`, then `git config arch.remote`, the only remote, the current branch's upstream, then `origin`. If a sync reports an error, run `team.py sync` again; if it persists, show the user the error.

## Starting and joining

Lead:
```bash
python scripts/team.py init --run <id> --member <name> --input <folder> --express yes|no --team N --task "<short title>"
```
Choose `<id>` as a short slug (e.g. `hackathon-1005`). `init` creates the orphan branch `arch/<id>` and its worktree in `.arch/<id>/`, pushes that branch only, and prints the command to give teammates. Add `--autopilot yes [--budget N]` if nobody on the team will answer checkpoints. With several remotes, add `--remote <name>` (remembered for later commands):
`/architecture-planner --run <id> --member <their-name>`

Member:
```bash
python scripts/team.py join --run <id> --member <name>
```
`join` fetches `arch/<id>` and creates the member's own worktree in `.arch/<id>/`. After joining, read the input folder and every shared file that already exists, then start the loop.

Member names: lowercase, no spaces; default to the git user name.

## Progress views

- Task list: `team.py todos --run R --member ME` → pass to TodoWrite / task tools after every state change. It shows all stages, plus the current stage's tasks with who is doing each and "(me)" on your own.
- Dashboard: `team.py board --run R` writes `.arch/<run>/.local/board.html` (git-ignored, refreshes every 10 s): pipeline, every task as a chip coloured by state with its owner, and a per-person load table. Re-run `board` after each sync.
- Chat: the usual 3-line status block at each stage boundary.

## Safety properties (why this does not break)

- All git work happens in the `arch/<run>` worktree; the project branch, index and working tree are never touched.
- Every file has a single writer: shared stage files, plans, stage states, `checkpoint-<stage>.md` and `TEAM-DECISIONS.md` are written only by the lead's session; task files carry the member's name in the filename. Rebases therefore never conflict. Decisions are spread across the team; only the writing is not.
- If two people ever do the same task (a rare race while stealing), the first `done` wins and the other output is ignored.
- `.local/` is ignored inside the arch branch, so local dashboards never cause commits.
- A member who leaves mid-task: their started task stays theirs; the lead can finish it by writing the output under the lead's own name and calling `done` — the stage then completes.

## Windows

- Use `py` if `python` is not found. All script paths use forward slashes.
- Always pass task plans with `--tasks-file`, never inline JSON.
- The scripts write UTF-8 and force UTF-8 console output, so Cyrillic names and notes are safe in PowerShell.
- Git for Windows must be installed and authenticated for the shared remote (the scripts call `git`).

## Without a shared remote

- Several sessions on **one machine**: give each session its own **git worktree** of the project (`git worktree add ../<repo>-2`) or its own clone. Worktrees are the lighter choice: all worktrees of one repo share refs, so they exchange the run through the local branch `arch/<run>` even without any remote. (Each session's `.arch/<run>/` is checked out detached, so git's one-branch-per-worktree rule does not get in the way.) Separate clones need a shared remote.
- Or run all sessions in **one plain folder** with `ARCH_NO_GIT=1`: no git at all, safe because every file has a single writer.
- One GitHub account for everyone is fine: members are identified by `--member`, not by their GitHub or Claude account.
- No remote and different machines: team mode is not possible; run solo.
