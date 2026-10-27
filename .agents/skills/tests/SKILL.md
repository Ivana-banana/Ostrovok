---
name: "tests"
description: "Run the tests now and report the result."
---
<!-- Сгенерировано scripts/codex-sync.py из .claude/skills/tests/SKILL.md — не править руками. -->

# $tests (Codex)

Скилл общий с Claude Code. Полный текст — `.claude/skills/tests/SKILL.md` (путь от корня репозитория).
Прочитай его целиком и выполняй. Относительные пути в нём (`references/`, `scripts/`,
файлы рядом) — от папки `.claude/skills/tests/`.

Перевод на Codex — `AGENTS.md`, раздел «Claude Code → Codex»:

- `Agent(subagent_type: "X", prompt: …)` — `spawn_agent` с `agent_type: "X"`
  (`.codex/agents/X.toml`) и этим брифом; `SendMessage` — `followup_task` тому же агенту.
- `/скилл` — `$скилл`; Read, Grep, Glob — shell и `rg`; Edit, Write — apply_patch.
