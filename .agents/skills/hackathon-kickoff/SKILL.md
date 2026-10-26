---
name: "hackathon-kickoff"
description: "Мост между architecture-planner и сборкой — переносит рабочую папку планировщика (_arch/ или .arch/<run>/) в репозиторий — бриф в CLAUDE.md, ловушки в тесты, сценарии в eval и e2e, holdout в запечатанный eval-final, ADR в docs/decisions.md и пресет, BUILD-PLAN в Issues и docs/timeline.md, вопросы жюри в pitch/QA.md. Issues заводит только после подтверждения плана человеком. Использовать командой /hackathon-kickoff после этапа decide планировщика, или когда просят «перенеси план в репозиторий», «заведи задачи по плану»."
---
<!-- Сгенерировано scripts/codex-sync.py из .claude/skills/hackathon-kickoff/SKILL.md — не править руками. -->

# $hackathon-kickoff (Codex)

Скилл общий с Claude Code. Полный текст — `.claude/skills/hackathon-kickoff/SKILL.md` (путь от корня репозитория).
Прочитай его целиком и выполняй. Относительные пути в нём (`references/`, `scripts/`,
файлы рядом) — от папки `.claude/skills/hackathon-kickoff/`.

Перевод на Codex — `AGENTS.md`, раздел «Claude Code → Codex»:

- `Agent(subagent_type: "X", prompt: …)` — `spawn_agent` с `agent_type: "X"`
  (`.codex/agents/X.toml`) и этим брифом; `SendMessage` — `followup_task` тому же агенту.
- `/скилл` — `$скилл`; Read, Grep, Glob — shell и `rg`; Edit, Write — apply_patch.
