---
name: retro
description: Ретро после защиты, 30 минут — читает STAGE-IMPACT.md планировщика, docs/decisions.md, журнал отказов хуков, закрытые blocker; каждый урок — одна строка «правило → почему → откуда»; готовит PR в hackathon-template с записью в его CHANGELOG.md. Использовать командой /retro после защиты.
---

# /retro — уроки в шаблон

30 минут. Цель — не отчёт, а **правки шаблона**: правило, хук, агент, скилл, пресет.
Отчёт в md не пишем: уроки идут в PR шаблона.

## 1. Факты (10 минут)

```bash
cat <папка планировщика>/STAGE-IMPACT.md            # какие этапы планировщика окупились
cat docs/decisions.md                               # что решали и когда
cat "$(git rev-parse --git-common-dir)/hook-refusals.log" | cut -f2 | sort | uniq -c | sort -rn
cut -f2,4 "$(git rev-parse --git-common-dir)/hook-refusals.log" | tail -40
gh issue list --state closed --label blocker --limit 50 --json number,title,closedAt
gh issue list --state all --limit 200 --json number,title,comments \
  --jq '.[] | select(any(.comments[]; .body | test("Перехвачена"))) | "#\(.number) \(.title)"'
cat eval/final-log.md; git log --oneline | wc -l
```

Журнал отказов — на машине каждого участника. Попроси команду прислать вывод
`cut -f2,4 "$(git rev-parse --git-common-dir)/hook-refusals.log"`.
Спроси у людей одной фразой: что сломалось на защите; какое правило мешало; чего не
хватило в час 0.

## 2. Уроки (10 минут)

Каждый урок — **одна строка**:

```
<правило, как оно будет в шаблоне> → <почему: что случилось, с числом> → <откуда: файл, #N, D-NNN, журнал хуков>
```

- Только то, что меняет шаблон. «Надо лучше планировать» — не урок.
- Хук срабатывал часто по делу — правило работает, урока нет. Хук срабатывал зря —
  урок: сузить хук. Этап планировщика ничего не изменил — урок для `--express`.
- 3–10 уроков. Больше — выбери по цене ошибки.

## 3. PR в hackathon-template (10 минут)

Адрес шаблона — `TEMPLATE_REPO` в `.env` (например `owner/hackathon-template`).

```bash
d="$(mktemp -d)" && gh repo clone "$TEMPLATE_REPO" "$d" && cd "$d" && make setup
bash scripts/issues.sh create "Ретро: <хакатон>" "area:infra,P1" - <<< "<уроки>"
bash scripts/issues.sh take <N>
git worktree add .claude/worktrees/<N>-retro -b worktree-<N>-retro origin/main
```

В worktree шаблона:

1. Правки по урокам: `CLAUDE.md`, `GIT.md`, хуки (+ тест в `test-hooks.sh`), агенты,
   скиллы, пресеты. Предметного кода хакатона в шаблон не переносим.
2. `CHANGELOG.md` шаблона — запись сверху:

   ```
   ## <дата> — <хакатон>
   - <правило> → <почему> → <откуда>
   ```

3. `make template-test` — зелёный.
4. Коммит, push, PR `Closes #N` (`bash scripts/finish.sh` остановится на коде 3 —
   PR ждёт ревью владельца шаблона). Merge делает владелец шаблона.

## Отчёт в чат

Уроки (строки), ссылка на PR, что не вошло и почему.
