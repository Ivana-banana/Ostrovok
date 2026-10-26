# GIT.md — правила ведения Git

Цель: `main` в любой момент — рабочий кандидат на сдачу (`make check` зелёный, демо
открывается), а сданную версию можно восстановить по коммиту.

Правило: **закончил одно проверенное действие — закоммить и запушь ветку.**

## Путь задачи

```
задача #N → worktree .claude/worktrees/<N>-<тема> на ветке worktree-<N>-<тема>
          → коммиты своих путей → bash scripts/finish.sh → PR «Closes #N» → merge → уборка
```

```bash
bash scripts/issues.sh take <N>                       # сначала задача
git fetch --prune
git worktree add .claude/worktrees/<N>-<тема> -b worktree-<N>-<тема> origin/main
cp .env .claude/worktrees/<N>-<тема>/ 2>/dev/null     # claude -w копирует сам (.worktreeinclude)
cd .claude/worktrees/<N>-<тема> && make setup
# или сразу сессией Claude Code: claude -w <N>-<тема>
```

`<тема>` — 2–3 слова латиницей: `12-baseline-eval`. Работы без задачи нет: ни строки в
документе, ни срочной починки `main`. Для починки сначала задача с меткой `blocker` —
это минута.

## Где правило проверяется

| Где | Что | Для кого |
|---|---|---|
| `.claude/hooks/check-issue.sh` (PreToolUse) | правка файла, `git commit`, `git push` — только в worktree взятой открытой задачи | Claude Code и субагенты |
| `.githooks/pre-commit`, `pre-push` | то же + данные мимо LFS, секреты, новые md в «финале» | все: люди, Codex, IDE (`make setup` подключает) |
| `scripts/finish.sh` | ветка `worktree-<N>-…`, свои коммиты, проверки по режиму, вердикт `reviewer` | тот, кто вливает |
| `bash scripts/issues.sh check [путь]` | то же вручную | перед началом |

Сверка с GitHub кешируется на 30 минут. GitHub недоступен — пропуск с предупреждением:
номер в ветке уже есть. Хуки не выключаем (`--no-verify` — только по явной просьбе
человека). Белый список: `.arch/`, `_arch/` (рабочие папки планировщика), scratchpad
агента, ветки `arch/<run>` (team.py).

## Автономия агента

Агент коммитит и пушит **свою ветку сам**, создаёт PR и вливает его через
`scripts/finish.sh`. Разрешения — `permissions.allow`, запреты — `permissions.deny` в
`.claude/settings.json`.

- В коммит — только текущий шаг: `git add <явные пути>`. Никогда `git add -A`,
  `git add .`, `git commit -a`: в дереве может лежать чужая работа.
- Запрещены: force-push, `rebase`, `--amend`, `reset --hard`, `clean`, push в `main`,
  squash/rebase-merge, `--admin`. Трейлеры соавторства ИИ (`Co-Authored-By`,
  «Generated with Claude Code») в коммиты и PR не добавляем: `attribution` пустая.
- Секреты, `.env`, `data/`, веса вне LFS не коммитятся (хуки `check-secrets.sh`,
  `check-data-files.sh`).

## Финиш

Одна команда из worktree: `bash scripts/finish.sh`.

1. `git fetch --prune && git merge origin/main`. Конфликт — стоп: разреши, закоммить,
   запусти снова.
2. Проверки по режиму: «сборка» — `make check`; «финал» — `make lint`.
3. `git push -u origin HEAD`, PR в `main` с телом `Closes #N`.
4. Нужен `reviewer` (сборка — всегда; финал — метка `contract`, diff > 300 строк или
   `--review`) и вердикта нет — код 3, PR ждёт. `reviewer` пишет в PR комментарий
   «Вердикт reviewer: merge <sha>». После вердикта своих новых коммитов быть не должно.
5. `gh pr merge --merge` (merge-коммит, без squash и rebase). Отказ — до трёх попыток.
6. Уборка: `git pull --ff-only` основной копии (если она чистая), `git worktree remove`,
   удаление ветки локально и на origin.

Без `gh`, без авторизации или без `origin` скрипт останавливается с понятной ошибкой.

## Уборка руками

Ветку или worktree удаляем, только если **оба** условия выполнены:

1. ветка влита: `git fetch --prune && git rev-list --count origin/main..<ветка>` → `0`;
2. ветка не открыта ни в одном worktree, кроме твоего, а в твоём пусто:
   `git -C <путь> status --porcelain` → пусто.

Чужой worktree с правками или ветку с невлитыми коммитами не трогаем никогда — даже
если она выглядит брошенной. Брошенную задачу перехватывает `issues.sh take`.

## Задачи — GitHub Issues

```bash
bash scripts/issues.sh free [метка]          # свободные
bash scripts/issues.sh view <N>              # условия и приёмка
bash scripts/issues.sh take <N> [--human]    # взять (правила — CLAUDE.md, «Как работаем»)
bash scripts/issues.sh mine                  # мои
bash scripts/issues.sh board                 # сводка: blocker, мои, занятые area:, перехват
bash scripts/issues.sh note <N> "<текст>"    # где остановился, вопрос соседу
bash scripts/issues.sh create "<заголовок>" "area:api,P1" <файл по шаблону>
bash scripts/issues.sh bootstrap             # час 0: метки и две первые задачи
```

Метки: `area:<область>` — из BUILD-PLAN; `P0`/`P1`/`P2` (P2 — ниже cut line);
`blocker`; `contract`. Шаблон описания — `.github/ISSUE_TEMPLATE/task.md`: цель →
критерий условий → приёмка (команда) → «Зависит от #…» → сложность и модель.

**Перехват.** Движение задачи — самое позднее из: обновление задачи, обновление её PR,
коммит в ветке `worktree-<N>-*` на origin. Без движения дольше `TAKEOVER_MIN` минут
(`.env`: 45 для хакатона ≤ 2 дней, 120 для длинных) `take` перехватывает задачу и
пишет в ней «Перехвачена … Ветка: …». Ветка есть — продолжаем её, чужие коммиты не
переписываем. Движение неизвестно (GitHub не ответил) — не перехватываем, кроме
`--human`.

## Перед началом работы

```bash
git fetch --prune && git status -sb
git log --oneline HEAD..origin/main      # что пришло от команды
bash scripts/issues.sh board
```

## Коммиты

Формат: `<area>: <что закончено>`, на русском. Один коммит — один осмысленный шаг.

## Сдача и тег

После `submission-checklist` на коммите из `origin/main`:

```bash
git fetch --prune
git tag -a submit-final origin/main -m "Сдача: <коммит>, демо пройдено <дата>"
git push origin submit-final
```

## Безопасный откат

| Ситуация | Действие |
|---|---|
| Вернуть свой файл | `git restore <путь>` после просмотра diff |
| Отменить влитый коммит | `git revert <hash>` в ветке задачи → PR |
| Посмотреть старое состояние | `git switch --detach <тег или hash>` |
