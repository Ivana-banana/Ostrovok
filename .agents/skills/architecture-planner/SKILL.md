---
name: "architecture-planner"
description: "Plans the architecture of a system or AI agent from a folder containing the task description, research notes and supporting material. It first probes the real data and sources and builds a domain primer, clarifies task conditions with the user via /grilling (each question with a deep recommendation), has an independent agent write test scenarios with a sealed holdout, generates up to 15 very different architectures in a blind and an informed wave, searches prior art and mines craft from similar projects, adds craft and safety gates to every candidate, researches in value-of-information order, scores options with an anonymised critic panel and a scoring script with sensitivity analysis and scale-drift control, tests a hybrid against a patched leader, prototypes the leader, P+ and the simple baseline under the judges' conditions, and delivers the best options, an ADR, a build plan and a stage-impact log; live progress, express, autopilot and team modes.…"
---
<!-- Сгенерировано scripts/codex-sync.py из .claude/skills/architecture-planner/SKILL.md — не править руками. -->

# $architecture-planner (Codex)

Скилл общий с Claude Code. Полный текст — `.claude/skills/architecture-planner/SKILL.md` (путь от корня репозитория).
Прочитай его целиком и выполняй. Относительные пути в нём (`references/`, `scripts/`,
файлы рядом) — от папки `.claude/skills/architecture-planner/`.

Перевод на Codex — `AGENTS.md`, раздел «Claude Code → Codex»:

- `Agent(subagent_type: "X", prompt: …)` — `spawn_agent` с `agent_type: "X"`
  (`.codex/agents/X.toml`) и этим брифом; `SendMessage` — `followup_task` тому же агенту.
- `/скилл` — `$скилл`; Read, Grep, Glob — shell и `rg`; Edit, Write — apply_patch.
