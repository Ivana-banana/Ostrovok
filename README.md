# hackathon-template

Шаблон, с которого команда начинает каждый хакатон. Предметного кода нет: только
процесс, скиллы, агенты, хуки и заготовки стека. Заменяет промпт-каркас, который мы
четыре раза писали заново. Что откуда взято — `CHANGELOG.md`.

## Как используется на хакатоне

```
ДО ХАКАТОНА: «Use this template» → новый репозиторий.

УСЛОВИЯ ПРИШЛИ (час 0):
  1a. deep research в ChatGPT и Claude Research (скилл deep-research-prompt
      генерирует промпт) — параллельно, его НЕ ждём;
  1b. make setup PRESET=<имя>, окружение, загрузка данных — то, что не зависит
      от архитектуры (2 человека).
  2.  /architecture-planner --input <условия> --express yes --team 2–3
      (2–3 человека; полный режим — если условия известны за дни).
      Ответ deep research пришёл до этапа research планировщика — кладём в папку
      как research; позже — в питч и jury.
  3.  /hackathon-kickoff — мост: выход планировщика → репозиторий и Issues.
СБОРКА: issues.sh take → worktree → builder → reviewer → finish.sh;
  вехи M0 skeleton → M1 → M2 → заморозка (cut line из BUILD-PLAN);
  jury перед каждой вехой; Режим: финал — на заморозке.
ПОСЛЕ ЗАЩИТЫ: /retro → PR в hackathon-template.
```

## Запуск за 5 минут

Нужны: `git`, `gh` (авторизован: `gh auth login`), `jq`, `make`, `python3`; по пресету —
`uv`, `node` 22+, `docker`, `git-lfs`.

```bash
gh repo create <org>/<хакатон> --template <owner>/hackathon-template --private --clone
cd <хакатон>
bash scripts/issues.sh bootstrap        # метки и две первые задачи: «Окружение и пресет», «Kickoff»
bash scripts/issues.sh take 1
git worktree add .claude/worktrees/1-setup -b worktree-1-setup origin/main
cd .claude/worktrees/1-setup
make setup PRESET=python-ml             # или web-fullstack, ros2; подключает .githooks, создаёт .env
make check                              # зелёный
git add <пути> && git commit -m "infra: пресет python-ml"
bash scripts/finish.sh                  # merge main → проверки → PR → вердикт reviewer → merge → уборка
```

Условия хакатона — в `task/`. Планировщик: `/architecture-planner --input task --express yes`.
Затем `/hackathon-kickoff` в задаче «Kickoff».

## Что внутри

| Часть | Где | Что делает |
|---|---|---|
| Правила | `CLAUDE.md` (≤ 200 строк, места `[ИЗ БРИФА]`), `GIT.md` | задача, критерии, ловушки; путь задачи; режимы «сборка» / «финал» |
| Задачи | `scripts/issues.sh` | GitHub Issues через `gh`: `create`, `take`, `mine`, `free`, `board`, `check`, `note`; мягкая `area:`, перехват по `TAKEOVER_MIN` |
| Финиш | `scripts/finish.sh` | merge `origin/main` → проверки по режиму → PR → вердикт `reviewer` → merge → уборка |
| Интерфейс | `Makefile` + `stack.mk` пресета | `setup`, `lint`, `check`, `demo`, `eval`, `eval-final`, `e2e` |
| Печать holdout | `scripts/eval-final.sh` | `eval-final`: только «финал», чистое дерево, sha256, журнал |
| Хуки Claude | `.claude/hooks/`, `.claude/settings.json` | задача взята; holdout и новые md; секреты; данные мимо LFS; тесты по режиму под общим замком (`scripts/heavy.sh`); чужие коммиты; правка конфига линтера — вопрос; контекст сессии. Модель — `opus`, effort `high` |
| Хуки git | `.githooks/` | то же для людей, Codex, IDE; цепочка в Git LFS |
| Агенты | `.claude/agents/` | `builder`, `reviewer`, `evaluator`, `operator`, `jury`, `critic`, `silent-failure-hunter`, `scribe`, `pitch` (`docs/agents.md`); определения на английском, отчёт — по-русски |
| Скиллы | `.claude/skills/` | `architecture-planner` (из zip, без правок), `hackathon-kickoff`, `orchestrate` (с человеком — по-русски, промпты субагентам — на английском), `tests`, `submission-checklist`, `deep-research-prompt`, `retro` |
| Codex | `AGENTS.md`, `.codex/agents/`, `.codex/hooks.json`, `.agents/skills/` | те же правила, роли, хуки и скиллы для Codex; `.codex/agents/` и `.agents/skills/` генерирует `make codex-sync` из `.claude/` |
| CI | `.github/workflows/ci.yml` | только вручную (`workflow_dispatch`); обязательная проверка — `make check` в `finish.sh` |
| Пресеты | `presets/` | `python-ml`, `web-fullstack`, `ros2` |
| Заготовки | `docs/`, `research/`, `pitch/`, `task/`, `data/` | вехи, решения, вопросы организаторам, вопросы жюри |

## Режимы

Строка `Режим: сборка | финал` в `CLAUDE.md`. Её читают хуки, `finish.sh` и агенты;
источник правды — `CLAUDE.md` в `origin/main`. Переключает человек одним коммитом на
вехе заморозки. Таблица режимов — `CLAUDE.md`, «Режимы».

## Стек с нуля

Пресет не подходит — свой `stack.mk` в корне: `STACK := <имя>` и цели `stack-setup`,
`stack-lint`, `stack-check`, `stack-demo`, `stack-eval`, `stack-eval-final` (получает
`HOLDOUT_DIR`, печатает строку `RESULT: …`), `stack-e2e`. Для Python —
`scripts/eval_harness.py` (сценарии `eval/cases.json` → проверки `check_<id>`).

## Проверка самого шаблона

```bash
make template-test     # = hooks-test + kickoff-test + codex-test
```

Без `stack.mk` (сам шаблон) `make check` запускает `template-test`. Правки шаблона —
через `/retro` и PR с записью в `CHANGELOG.md`.
