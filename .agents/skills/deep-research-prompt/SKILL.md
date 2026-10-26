---
name: "deep-research-prompt"
description: "Из условий хакатона генерирует промпт для ChatGPT Deep Research и Claude Research — топ решений по баллам на оставшийся срок, что не делать, таблица первичных источников, вопросы жюри и вопросы организаторам, правила UNKNOWN/HYPOTHESIS. Использовать в час 0, когда пришли условия, параллельно с make setup и планировщиком; ответ не ждём."
---
<!-- Сгенерировано scripts/codex-sync.py из .claude/skills/deep-research-prompt/SKILL.md — не править руками. -->

# $deep-research-prompt (Codex)

Скилл общий с Claude Code. Полный текст — `.claude/skills/deep-research-prompt/SKILL.md` (путь от корня репозитория).
Прочитай его целиком и выполняй. Относительные пути в нём (`references/`, `scripts/`,
файлы рядом) — от папки `.claude/skills/deep-research-prompt/`.

Перевод на Codex — `AGENTS.md`, раздел «Claude Code → Codex»:

- `Agent(subagent_type: "X", prompt: …)` — `spawn_agent` с `agent_type: "X"`
  (`.codex/agents/X.toml`) и этим брифом; `SendMessage` — `followup_task` тому же агенту.
- `/скилл` — `$скилл`; Read, Grep, Glob — shell и `rg`; Edit, Write — apply_patch.
