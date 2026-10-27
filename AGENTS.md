# AGENTS.md — правила для Codex

Ты — Codex одного из равноправных участников команды. В команде работают Claude Code и
Codex, правила у всех одни. Главный документ — `CLAUDE.md`: бриф, режим, как работаем,
проверка, документы, код. Прочитай его целиком в начале сессии и после каждого merge
`origin/main`. Здесь — что у Codex устроено иначе и правила, которые нельзя пропустить.

Пиши на ~80% ASD-STE100: короткие предложения, одно действие — одно предложение,
один термин — одно значение. Так же пиши отчёты, комментарии в issue и описания PR.

## Начало сессии

1. Прочитай `CLAUDE.md`, `GIT.md`, `docs/agents.md`, `docs/timeline.md`, условия в `task/`.
   Режим правил — строка «Режим:» в `CLAUDE.md` (`bash scripts/mode.sh`).
2. Контекст: `bash .claude/hooks/session-context.sh` — ветка, режим, ближайшая веха, мои
   задачи, занятые `area:`, blocker. С включёнными хуками он приходит сам.
3. Работай по скиллу `$orchestrate`: свои задачи → worktree → `builder` → `reviewer` →
   `scripts/finish.sh`. Номер задачи и план у человека не спрашиваешь.

## Правила, которые нельзя пропустить

Это дубль `CLAUDE.md`. Расходятся — прав `CLAUDE.md`.

- Одна задача = одна issue = один worktree = одна ветка `worktree-<N>-<тема>` = один PR
  с `Closes #N`. Без взятой задачи файлы не меняешь и не коммитишь, даже одну строку.
- Взять: `bash scripts/issues.sh free`, затем `take <N>`. Подходящей нет — `create`, затем
  `take`. Человек сказал «возьми #N» — `take <N> --human`.
- Worktree: `git worktree add .claude/worktrees/<N>-<тема> -b worktree-<N>-<тема> origin/main`.
  Основная копия на `main` — только для чтения. Чужие worktree и ветки не трогаешь.
- Коммит — после каждого проверенного шага, `git add <явные пути>`.
- Финиш: `bash scripts/finish.sh` из worktree. Код 3 — нужен вердикт `reviewer`: запусти
  агента `reviewer`, он пишет в PR строку `Вердикт reviewer: merge <sha>`, затем снова
  `finish.sh`. Вливаешь по одному.
- Память — в issue, не в голове: `bash scripts/issues.sh note <N> "сделано / дальше / блокер"`
  на каждой вехе, перед сжатием контекста, при смене задачи и в конце сессии.
- Риск показа или сдачи — сразу задача с меткой `blocker`. `main` всегда зелёный.
- Запрещено: force-push, `rebase`, `--amend`, `git add -A` / `.` / `-u`, `commit -a`,
  `--no-verify`, push в `main`, `reset --hard`, `git clean`,
  `gh pr merge --squash | --rebase | --admin`, трейлеры соавторства ИИ в коммитах и PR.
- Holdout (`eval/holdout*`) не открываешь никаким способом. Работаешь по `eval/cases*`.
- Секреты — только в `.env`. Данные — вне git (`data/`): сырые данные коллектора — в GitHub Releases, агрегаты без названий — в git (`backend/src/api/`); примеры — `tests/fixtures/`.
- «Работает» пишешь только с командой и её выводом. Цели `make` — единственный интерфейс.
- Человеку пишешь только по списку `CLAUDE.md`, «Автономия». На вехе — статус в 1–3 строки,
  ответа не ждёшь.

## Claude Code → Codex

`CLAUDE.md`, роли `.claude/agents/` и скиллы `.claude/skills/` написаны для Claude Code.
Перевод:

| В тексте | В Codex |
|---|---|
| `Agent(subagent_type: "builder", prompt: <бриф>)` | `spawn_agent` с `agent_type: "builder"` (`.codex/agents/builder.toml`) и этим брифом |
| `SendMessage` тому же агенту | `followup_task` тому же агенту; дождаться — `wait_agent` |
| `Monitor` с циклом `while true` | один проход тела цикла без `while` и `sleep`: на синхронизации |
| `model: "opus"`, сложность «высокая» | агенту — усилие рассуждения `high` или выше |
| скилл `/orchestrate` | `$orchestrate`; обёртки — `.agents/skills/`, полный текст — `.claude/skills/` |
| Read, Grep, Glob | shell: `sed -n`, `rg`, `ls` |
| Edit, Write | `apply_patch` |
| хуки `.claude/settings.json` | `.codex/hooks.json` — те же скрипты `.claude/hooks/` |
| `permissions.deny` | хук `.claude/hooks/check-git.sh` и список «Запрещено» выше |
| пустая `attribution` | без `Co-authored-by` и подписей ИИ |
| `claude -w <N>-<тема>` | `git worktree add …` руками, затем работа в этой папке |
| scratchpad агента | `/tmp`; не файлы репозитория |

Роли — `docs/agents.md`: `builder`, `reviewer`, `evaluator`, `operator`, `jury`, `critic`,
`silent-failure-hunter`, `scribe`, `pitch`. Промпт агенту — на английском, отчёт человеку —
на русском. Агента запускаешь, когда этого требует скилл или правило: `builder` на
задачу, `reviewer` на PR, свежий скептик на своё решение, `jury` как арбитр после двух
отказов. Модель и усилие агенту задаёт твоя сессия: в `.codex/agents/` их нет.

## Хуки Codex — включить один раз

`.codex/hooks.json` вызывает скрипты `.claude/hooks/`: `CLAUDE.md` в контекст, задача
взята, holdout, новые md в «финале», запрещённые команды git, данные мимо LFS, секреты,
чужие коммиты в `main`. Codex запускает хуки проекта только
после доверия:

1. Папка проекта — доверенная (Codex спросит при первом запуске).
2. В сессии: `/hooks` → просмотри список → trust. После правки `.codex/hooks.json` — снова.

Хуки не включены — те же главные проверки делают `.githooks/` на коммите и push (их ставит
`make setup`). Правила выше ты выполняешь сам в любом случае. `make check` гоняет
`finish.sh` перед PR.

## Правишь роли или скиллы

Источник — `.claude/agents/*.md` и `.claude/skills/*/SKILL.md`. После правки:
`python3 scripts/codex-sync.py` и сгенерированные `.codex/agents/`, `.agents/skills/` — в тот
же коммит. Руками их не правишь. Расхождение ловят `make codex-test` и `.githooks/pre-commit`.
