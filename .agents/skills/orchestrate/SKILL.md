---
name: "orchestrate"
description: "Личный протокол участника на сборке — свои задачи из GitHub Issues → worktree на каждую → параллельные builder → reviewer → scripts/finish.sh по одному. Общего оркестратора нет: каждый участник ведёт свои задачи. Использовать командой /orchestrate в начале рабочего окна и когда просят «возьми следующие задачи», «доведи до main»."
---
<!-- Сгенерировано scripts/codex-sync.py из .claude/skills/orchestrate/SKILL.md — не править руками. -->

# $orchestrate (Codex)

Скилл общий с Claude Code. Полный текст — `.claude/skills/orchestrate/SKILL.md` (путь от корня репозитория).
Прочитай его целиком и выполняй. Относительные пути в нём (`references/`, `scripts/`,
файлы рядом) — от папки `.claude/skills/orchestrate/`.

Перевод на Codex — `AGENTS.md`, раздел «Claude Code → Codex»:

- `Agent(subagent_type: "X", prompt: …)` — `spawn_agent` с `agent_type: "X"`
  (`.codex/agents/X.toml`) и этим брифом; `SendMessage` — `followup_task` тому же агенту.
- `/скилл` — `$скилл`; Read, Grep, Glob — shell и `rg`; Edit, Write — apply_patch.
