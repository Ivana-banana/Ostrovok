---
name: orchestrate
description: Личный протокол участника на сборке — свои задачи из GitHub Issues → worktree на каждую → параллельные builder → reviewer → scripts/finish.sh по одному. Общего оркестратора нет: каждый участник ведёт свои задачи. Использовать командой /orchestrate в начале рабочего окна и когда просят «возьми следующие задачи», «доведи до main».
---

# /orchestrate — мои задачи до main

Ты — главная сессия одного участника. Сидишь в основной копии на `main`, файлы в ней
не правишь. Работа: взять задачи, открыть worktree, написать брифы, вести `builder` →
`reviewer` → `finish.sh`. Роли — `docs/agents.md`. Режим — `bash scripts/mode.sh`.

## Язык

- С человеком — по-русски: блоки «ЗАДАЧА / СДЕЛАНО», вопросы, отчёт окна.
- Промпт субагенту (`Agent`, `Workflow`, `SendMessage`) — на английском, как и
  определения в `.claude/agents/`. Названия задач, цитаты из задачи и строки-маркеры
  (`Вердикт reviewer: merge`, значения режима) не переводи.
- Субагент думает и работает на английском. По-русски пишет то, что читают люди:
  отчёт, коммиты, тексты интерфейса, документы. Это сказано в его определении
  (`.claude/agents/`).

## 1. Синхронизация (начало окна и после каждого merge)

```bash
git fetch --prune && git status -sb && git pull --ff-only   # pull — только при чистом status
git worktree list                                           # чужие worktree не трогать
bash scripts/issues.sh board                                # blocker, мои, занятые area:, перехват
```

Открытый `blocker` — раньше своих задач. Ближайшая веха — в выводе хука начала сессии.

## 2. Выбор

- Сначала критический путь вехи (`docs/timeline.md`), потом ширина. P2 — только
  после вехи M1.
- Задачу бери, если её «Зависит от #…» влиты в `origin/main`. Иначе — на fixtures, с
  пометкой в брифе.
- `bash scripts/issues.sh take <N>`. Предупреждение о занятой `area:` — договорись в
  задаче (`issues.sh note`), разведи файлы. Человек сказал «возьми #N» —
  `take <N> --human`.
- Параллельно — до трёх `builder` с непересекающимися путями.
- Печатай блок без ожидания ответа:
  `ЗАДАЧА #N — <название>; ветка worktree-<N>-<тема>; почему сейчас: <веха / blocker>`.

## 3. Worktree

```bash
git worktree add .claude/worktrees/<N>-<тема> -b worktree-<N>-<тема> origin/main
# перехват: git fetch origin <ветка> && git worktree add .claude/worktrees/<N>-<тема> -b <ветка> origin/<ветка>
cp .env .claude/worktrees/<N>-<тема>/ 2>/dev/null; (cd .claude/worktrees/<N>-<тема> && make setup)
```

`isolation: worktree` у Agent не используй: свои имена веток, нет `.env`.

## 4. Бриф builder

```
Task: #<N> — <title>; area:<…>; complexity <…>
Worktree: <absolute path>; branch: worktree-<N>-<topic>
Acceptance: bash scripts/issues.sh view <N>; scenarios: S…
Allowed paths: <…>; forbidden: contracts, lockfile, other area:
Base in main: <modules>; accept on: real module | fixtures
```

`Agent(subagent_type: "builder", prompt: <бриф>)`, в фоне. Сложность «высокая» —
`model: "opus"`. Пока builder работает, его файлы не читай и не правь.

## 5. Ревью и финиш

- Нужен ли `reviewer`: «сборка» — всегда; «финал» — метка `contract`, формат данных,
  diff > 300 строк. `finish.sh` вернул код 3 — значит нужен.
- `Agent(subagent_type: "reviewer", prompt: ветка, worktree, #N, PR, отчёт builder)`.
  - `merge` — reviewer сам пишет вердикт в PR → `SendMessage` тому же builder:
    "run `bash scripts/finish.sh`".
  - `доработать` — `SendMessage` builder с блокирующими пунктами, снова reviewer.
    Два отказа подряд — арбитр `jury` (режим «арбитр»).
  - `человеку` — человеку с цитатой; задача стоит.
- Вливай **по одному**: следующий `finish.sh` — после завершения предыдущего.
- Печатай: `СДЕЛАНО #N — PR #k влит; что изменилось для пользователя`.

## 6. Свои решения

Решение вне задачи (формат, порог, библиотека) — проверь свежим скептиком
(`reviewer`, бриф "find why this decision is wrong"). Два отказа — `jury`-арбитр.
Человеку — только список из `CLAUDE.md`, «Автономия».

## 7. Веха

Перед вехой: `evaluator` (`make eval`, было → стало) → `operator` (роль из брифа) →
`jury` → скилл `submission-checklist`. Находки — новые задачи (`issues.sh create`),
не строки в документах. На заморозке — человек переключает режим; `evaluator` делает
`make eval-final`; `scribe` и `pitch` — README и питч.

## Эскалация человеку

Контракт вне плана, новая зависимость, сокращение объёма условий, чужой worktree с
правками на пути задачи, `check-upstream.sh` против задачи, внешнее действие.

## Конец окна

До 10 строк: что влито (#N → PR), что застряло и почему, какие вопросы ждут человека.
