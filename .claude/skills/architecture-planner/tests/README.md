# Tests

Standard library `unittest` only. From the skill folder (`architecture-planner/`):

```bash
python -m unittest discover -s tests -v        # all tests (use py or python3 if needed)
python -m unittest tests.test_score            # one file
```

| File | Covers |
|---|---|
| `test_score.py` | formula (0 / 50 / 100 %), medians, totals, disagreements, measured override, every `validate` rule, sensitivity (dominance → 100 %, mirror tie → ~50 %, fixed seed), VOI (irrelevant question = 0, leader-dropping question first), diversity check, `weights` CLI |
| `test_progress.py` | numeric `--score` 0–100, `--wave`, VOI ordering, autopilot (non-blocking `wait`, `AUTOPILOT-DECISIONS.md`, badge), backwards-compatible CLI, legacy console encoding |
| `test_team.py` | needs `git`: unpushed local commits and the user's branch untouched, identical `git status`, two clones exchanging tasks via `arch/<run>`, remote not named `origin`, worktrees without a remote, `ARCH_NO_GIT`, `next --wait` timing, cap and `retry_after` |

`test_team.py` builds throwaway repositories with a bare remote in a temp folder and is skipped when `git` is not installed. The whole suite runs in about 15 seconds.
