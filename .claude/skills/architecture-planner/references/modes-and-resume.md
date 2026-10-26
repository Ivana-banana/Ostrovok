# Modes, autopilot and resuming

Contents: Choosing a mode · Express in team mode · Autopilot · Resuming

## Choosing a mode

| | Full | Express |
|---|---|---|
| When | Days available, or the decision is costly to reverse | Deadline in hours, or the user asks for speed |
| Scenarios | 10–20 | 6–8 |
| Prior-art search angles | 4–8 | 3 |
| Candidates (stop rule; floor–ceiling) | 8–15, wave A 7–8 | 5–8, wave A 4 |
| Finalists → final | about half → ≤ 5 | about half → ≤ 3 |
| Research questions | in VOI order until all have VOI < 0.05 | same, at most 4 |
| Critics | panel of 3 lenses | one critic covering all lenses |
| Hybrid | yes, if coherent | optional |
| Holdout scenarios | 3–5 | 1–2 |
| Prototype | leader + baseline (+ runner-up), after approval | skipped unless asked |

If the user has not said, infer from the deadline in the folder; if still unclear, ask once with a recommendation. The user can switch mode at any checkpoint; record it with `log` and adjust the remaining counts.

## Express in team mode
The same counts apply; there are simply fewer tasks per parallel stage. With `--team N`, express runs are often fast enough that prototyping one option becomes affordable — offer it at the prototype checkpoint.

## Autopilot

For runs nobody is watching ("I'm leaving, decide yourself", a scheduled run). Turned on by `--autopilot yes`, by those words, or mid-run with `progress.py autopilot yes [--budget N]`. Turn it on automatically after an unanswered checkpoint only if the environment can actually tell that the user has been away for a while; otherwise only explicitly.

On autopilot:
- **Grill:** still write every round in the `grilling` question format (options with ratings and one ★ option, plus the deep recommendation) into `01a-grill.md`; take each ★ as an assumption with status `assumed`, and put them in the brief under "Assumptions taken on autopilot — please review".
- **Checkpoints:** take the recommended option. Record each decision with `progress.py log "AUTOPILOT: <decision> — <why>"`; this also appends it to `AUTOPILOT-DECISIONS.md`. `progress.py wait` does not block on autopilot; it only records the checkpoint.
- **Report:** opens with a "Decided without you" block (in the user's language): every autopilot decision worth checking, ordered by how much it moves the ranking — use `score.py sensitivity` critical weights for weight assumptions, and say which assumed answers feed which scores.

**Hard stops — never decided on autopilot:**
- spending API budget on prototypes beyond the `--budget` approved at launch (no budget → prototype skipped with "no budget approved");
- writing into the repository outside the working directory (e.g. copying the ADR to `docs/adr/`) — leave it for the user;
- any git push other than to the run's own `arch/<run>` branch in team mode;
- choosing the final option: the ADR is written for the top recommendation with status **`proposed`**, not `accepted`, and `BUILD-PLAN.md` is marked draft.

At a hard stop, finish everything else, record the stop with `log "AUTOPILOT: stopped at <what> — needs your decision"`, and list it first in "Decided without you".

## One writer per working directory

Solo runs take a lock: at start, if `<workdir>/.lock` exists and is younger than 2 hours and names another session, stop and ask (autopilot: use `<workdir>-2`). Otherwise write `.lock` with the session name and time; refresh it at every stage boundary; delete it after stage 16. A warning from another session ("my limit is near") is not a hand-over: take over a working directory only after its owner writes "stopped" in the lock or hand-off file.

## Resuming

At the start, check the working directory for `progress.json`. If it exists:
1. Run `python scripts/progress.py status`. It reports the task, mode, the first unfinished stage, candidate statuses, open research questions, whether a checkpoint was waiting, and saved files.
2. Tell the user in 2–3 lines where the previous run stopped and offer: resume from that stage, or start over (start over = re-run `init`, which overwrites progress; keep old stage files in an `old/` subfolder rather than deleting them).
3. To resume, read the saved stage files in order (`00-inventory.md` … up to the last finished stage) to rebuild context — the files are the source of truth, not memory — then continue with the first unfinished stage. If that stage had partial output (e.g. some research answered), continue from where it stopped instead of redoing it.
4. If the previous run was waiting at a checkpoint, re-ask that checkpoint's question.

Team mode: state lives in `.arch/<run>/` in git. Any session (lead or member) resumes by re-running its loop (`team.py next`); `team.py status` shows where the run stands. The lead re-reads the shared stage files to rebuild context, exactly as above.
