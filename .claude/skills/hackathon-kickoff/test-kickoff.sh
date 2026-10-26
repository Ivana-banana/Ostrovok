#!/usr/bin/env bash
# Сухой прогон /hackathon-kickoff на tests/fixtures/_arch во временном клоне шаблона:
# CLAUDE.md заполнен, eval-final запечатан, план задач выведен, Issues не заведены.
#   bash .claude/skills/hackathon-kickoff/test-kickoff.sh
set -uo pipefail
SRC="$(cd "$(dirname "$0")/../../.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
FAIL=0
ok()  { echo "ok:   $1"; }
bad() { echo "FAIL: $1"; FAIL=1; }

# поддельный gh: любой вызов пишется в журнал — Issues заводить нельзя
mkdir -p "$TMP/bin" && printf '#!/bin/sh\necho "$*" >> "%s/gh.log"\n' "$TMP" > "$TMP/bin/gh" && chmod +x "$TMP/bin/gh"
export PATH="$TMP/bin:$PATH"

R="$TMP/repo"
mkdir -p "$R" && (cd "$SRC" && tar --exclude=.git --exclude=node_modules --exclude=.venv \
  --exclude=.claude/worktrees --exclude=stack.mk --exclude=__pycache__ -cf - .) | tar -xf - -C "$R"
# заполненный репозиторий → шаблон: decisions.md из коммита, который его добавил (импорт шаблона;
# корневой коммит может быть пустым «Initial commit» GitHub), кода стека и
# тестов ловушек нет (check-traps считает тестом любое упоминание id в tests/src/backend/web)
git -C "$SRC" show "$(git -C "$SRC" log --diff-filter=A --format=%H -- docs/decisions.md | tail -1)":docs/decisions.md > "$R/docs/decisions.md"
rm -rf "$R/src" "$R/backend" "$R/web"
(cd "$R" && grep -rlswZ --exclude-dir=fixtures -e 'TRAP-[0-9]\+' tests | xargs -0r rm -f)
cd "$R" && git init -q -b main && git add -A && git -c user.name=t -c user.email=t@t commit -qm template
cp -r tests/fixtures/_arch "$TMP/_arch"

out="$(python3 .claude/skills/hackathon-kickoff/kickoff.py apply --arch "$TMP/_arch" 2>&1)"; code=$?
[ $code = 0 ] && ok "apply: код 0" || { bad "apply: код $code"; echo "$out"; }
echo "$out" | sed -n '/^## План задач/,/^План:/p' | sed 's/^/      /'

block() { sed -n "/<!-- kickoff:$1 -->/,/<!-- \/kickoff:$1 -->/p" CLAUDE.md; }
for b in task criteria constraints traps jury-run stack; do
  block "$b" | grep -q '\[ИЗ БРИФА\]' && bad "CLAUDE.md: блок $b не заполнен" || ok "CLAUDE.md: блок $b заполнен"
done
block criteria | grep -q '| Качество классификации (macro-F1, recall срочных) | 35% |' && ok "критерии с весами" || bad "критерии"
block task | grep -q 'Пользователь: диспетчер аварийно-диспетчерской службы' && ok "пользователь из брифа" || bad "пользователь"
block traps | grep -q 'TRAP-3' && ok "ловушки → CLAUDE.md" || bad "ловушки"
grep -q '^Режим: сборка' CLAUDE.md && ok "строка «Режим: сборка» на месте" || bad "строка режима"
[ "$(wc -l < CLAUDE.md)" -le 200 ] && ok "CLAUDE.md ≤ 200 строк ($(wc -l < CLAUDE.md))" || bad "CLAUDE.md > 200 строк"

v="$(bash scripts/eval-final.sh --verify 2>&1)"
printf '%s' "$v" | grep -q "печать цела" && ok "eval-final запечатан: $v" || bad "печать: $v"
git diff --cached --name-only | grep -q 'eval/holdout.sha256' && ok "печать проиндексирована (git add явных путей)" || bad "печать не в индексе"
tar -tzf eval/holdout.tar.gz | grep -q scenarios.md && ok "holdout внутри архива" || bad "архив"
v2="$(HACK_MODE=финал EVAL_FINAL_CONFIRM=0000000 bash scripts/eval-final.sh 2>&1)"
printf '%s' "$v2" | grep -q 'дерево не чистое\|назови замороженный коммит' && ok "eval-final без заморозки не вскрывается" || bad "eval-final вскрылся: $v2"
printf '%s' "$out" | grep -q 'S5\|Запах газа' && bad "holdout попал в вывод kickoff" || ok "holdout не напечатан в чат"
grep -q 'Запах газа' eval/cases.json && bad "holdout попал в видимые cases" || ok "видимые cases без holdout"
[ "$(jq length eval/cases.json)" = 4 ] && ok "видимых сценариев 4 → eval/cases.json" || bad "cases.json"
[ "$(jq '[.[] | select(.jury_click)] | length' eval/cases.json)" = 1 ] && ok "jury-click сценарий отмечен для e2e" || bad "jury-click"
[ "$(jq length eval/traps.json)" = 3 ] && ok "ловушек 3 → eval/traps.json" || bad "traps.json"
jq -e '.rule | test("0,04")' eval/baseline.json >/dev/null && ok "тривиальный ответ → eval/baseline.json" || bad "baseline"
grep -q '^- D-001' docs/decisions.md && ok "D-001 из ADR" || bad "D-001"
[ -f docs/adr/ADR-0001-tfidf-plus-rules.md ] && ok "ADR → docs/adr/" || bad "ADR"
block stack | grep -q 'Пресет: `web-fullstack`' && ok "пресет выбран по ADR" || bad "пресет"
grep -q '| M1 | 2026-10-21 14:00 |' docs/timeline.md && ok "вехи с датами → timeline" || bad "timeline"
grep -q 'Cut line: первым выбрасываем T5' docs/timeline.md && ok "cut line → timeline" || bad "cut line"
grep -q 'Почему не LLM' pitch/QA.md && ok "вопросы жюри → pitch/QA.md" || bad "QA"
grep -q 'аварийно-диспетчерская' research/domain.md && ok "00c-domain → research/domain.md" || bad "domain"
grep -q '\[ \] нет во входе: Справочника исполнителей' docs/questions-for-organizers.md && ok "пробел входа → вопросы организаторам" || bad "вопросы"
printf '%s' "$out" | grep -q '| T3 | TF-IDF + логрегрессия, eval против baseline | M1 | P0 | model | T1 |' && ok "план задач выведен в чат" || bad "план в чат"
printf '%s' "$out" | grep -q '| T5 | .* | P2 |' && ok "задача за cut line — P2" || bad "cut line → P2"
printf '%s' "$out" | grep -q 'высокая / opus' && ok "сложность «высокая» → opus" || bad "модель"
[ ! -s "$TMP/gh.log" ] && ok "apply не вызывал gh — Issues не заведены" || bad "apply вызывал gh: $(cat "$TMP/gh.log")"
python3 .claude/skills/hackathon-kickoff/kickoff.py issues --arch "$TMP/_arch" >/dev/null 2>&1
[ $? = 2 ] && [ ! -s "$TMP/gh.log" ] && ok "issues без --confirmed — отказ, gh не вызван" || bad "issues без подтверждения"
bash scripts/check-traps.sh >/dev/null 2>&1 && bad "check-traps: без тестов ловушек — зелёный" || ok "check-traps: нет тестов ловушек — красный"
mkdir -p tests && printf '# TRAP-1 TRAP-2 TRAP-3\n' > tests/test_traps.py
bash scripts/check-traps.sh >/dev/null 2>&1 && ok "check-traps: тесты с id — зелёный" || bad "check-traps с тестами"

ml="$(PYTHONDONTWRITEBYTECODE=1 python3 -c '
import sys; sys.path.insert(0, ".claude/skills/hackathon-kickoff")
from kickoff import parse_scenarios
t = "### S1 — Тест [typical]\nОжидаемое поведение: строка 1\nстрока 2\nКритерий прохождения:\n- (a) да;\n- (b) нет.\nВес: critical\n"
c = parse_scenarios(t)[0]
print(c["expected"] == "строка 1\nстрока 2" and "(b) нет" in c["pass"] and c["critical"])' 2>&1)"
[ "$ml" = True ] && ok "сценарий: многострочные «Ожидаемое» и «Критерий» разобраны" || bad "многострочные поля сценария: $ml"

make -s lint >/dev/null 2>&1 && ok "make lint без стека — зелёный" || bad "make lint без стека упал"
find . -name __pycache__ -not -path './.git/*' | grep -q . && ok "make lint создал __pycache__ (проверяем игнор, а не отсутствие)" || bad "make lint не создал __pycache__ — проверка игнора пустая"
dirty="$(git status --porcelain --untracked-files=all | grep '__pycache__' | head -3)"
[ -z "$dirty" ] && ok "после make lint нет неотслеживаемых __pycache__" || bad "__pycache__ в git status: $dirty"

[ $FAIL = 0 ] && echo "KICKOFF: ЗЕЛЁНЫЙ" || echo "KICKOFF: ЕСТЬ КРАСНЫЕ"
exit $FAIL
