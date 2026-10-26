---
name: tests
description: Set the test mode for this project, or run the tests now.
argument-hint: "off | ask | on | run"
disable-model-invocation: true
---

Argument: `$ARGUMENTS`

The mode lives in one line of `CLAUDE.local.md` in the project root: `Tests: <mode>`.

- `off`, `ask`, `on`: write this line. Replace an old `Tests:` line if it exists.
  Create the file if it does not exist. In a git repo, if git does not ignore
  `CLAUDE.local.md`, add it to `.git/info/exclude`. Reply with one line: the new mode.
  `off` and `ask` silence the Stop hook `check-tests.sh`. `scripts/finish.sh` still runs
  `make check` before a PR.
- `run`: do not change the mode. Run `make check` once. Report pass/fail counts
  and each failure in one line. Do not fix failures unless I ask.
- No argument: reply with the current mode. No line means `on`.
