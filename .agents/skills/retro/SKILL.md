---
name: "retro"
description: "Ретро после защиты, 30 минут — читает STAGE-IMPACT.md планировщика, docs/decisions.md, журнал отказов хуков, закрытые blocker; каждый урок — одна строка «правило → почему → откуда»; готовит PR в hackathon-template с записью в его CHANGELOG.md. Использовать командой /retro после защиты."
---
<!-- Сгенерировано scripts/codex-sync.py из .claude/skills/retro/SKILL.md — не править руками. -->

# $retro (Codex)

Скилл общий с Claude Code. Полный текст — `.claude/skills/retro/SKILL.md` (путь от корня репозитория).
Прочитай его целиком и выполняй. Относительные пути в нём (`references/`, `scripts/`,
файлы рядом) — от папки `.claude/skills/retro/`.

Перевод на Codex — `AGENTS.md`, раздел «Claude Code → Codex»:

- `Agent(subagent_type: "X", prompt: …)` — `spawn_agent` с `agent_type: "X"`
  (`.codex/agents/X.toml`) и этим брифом; `SendMessage` — `followup_task` тому же агенту.
- `/скилл` — `$скилл`; Read, Grep, Glob — shell и `rg`; Edit, Write — apply_patch.
