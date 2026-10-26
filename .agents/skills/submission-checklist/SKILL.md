---
name: "submission-checklist"
description: "Проверка комплекта сдачи хакатона перед вехой, заморозкой и тегом сдачи — цели make, чистый клон, секреты, заглушки в демо-пути, критерии из брифа, формат сдачи из брифа. Каждый пункт — командой или файлом. Использовать, когда просят «проверь, готовы ли к сдаче», перед вехой и перед тегом submit-final."
---
<!-- Сгенерировано scripts/codex-sync.py из .claude/skills/submission-checklist/SKILL.md — не править руками. -->

# $submission-checklist (Codex)

Скилл общий с Claude Code. Полный текст — `.claude/skills/submission-checklist/SKILL.md` (путь от корня репозитория).
Прочитай его целиком и выполняй. Относительные пути в нём (`references/`, `scripts/`,
файлы рядом) — от папки `.claude/skills/submission-checklist/`.

Перевод на Codex — `AGENTS.md`, раздел «Claude Code → Codex»:

- `Agent(subagent_type: "X", prompt: …)` — `spawn_agent` с `agent_type: "X"`
  (`.codex/agents/X.toml`) и этим брифом; `SendMessage` — `followup_task` тому же агенту.
- `/скилл` — `$скилл`; Read, Grep, Glob — shell и `rg`; Edit, Write — apply_patch.
