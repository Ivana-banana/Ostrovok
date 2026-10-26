#!/usr/bin/env bash
# Тесты хуков и scripts/*.sh на временном репозитории с поддельным gh и локальным origin.
#   bash .claude/hooks/test-hooks.sh        # код 0 — всё зелёное
set -uo pipefail

HOOKS="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$HOOKS/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export TZ=UTC GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
export ISSUES_NOW="$(date -d '2026-10-20T12:00:00Z' +%s)"
unset HACK_MODE TAKEOVER_MIN SKIP_TEST_HOOK
FAIL=0 N=0
ok()   { N=$((N + 1)); echo "ok:   $1"; }
bad()  { N=$((N + 1)); echo "FAIL: $1"; FAIL=1; }
has()  { printf '%s' "$2" | grep -q -- "$3" && ok "$1" || bad "$1 — нет «$3»: $(printf '%s' "$2" | head -c 600)"; }
lacks(){ printf '%s' "$2" | grep -q -- "$3" && bad "$1 — лишнее «$3»: $(printf '%s' "$2" | head -c 600)" || ok "$1"; }

# --- поддельный gh: данные — файлы в $GHD, вызовы — в $GHD/calls.log
GHD="$TMP/gh" && mkdir -p "$GHD" "$TMP/bin"
cat > "$TMP/bin/gh" <<'GH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$GHD/calls.log"
[ -n "${GH_FAIL:-}" ] && { echo "error connecting to api.github.com" >&2; exit 1; }
case "$1 $2" in
  "auth status") exit 0 ;;
  "api user") echo me ;;
  "issue view")
    f="$GHD/issue-$3.json"
    [ -f "$f" ] || { echo "GraphQL: Could not resolve to an issue with the number of $3." >&2; exit 1; }
    if [ "${4:-}" = --json ]; then cat "$f"; else jq -r '"#\(.number) \(.title)"' "$f"; fi ;;
  "issue list") cat "$GHD/issues.json" ;;
  "pr list") [ -n "${GH_PR_FAIL:-}" ] && exit 1; cat "$GHD/prs.json" ;;
  "issue edit") ;;
  "issue comment") printf '#%s: %s\n' "$3" "$(cat)" >> "$GHD/comments.log" ;;
  *) echo "fake gh: $*" >&2; exit 1 ;;
esac
GH
chmod +x "$TMP/bin/gh"
export PATH="$TMP/bin:$PATH" GHD

iss() { # iss <N> <state> <assignee|-> <labels,через,запятую|-> <created> <updated>
  jq -n --argjson n "$1" --arg s "$2" --arg a "$3" --arg l "$4" --arg c "$5" --arg u "$6" \
    '{number: $n, title: "Задача \($n)", state: $s,
      assignees: (if $a == "-" then [] else [{login: $a}] end),
      labels: (if $l == "-" then [] else ($l | split(",") | map({name: .})) end),
      createdAt: $c, updatedAt: $u}' > "$GHD/issue-$1.json"
}
D=2026-10-20T
iss 3  OPEN   me   area:web            ${D}08:00:00Z ${D}11:00:00Z
iss 5  OPEN   mate area:api            ${D}08:00:00Z ${D}11:30:00Z
iss 7  OPEN   -    area:ui             ${D}08:00:00Z ${D}08:00:00Z
iss 9  OPEN   -    blocker             ${D}08:00:00Z ${D}08:00:00Z
iss 12 OPEN   me   area:api            ${D}08:00:00Z ${D}11:00:00Z
iss 13 OPEN   mate area:model          ${D}08:00:00Z ${D}10:00:00Z
iss 14 CLOSED me   -                   ${D}08:00:00Z ${D}09:00:00Z
iss 17 OPEN   mate -                   ${D}08:00:00Z ${D}11:40:00Z
iss 18 OPEN   mate -                   ${D}08:00:00Z ${D}10:00:00Z
iss 20 OPEN   gone area:data           ${D}07:00:00Z ${D}08:00:00Z
iss 21 OPEN   -    area:api            ${D}08:00:00Z ${D}08:00:00Z
iss 24 OPEN   mate -                   ${D}09:00:00Z ${D}09:30:00Z
jq -s '[.[] | select(.state == "OPEN")]' "$GHD"/issue-*.json > "$GHD/issues.json"
echo "[{\"number\": 40, \"headRefName\": \"worktree-18-y\", \"updatedAt\": \"${D}11:50:00Z\"}]" > "$GHD/prs.json"

# --- репозиторий: origin (bare) + основная копия
git init -q --bare -b main "$TMP/remote.git"
R="$TMP/repo" && git clone -q "$TMP/remote.git" "$R" 2>/dev/null && cd "$R" || exit 1
git checkout -q -b main
mkdir -p scripts .claude/hooks
cp "$SRC"/scripts/{issues,finish,mode,eval-final,heavy}.sh scripts/
cp "$HOOKS"/*.sh .claude/hooks/
printf '# Проект\n\nРежим: сборка\n' > CLAUDE.md
printf '.claude/worktrees/\n.env\n' > .gitignore
git add -A && git commit -q -m init && git push -q origin main
push_branch() { # push_branch <ветка> <дата коммита>
  git worktree add -q "$TMP/wt-$1" -b "$1" origin/main 2>/dev/null
  (cd "$TMP/wt-$1" && echo x > "f-$1" && git add "f-$1" &&
   GIT_COMMITTER_DATE="$2" git commit -q -m "$1" && git push -q origin "$1")
  git worktree remove --force "$TMP/wt-$1"; git branch -q -D "$1"
}
push_branch worktree-13-x  "${D}10:30:00Z"
push_branch worktree-24-old 2026-10-19T10:00:00Z

echo "--- mode.sh"
has   "режим из CLAUDE.md: сборка"                 "$(bash scripts/mode.sh --source)" "сборка"
git fetch -q
git worktree add -q "$TMP/wt-mode" -b worktree-90-mode origin/main 2>/dev/null
(cd "$TMP/wt-mode" && sed -i 's/^Режим: .*/Режим: финал/' CLAUDE.md && git commit -qam "финал" && git push -q origin HEAD:main)
git fetch -q
has   "main переключён на финал — финал везде"       "$(bash scripts/mode.sh --source)" "финал origin/main"
has   "переключение видно в ветке со старым CLAUDE.md" "$(cd "$TMP/wt-mode" && git reset -q --soft HEAD~1 && git stash -q && bash scripts/mode.sh)" "финал"
has   "session-context: в ветке сборка, в main финал" "$(cd "$TMP/wt-mode" && SKIP_ISSUES=1 bash .claude/hooks/session-context.sh)" "в твоей ветке «сборка», в main «финал»"
git worktree remove --force "$TMP/wt-mode"; git branch -q -D worktree-90-mode
has   "HACK_MODE переопределяет"                    "$(HACK_MODE=сборка bash scripts/mode.sh)" "сборка"
(cd "$TMP" && mkdir nogit && cd nogit && printf 'Режим: что-то\n' > CLAUDE.md && bash "$R/scripts/mode.sh") | grep -qx сборка \
  && ok "нераспознанный режим — сборка (строже)" || bad "нераспознанный режим"
git merge -q --ff-only origin/main
# вернуть main в сборку для остальных тестов
sed -i 's/^Режим: .*/Режим: сборка/' CLAUDE.md && git commit -qam "сборка" && git push -q origin main

echo "--- check-secrets.sh"
sec() { bash .claude/hooks/check-secrets.sh "$@" 2>&1; }
lacks "чистый репозиторий"                   "$(sec)" block
echo 'LLM_API_KEY=your_api_key_here' > .env.example
lacks ".env.example с плейсхолдером"         "$(sec)" block
echo 'const token = "f9a8Xc72kLmQ01zYvB3n4dHs"' > bot.js
has   "токен в коде — блок"                  "$(sec)" '"decision": "block"'
echo 'const token = "f9a8Xc72kLmQ01zYvB3n4dHs" // secret-scan: allow' > bot.js
lacks "secret-scan: allow"                   "$(sec)" block
echo 'client = OpenAI(api_key="sk-3f9a8c72b1d04e6fa9b7c2d5e8f10a4b")' > bot.js
has   "ключ LLM — блок"                      "$(sec)" '"decision": "block"'
git add bot.js
sec --git >/dev/null; [ $? = 1 ] && ok "pre-commit режим: секрет в индексе — код 1" || bad "pre-commit режим секретов"
git rm -q --cached bot.js; rm bot.js .env.example

echo "--- session-context.sh"
mkdir -p docs && cat > docs/timeline.md <<'EOF'
| Веха | Когда | Что готово |
|---|---|---|
| M0 skeleton | 2026-10-20 10:00 | сквозной путь на заглушках |
| M1 | 2026-10-20 15:30 | критические сценарии |
EOF
out="$(bash .claude/hooks/session-context.sh 2>&1)"
has   "основная копия на main — предупреждение"   "$out" "основная копия на main"
has   "режим виден"                               "$out" "\[режим\] сборка"
has   "ближайшая веха — будущая"                  "$out" "M1 — 2026-10-20 15:30 (через 3 ч 30 мин)"
has   "мои задачи"                                "$out" "#12 Задача 12"
has   "занятая area: видна с временем"            "$out" "area:api: #5 mate (движение 20.10 11:30)"
has   "blocker виден"                             "$out" "#9 Задача 9"
has   "кандидат на перехват"                      "$out" "можно перехватить (без движения > 45 мин): .*#20 gone, 240 мин"
lacks "свежая чужая задача — не кандидат"         "$out" "#17 mate, "
out="$(GH_FAIL=1 bash .claude/hooks/session-context.sh 2>&1)"; code=$?
has   "gh не отвечает — предупреждение, код 0 ($code)" "$out$code" "не авторизован или нет сети.*0$"
# #225: основная копия отстала от origin/main — веха всё равно из main
printf '| M2 | 2026-10-21 12:00 | покрытие |\n' > docs/timeline.md
git add docs/timeline.md && git commit -qm "вехи" && git push -q origin main && git reset -q --hard HEAD~1
printf '| M0 | 2026-10-19 10:00 | старое |\n' > docs/timeline.md
has   "веха из origin/main, а не из отставшей копии" "$(bash .claude/hooks/session-context.sh 2>&1)" "\[веха\] M2 — 2026-10-21 12:00"
rm docs/timeline.md && git merge -q --ff-only origin/main

echo "--- issues.sh take: мягкая area:, перехват, --human, TAKEOVER_MIN"
take() { : > "$GHD/comments.log"; : > "$GHD/calls.log"; bash scripts/issues.sh take "$@" 2>&1; }
out="$(take 21)"; code=$?
has   "занятая area: — предупреждение"            "$out" "area:api уже в работе: #5 mate"
[ $code = 0 ] && grep -q "issue edit 21 --add-assignee me" "$GHD/calls.log" && ok "занятая area: — не отказ, задача взята" || bad "мягкая area: ($code): $out"
out="$(take 13)"; code=$?
[ $code = 0 ] && grep -q "issue edit 13 --add-assignee me --remove-assignee mate" "$GHD/calls.log" \
  && ok "чужая без движения 90 мин > 45 — перехват" || bad "перехват #13 ($code): $out"
has   "перехват: комментарий с временем и веткой" "$(cat "$GHD/comments.log")" "#13: Перехвачена me у mate: без движения с 20.10 10:30 (90 мин назад).*Ветка: worktree-13-x — продолжаю её"
has   "перехват = продолжение чужой ветки"        "$out" "git worktree add .claude/worktrees/13-x -b worktree-13-x origin/worktree-13-x"
out="$(take 17)"; code=$?
[ $code != 0 ] && ! grep -q "issue edit" "$GHD/calls.log" && ok "чужая свежая (20 мин) — отказ без правок" || bad "#17 ($code): $out"
has   "отказ называет --human"                    "$out" "take 17 --human"
out="$(take 18)"; code=$?
[ $code != 0 ] && ok "свежий PR чужой задачи — отказ" || bad "#18 по PR ($code): $out"
out="$(take 17 --human)"; code=$?
[ $code = 0 ] && grep -q "по прямой просьбе человека" "$GHD/comments.log" && ok "«возьми #17» от человека — перехват с комментарием" || bad "--human ($code): $out"
out="$(take 24)"; code=$?
[ $code = 0 ] && grep -q "Ветки нет" "$GHD/comments.log" && ok "ветка старше задачи — не её ветка" || bad "#24 ($code): $(cat "$GHD/comments.log") $out"
echo 'TAKEOVER_MIN=120' > .env
out="$(take 13)"; code=$?
[ $code != 0 ] && has "TAKEOVER_MIN=120 из .env — те же 90 мин свежие" "$out" "TAKEOVER_MIN=120" || bad "TAKEOVER_MIN из .env ($code): $out"
rm .env
out="$(GH_PR_FAIL=1 take 13)"; code=$?
[ $code != 0 ] && has "PR не прочитались — движение неизвестно, не перехват" "$out" "движение неизвестно" || bad "неизвестное движение ($code): $out"
out="$(take 14)"; code=$?
[ $code != 0 ] && ok "закрытая задача — отказ" || bad "закрытая ($code)"

echo "--- check-issue.sh"
pre() { # pre <block|pass> <описание> <json>
  local out code
  out="$(printf '%s' "$3" | bash .claude/hooks/check-issue.sh 2>&1)"; code=$?
  if [ "$1" = block ] && [ "$code" != 2 ]; then bad "$2 — ожидался отказ, код $code: $out"
  elif [ "$1" = pass ] && [ "$code" != 0 ]; then bad "$2 — ожидался пропуск, код $code: $out"
  else ok "$2"; fi
}
edit() { jq -cn --arg p "$1" '{tool_name: "Edit", cwd: "'"$R"'", tool_input: {file_path: $p}}'; }
bashc() { jq -cn --arg c "$2" --arg d "$1" '{tool_name: "Bash", cwd: $d, tool_input: {command: $c}}'; }
for n in 12 13 14 15; do git worktree add -q ".claude/worktrees/$n-x" -b "worktree-$n-x" origin/main 2>/dev/null; done
git worktree add -q .claude/worktrees/wt-test -b worktree-wt-test origin/main 2>/dev/null
pre block "правка в основной копии"                  "$(edit "$R/src/a.py")"
pre block "ветка без номера задачи"                  "$(edit "$R/.claude/worktrees/wt-test/a.py")"
pre pass  "моя открытая задача"                      "$(edit "$R/.claude/worktrees/12-x/src/new/a.py")"
pre block "задача назначена на другого"              "$(edit "$R/.claude/worktrees/13-x/a.py")"
pre block "задача закрыта"                           "$(edit "$R/.claude/worktrees/14-x/a.py")"
pre block "задачи нет"                               "$(edit "$R/.claude/worktrees/15-x/a.py")"
pre pass  "файл вне репозитория"                     "$(edit /tmp/elsewhere/note.md)"
pre pass  "белый список: .arch/ в основной копии"     "$(edit "$R/.arch/hack1/03-candidates.md")"
pre pass  "белый список: <input>/_arch/"             "$(edit "$R/task/_arch/00-inventory.md")"
pre pass  "белый список: scratchpad"                 "$(edit "$R/scratchpad/notes.md")"
pre pass  "белый список: относительный .arch/"        "$(jq -cn '{tool_name: "Write", cwd: "'"$R"'", tool_input: {file_path: ".arch/hack1/x.md"}}')"
git worktree add -q "$TMP/archwt" -b arch/hack1 origin/main 2>/dev/null
pre pass  "ветка arch/<run> (team.py) — не проверяется" "$(bashc "$TMP/archwt" "git commit -m stage")"
pre block "git commit в основной копии"               "$(bashc "$R" "git add a && git commit -m x")"
pre pass  "git -C <worktree задачи> commit"          "$(bashc "$R" "git -C $R/.claude/worktrees/12-x commit -m x")"
pre pass  "git push внутри heredoc — данные"          "$(bashc "$R" "$(printf 'cat > /tmp/b.md <<EOF\nпотом git push\nEOF')")"
pre pass  "push --delete — уборка"                   "$(bashc "$R" "git push origin --delete worktree-12-x")"
pre pass  "обычная команда Bash"                     "$(bashc "$R" "git status")"
# Windows: C:/... and C:\... are absolute paths, not relative to cwd (#188).
if command -v cygpath >/dev/null 2>&1; then
  W="$(cygpath -m "$R")"; WB="$(cygpath -w "$R")"
  pre pass  "cd C:/<worktree задачи> && git commit — путь Windows" "$(bashc "$R" "cd $W/.claude/worktrees/12-x && git commit -m x")"
  pre block "cd C:/<основная копия> && git commit — путь Windows"  "$(bashc "$R/.claude/worktrees/12-x" "cd $W && git commit -m x")"
  pre pass  "правка C:\... в worktree задачи"                     "$(edit "$WB\.claude\worktrees\12-x\a.py")"
  pre block "правка C:\... в основной копии"                      "$(edit "$WB\src\a.py")"
fi
# Codex (.codex/hooks.json): правка — apply_patch, пути в тексте патча; Bash — команда может прийти массивом argv.
patch() { jq -cn --arg d "$1" --arg c "$2" '{tool_name: "apply_patch", cwd: $d, tool_input: {command: $c}}'; }
P1="$(printf '*** Begin Patch\n*** Update File: src/a.py\n@@\n-a\n+b\n*** End Patch\n')"
pre block "Codex: apply_patch в основной копии"            "$(patch "$R" "$P1")"
pre pass  "Codex: apply_patch в worktree своей задачи"      "$(patch "$R/.claude/worktrees/12-x" "$P1")"
P2="$(printf '*** Begin Patch\n*** Add File: %s/.claude/worktrees/12-x/b.py\n+x\n*** Update File: %s/.claude/worktrees/13-x/a.py\n@@\n-a\n+b\n*** End Patch\n' "$R" "$R")"
pre block "Codex: один путь патча в чужой задаче — отказ"   "$(patch "$R" "$P2")"
pre pass  "Codex: патч без путей — пропуск"                 "$(patch "$R" "")"
pre block "Codex: Bash массивом — git commit в основной копии" "$(jq -cn --arg d "$R" '{tool_name: "Bash", cwd: $d, tool_input: {command: ["bash", "-lc", "git commit -m x"]}}')"
rm -f "$(git -C .claude/worktrees/13-x rev-parse --git-dir)/issue-check"
GH_FAIL=1 pre pass "GitHub недоступен — номер в ветке есть, пропуск" "$(edit "$R/.claude/worktrees/13-x/a.py")"
grep -q "check-issue" "$(git rev-parse --git-common-dir)/hook-refusals.log" && ok "отказы пишутся в журнал хуков" || bad "журнал отказов пуст"

echo "--- check-paths.sh: печать holdout и md в финале"
paths() { local out code; out="$(printf '%s' "$3" | bash .claude/hooks/check-paths.sh 2>&1)"; code=$?
  if [ "$1" = block ] && [ $code != 2 ]; then bad "$2 — ожидался отказ ($code)"; elif [ "$1" = pass ] && [ $code != 0 ]; then bad "$2 — ожидался пропуск ($code): $out"; else ok "$2"; fi; }
tool() { jq -cn --arg t "$1" --arg k "$2" --arg v "$3" '{tool_name: $t, cwd: "'"$R"'/.claude/worktrees/12-x", tool_input: {($k): $v}}'; }
paths block "Read holdout"                    "$(tool Read file_path "$R/eval/holdout.tar.gz")"
paths block "Grep по holdout"                 "$(tool Grep path "eval/holdout.tar.gz")"
paths block "Bash: tar holdout"               "$(tool Bash command "tar -xzf eval/holdout.tar.gz -O | head")"
paths pass  "Bash: make eval-final"           "$(tool Bash command "EVAL_FINAL_CONFIRM=abc1234 make eval-final")"
paths pass  "Read обычного eval/cases"        "$(tool Read file_path "$R/eval/cases.jsonl")"
md="$R/.claude/worktrees/12-x/docs/report.md"
paths pass  "сборка: новый md — хук пропускает" "$(tool Write file_path "$md")"
HACK_MODE=финал paths block "финал: новый md — отказ"  "$(tool Write file_path "$md")"
HACK_MODE=финал paths pass  "финал: README.md — можно" "$(tool Write file_path "$R/.claude/worktrees/12-x/web/README.md")"
HACK_MODE=финал paths pass  "финал: md в .claude/ — можно" "$(tool Write file_path "$R/.claude/worktrees/12-x/.claude/agents/x.md")"
HACK_MODE=финал paths pass  "финал: существующий md — правка" "$(tool Write file_path "$R/CLAUDE.md")"

tpatch() { jq -cn --arg c "$1" '{tool_name: "apply_patch", cwd: "'"$R"'/.claude/worktrees/12-x", tool_input: {command: $c}}'; }
paths block "Codex: apply_patch по holdout"                "$(tpatch "$(printf '*** Begin Patch\n*** Update File: eval/holdout.tar.gz\n*** End Patch\n')")"
paths pass  "Codex: в тексте патча eval/holdout — не путь"  "$(tpatch "$(printf '*** Begin Patch\n*** Update File: CLAUDE.md\n@@\n-x\n+eval/holdout не читаем\n*** End Patch\n')")"
HACK_MODE=финал paths block "Codex: финал, новый md патчем — отказ"   "$(tpatch "$(printf '*** Begin Patch\n*** Add File: docs/report.md\n+x\n*** End Patch\n')")"
HACK_MODE=финал paths pass  "Codex: финал, правка существующего md"  "$(tpatch "$(printf '*** Begin Patch\n*** Update File: CLAUDE.md\n@@\n-x\n+y\n*** End Patch\n')")"

echo "--- check-git.sh: запреты git для Codex (у Claude — permissions.deny)"
gitc() { local out code; out="$(jq -cn --arg c "$3" --arg d "$R" '{tool_name: "Bash", cwd: $d, tool_input: {command: $c}}' | bash .claude/hooks/check-git.sh 2>&1)"; code=$?
  if [ "$1" = block ] && [ $code != 2 ]; then bad "$2 — ожидался отказ ($code)"; elif [ "$1" = pass ] && [ $code != 0 ]; then bad "$2 — ожидался пропуск ($code): $out"; else ok "$2"; fi; }
gitc block "force-push"                         "git push --force origin worktree-12-x"
gitc block "force-push через +refspec"          "git push origin +worktree-12-x"
gitc block "push в main"                        "git push origin HEAD:main"
gitc block "удаление main"                      "git push origin :main"
gitc block "add -A после cd"                    "cd x && git add -A"
gitc block "add ."                              "git add ."
gitc block "commit -am"                         "git commit -am x"
gitc block "commit --amend"                     "git -C /tmp/w commit --amend --no-edit"
gitc block "commit --no-verify"                 "git commit --no-verify -m x"
gitc block "rebase"                             "git rebase origin/main"
gitc block "pull --rebase"                      "git pull --rebase"
gitc block "reset --hard"                       "git reset --hard HEAD~1"
gitc block "git clean"                          "git clean -fd"
gitc block "gh pr merge --squash"               "gh pr merge 5 --squash"
gitc block "переменная окружения перед git"     "FOO=1 git push -f origin x"
gitc pass  "push своей ветки"                   "git push -u origin HEAD"
gitc pass  "удаление своей ветки"               "git push origin --delete worktree-12-x"
gitc pass  "add явных путей"                    "git add src/a.py docs/b.md"
gitc pass  "в сообщении коммита слова add -A"   'git commit -m "add -A запрещён"'
gitc pass  "merge main"                         "git merge --no-edit origin/main"
gitc pass  "gh pr merge --merge"                "gh pr merge 5 --merge"
gitc pass  "git push --force в heredoc — данные" "$(printf 'cat > /tmp/b.md <<EOF\ngit push --force\nEOF')"
gitc pass  "echo со словами git push -f"        "echo git push -f"

echo "--- check-data-files.sh и .githooks"
W="$R/.claude/worktrees/12-x"
mkdir -p "$W/data" && echo "a,b" > "$W/data/train.csv" && git -C "$W" add -f data/train.csv
out="$(cd "$W" && bash "$R/.claude/hooks/check-data-files.sh" 2>&1)"; code=$?
[ $code = 1 ] && has "данные в data/ — отказ" "$out" "data/train.csv" || bad "data/ ($code): $out"
out="$(bashc "$W" "git commit -m data" | bash "$R/.claude/hooks/check-data-files.sh" pre-tool)"
has   "PreToolUse git commit — deny JSON"   "$out" 'permissionDecision": "deny'
git -C "$W" rm -q --cached data/train.csv
head -c $((6 * 1024 * 1024)) /dev/zero > "$W/big.bin" && git -C "$W" add big.bin
(cd "$W" && bash "$R/.claude/hooks/check-data-files.sh" >/dev/null 2>&1); [ $? = 1 ] && ok "файл 6 МБ вне LFS — отказ" || bad "6 МБ"
echo 'big.bin filter=lfs diff=lfs merge=lfs -text' > "$W/.gitattributes"
(cd "$W" && bash "$R/.claude/hooks/check-data-files.sh" >/dev/null 2>&1); [ $? = 0 ] && ok "файл 6 МБ в LFS — можно" || bad "6 МБ LFS"
git -C "$W" rm -q --cached -f big.bin; rm -f "$W/big.bin" "$W/.gitattributes"
mkdir -p .githooks "$W/.githooks" && cp "$SRC"/.githooks/* .githooks/ && cp .githooks/* "$W/.githooks/"
echo "отчёт" > "$W/report.md" && git -C "$W" add report.md
(cd "$W" && HACK_MODE=финал bash .githooks/pre-commit >/dev/null 2>&1); [ $? = 1 ] && ok "pre-commit: финал, новый md — отказ" || bad "pre-commit md финал"
(cd "$W" && HACK_MODE=сборка bash .githooks/pre-commit >/dev/null 2>&1); [ $? = 0 ] && ok "pre-commit: сборка, новый md — можно" || bad "pre-commit md сборка"
git -C "$W" rm -q --cached report.md; rm "$W/report.md"
(printf 'refs/heads/x 0000000000000000000000000000000000000000 refs/heads/x abc\n' | bash .githooks/pre-push origin url >/dev/null 2>&1) && ok "pre-push: удаление ветки пропускается" || bad "pre-push удаление"
(printf 'refs/heads/arch/hack1 abc123 refs/heads/arch/hack1 def456\n' | bash .githooks/pre-push origin url >/dev/null 2>&1) && ok "pre-push: arch/<run> пропускается" || bad "pre-push arch"
(printf 'refs/heads/main abc123 refs/heads/main def456\n' | bash .githooks/pre-push origin url >/dev/null 2>&1) && bad "pre-push: push из основной копии пропущен" || ok "pre-push: push из основной копии — отказ"

echo "--- check-worktrees.sh"
lacks "секретов в worktree нет"               "$(bash .claude/hooks/check-worktrees.sh)" block
echo 'const token = "f9a8Xc72kLmQ01zYvB3n4dHs"' > .claude/worktrees/wt-test/bot.js
has   "секрет в чужом worktree — блок с путём" "$(bash .claude/hooks/check-worktrees.sh)" "worktree .*wt-test"
rm .claude/worktrees/wt-test/bot.js

echo "--- check-upstream.sh"
git clone -q "$TMP/remote.git" "$TMP/mate" 2>/dev/null
mate_push() { (cd "$TMP/mate" && git pull -q --ff-only && echo "$2" >> "$1" && git add "$1" && git commit -q -m "$2" && git push -q origin HEAD:main); }
cd .claude/worktrees/12-x
up() { echo '{}' | bash "$R/.claude/hooks/check-upstream.sh" "${1:-stop}" 2>&1; }
up >/dev/null
lacks "новых коммитов нет"                     "$(up)" block
mate_push other.txt "чужой файл"
lacks "чужой коммит без пересечения"           "$(up)" block
echo mine > shared.txt
mate_push shared.txt "тот же файл"
has   "пересечение с моим файлом — блок"       "$(up)" "shared.txt"
lacks "то же состояние второй раз — тихо"      "$(up)" block
(cd "$TMP/mate" && sed -i 's/^Режим: .*/Режим: финал/' CLAUDE.md && git commit -qam "финал" && git push -q origin HEAD:main)
has   "смена режима в main — видна"            "$(up)" "Сменился режим правил: Режим: финал"
rm shared.txt
(cd "$TMP/mate" && sed -i 's/^Режим: .*/Режим: сборка/' CLAUDE.md && git commit -qam "сборка" && git push -q origin HEAD:main)
cd "$R"

echo "--- eval-final.sh: печать"
git pull -q --ff-only
mkdir -p "$TMP/h" eval && echo "S9 holdout" > "$TMP/h/scenarios.md"
# RESULT comes from a file, not from the recipe text: make on Windows re-encodes Cyrillic in recipes (#191).
# The real RESULT line is printed by the eval program, not by make.
echo "RESULT: 3/4 сценариев" > "$TMP/h/result.txt"
tar -czf eval/holdout.tar.gz -C "$TMP/h" . && sha256sum eval/holdout.tar.gz > eval/holdout.sha256
echo 'include stack.mk' > Makefile
printf 'STACK := t\nstack-eval-final:\n\t@cat $(HOLDOUT_DIR)/scenarios.md $(HOLDOUT_DIR)/result.txt\n' > stack.mk
git add -A && git commit -qm eval
has   "--verify: печать цела"                   "$(bash scripts/eval-final.sh --verify)" "печать цела"
out="$(bash scripts/eval-final.sh 2>&1)"; code=$?
[ $code != 0 ] && has "сборка — holdout не открывается" "$out" "режим не «финал»" || bad "eval-final в сборке ($code)"
out="$(HACK_MODE=финал bash scripts/eval-final.sh 2>&1)"; code=$?
[ $code != 0 ] && has "без EVAL_FINAL_CONFIRM — отказ" "$out" "EVAL_FINAL_CONFIRM=" || bad "confirm ($code)"
touch dirty.txt
out="$(HACK_MODE=финал EVAL_FINAL_CONFIRM="$(git rev-parse HEAD)" bash scripts/eval-final.sh 2>&1)"; code=$?
[ $code != 0 ] && has "грязное дерево — отказ" "$out" "дерево не чистое" || bad "dirty ($code)"
rm dirty.txt
out="$(HACK_MODE=финал EVAL_FINAL_CONFIRM="$(git rev-parse --short=8 HEAD)" bash scripts/eval-final.sh 2>&1)"; code=$?
[ $code = 0 ] && has "финал + чистое дерево + коммит — оценка" "$out" "RESULT: 3/4" || bad "eval-final ($code): $out"
has   "строка в журнале"                       "$(cat eval/final-log.md)" "| $(git rev-parse --short=12 HEAD) | .* | 3/4 сценариев | 0 |"
git add eval/final-log.md && git commit -qm "журнал eval-final"
out="$(HACK_MODE=финал EVAL_FINAL_CONFIRM="$(git rev-parse HEAD)" bash scripts/eval-final.sh 2>&1)"; code=$?
[ $code != 0 ] && has "тот же код второй раз (журнал закоммичен) — отказ" "$out" "уже оценивался" || bad "повтор ($code): $out"
echo "S9 правка" > "$TMP/h/scenarios.md" && tar -czf eval/holdout.tar.gz -C "$TMP/h" .
out="$(bash scripts/eval-final.sh --verify 2>&1)"; code=$?
[ $code != 0 ] && has "подменённый holdout — печать нарушена" "$out" "печать нарушена" || bad "подмена ($code)"
git checkout -q -- eval

echo "--- heavy.sh: общий замок тяжёлых прогонов (#333)"
if command -v flock >/dev/null; then
  L="$TMP/heavy333.lock"
  hv() { HEAVY_LOCK="$L" HEAVY_LOCK_WAIT=1 bash -c '. "$1"; shift; heavy "$@"' _ "$R/scripts/heavy.sh" "$@" 2>&1; }
  has   "замок свободен — команда идёт"            "$(hv echo ran)" "ran"
  has   "замок держит предок — без второго flock" "$(flock "$L" bash -c '. "$1"; HEAVY_LOCK="$2" HEAVY_LOCK_WAIT=1 heavy echo ran' _ "$R/scripts/heavy.sh" "$L" 2>&1)" "ran"
  flock "$L" sleep 4 & holder=$!
  for _ in $(seq 50); do flock -n "$L" true || break; sleep 0.1; done
  out="$(hv echo ran)"; code=$?
  [ "$code" = 75 ] && lacks "замок занят чужим — код 75, команда не шла" "$out" "ran" || bad "heavy: занятый замок, код $code"
  # a parent shell whose text mentions flock and the lock is not the holder (Claude Code runs bash -c '<text>')
  out="$(bash -c ': flock '"$L"'; HEAVY_LOCK="$2" HEAVY_LOCK_WAIT=1 bash -c ". \"\$1\"; heavy echo ran" _ "$1"; exit $?' _ "$R/scripts/heavy.sh" "$L" 2>&1)"; code=$?  # exit $?: the outer shell stays a parent, no exec
  [ "$code" = 75 ] && lacks "предок-оболочка со словом flock — не держатель, ждём" "$out" "ran" || bad "heavy: ложный предок, код $code: $out"
  wait "$holder"
fi
# #379: a direct run executes the command under the lock and passes its code (it was a silent no-op, rc 0)
has   "прямой запуск — команда идёт"              "$(HEAVY_LOCK="$TMP/heavy379.lock" bash "$R/scripts/heavy.sh" echo ran 2>&1)" "ran"
HEAVY_LOCK="$TMP/heavy379.lock" bash "$R/scripts/heavy.sh" sh -c 'exit 3' >/dev/null 2>&1; code=$?
[ "$code" = 3 ] && ok "прямой запуск — код команды наружу (3)" || bad "heavy прямой запуск: код $code"
bash "$R/scripts/heavy.sh" >/dev/null 2>&1; code=$?
[ "$code" = 2 ] && ok "прямой запуск без команды — ошибка (2), не молчаливый 0" || bad "heavy без команды: код $code"
has   "HEAVY_LOCK пуст — без замка"               "$(HEAVY_LOCK= bash -c '. "$1"; heavy echo ran' _ "$R/scripts/heavy.sh" 2>&1)" "ran"

# #595: no flock (Git Bash, macOS) — the fallback lock is an atomic mkdir of "$LOCK.d". PATH has wrappers of the tools only.
echo "--- heavy.sh: запасной замок без flock (#595)"
NF="$TMP/noflock" && mkdir -p "$NF"
for t in bash sh cat sed tr grep cut sleep date mkdir rm mv seq touch head tail dirname mktemp timeout jq git find; do
  p="$(command -v "$t" 2>/dev/null)"
  case "$p" in /*) printf '#!/bin/sh\nexec "%s" "$@"\n' "$p" > "$NF/$t" && chmod +x "$NF/$t" ;; esac  # a script, not a link: links are copies in Git Bash
done
# make is a stub: a native Windows make cannot run the sh wrapper
printf '#!/bin/sh\necho "1 failed"; exit 1\n' > "$NF/make" && chmod +x "$NF/make"
[ -z "$(PATH="$NF" command -v flock)" ] && ok "#595: в тестовой среде flock не находится" || bad "#595: flock виден в PATH=$NF"
LN="$TMP/heavy595.lock"
hn() { HEAVY_LOCK="$LN" HEAVY_LOCK_WAIT="${W595:-1}" PATH="$NF" bash -c '. "$1"; shift; heavy "$@"' _ "$R/scripts/heavy.sh" "$@" 2>&1; }
has   "#595: замок свободен — команда идёт"     "$(hn echo ran)" "ran"
[ ! -e "$LN.d" ] && ok "#595: после прогона замок снят" || bad "#595: каталог замка остался"
hn bash -c 'exit 3' >/dev/null; code=$?
[ "$code" = 3 ] && ok "#595: код команды наружу (3)" || bad "#595: код команды $code"
# two parallel runs go one after another: the second starts after the first ends
OUTP="$TMP/order595.log"; : > "$OUTP"
( W595=30 hn bash -c 'echo a-start >>"$1"; sleep 2; echo a-end >>"$1"' _ "$OUTP" >/dev/null ) & p1=$!
for _ in $(seq 50); do grep -q a-start "$OUTP" && break; sleep 0.1; done
( W595=30 hn bash -c 'echo b-start >>"$1"; echo b-end >>"$1"' _ "$OUTP" >/dev/null ) & p2=$!
wait "$p1" "$p2"
[ "$(tr '\n' ' ' <"$OUTP")" = "a-start a-end b-start b-end " ] && ok "#595: два параллельных heavy идут по очереди" || bad "#595: порядок: $(tr '\n' ' ' <"$OUTP")"
# a dead owner: the lock is removed with a message, the command runs
mkdir "$LN.d" && echo "999999 $(date +%s)" > "$LN.d/owner"
out="$(hn echo ran)"
has   "#595: замок с мёртвым pid снят — команда идёт" "$out" "ran"
has   "#595: снятие замка — сообщение в stderr"       "$out" "снимаю"
# a live owner (this test shell): code 75, the command does not run
mkdir "$LN.d" && echo "$$ $(date +%s)" > "$LN.d/owner"
out="$(hn echo ran)"; code=$?
[ "$code" = 75 ] && lacks "#595: живой замок занят — код 75, команда не шла" "$out" "ran" || bad "#595: живой замок, код $code: $out"
[ -d "$LN.d" ] && ok "#595: чужой живой замок не тронут" || bad "#595: чужой живой замок снят"
# a live owner, but the lock is older than the limit: removed
echo "$$ $(( $(date +%s) - 100000 ))" > "$LN.d/owner"
has   "#595: замок старше лимита снят" "$(hn echo ran)" "ran"
# a nested heavy inside heavy does not wait for itself
out="$(hn bash -c '. "$1"; HEAVY_LOCK_WAIT=1 heavy echo inner' _ "$R/scripts/heavy.sh")"; code=$?
[ "$code" = 0 ] && has "#595: вложенный heavy не ждёт сам себя" "$out" "inner" || bad "#595: вложенный heavy, код $code: $out"
# TERM to the holder (its child sends it to the parent): the lock is removed when the command ends, code 143
rm -rf "$LN.d"
hn bash -c 'kill -TERM $PPID; sleep 1' >/dev/null; code=$?
[ "$code" = 143 ] && ok "#595: TERM держателю — код 143" || bad "#595: TERM держателю, код $code"
[ ! -e "$LN.d" ] && ok "#595: замок снят после TERM" || { bad "#595: замок остался после TERM"; rm -rf "$LN.d"; }
# two waiters meet a dead lock: one breaks it, the commands never overlap (a lock taken by the other is not removed)
ovl_ok=1; msgs=0
for _ in 1 2 3 4; do
  : > "$OUTP"; rm -rf "$LN.d"; mkdir "$LN.d" && echo "999999 $(date +%s)" > "$LN.d/owner"
  ( W595=30 hn bash -c 'echo s >>"$1"; sleep 0.7; echo e >>"$1"' _ "$OUTP" >"$TMP/m1.log" ) &
  p1=$!
  ( W595=30 hn bash -c 'echo s >>"$1"; sleep 0.7; echo e >>"$1"' _ "$OUTP" >"$TMP/m2.log" ) &
  p2=$!
  wait "$p1" "$p2"
  [ "$(tr "\n" " " <"$OUTP")" = "s e s e " ] || ovl_ok=0
  msgs=$((msgs + $(cat "$TMP/m1.log" "$TMP/m2.log" | grep -c "снимаю")))
done
[ "$ovl_ok" = 1 ] && ok "#595: два ждущих при мёртвом замке — команды не пересекаются (4 раунда)" || bad "#595: команды пересеклись: $(tr "\n" " " <"$OUTP")"
[ "$msgs" = 4 ] && ok "#595: мёртвый замок снят ровно один раз за раунд" || bad "#595: «снимаю» $msgs раз за 4 раунда (ждали 4)"
# a lock without owner (the holder died after mkdir): removed after HEAVY_LOCK_NOOWNER_SEC
rm -rf "$LN.d"; mkdir "$LN.d"
out="$(HEAVY_LOCK_NOOWNER_SEC=1 W595=30 hn echo ran)"
has   "#595: замок без owner снят по порогу — команда идёт" "$out" "ran"
has   "#595: замок без owner — сообщение «без владельца»"    "$out" "без владельца"
# a fresh lock without owner is not touched before the threshold
rm -rf "$LN.d"; mkdir "$LN.d"
out="$(W595=1 hn echo ran)"; code=$?
[ "$code" = 75 ] && lacks "#595: свежий замок без owner не трогаем — код 75" "$out" "ran" || bad "#595: свежий замок без owner, код $code: $out"
rm -rf "$LN.d"
# a holder whose lock was taken by age does not remove the new lock on exit
rm -rf "$LN.d"
( hn bash -c 'sleep 1.5' >/dev/null ) & ph=$!
for _ in $(seq 50); do [ -f "$LN.d/owner" ] && break; sleep 0.1; done
rm -rf "$LN.d"; mkdir "$LN.d" && echo "$$ $(date +%s)" > "$LN.d/owner"   # the lock is taken from the old holder
wait "$ph"
[ -f "$LN.d/owner" ] && ok "#595: старый держатель не снял замок нового" || bad "#595: старый держатель снял чужой замок"
rm -rf "$LN.d"
# #602: a dead lock that cannot be removed does not keep the waiter past HEAVY_LOCK_WAIT — code 75, no endless loop
mkdir "$LN.d" && echo "999999 $(date +%s)" > "$LN.d/owner"
out="$(HEAVY_LOCK="$LN" HEAVY_LOCK_WAIT=2 PATH="$NF" timeout 20 bash -c '. "$1"; shift; _heavy_rm() { :; }; heavy "$@"' _ "$R/scripts/heavy.sh" echo ran 2>&1)"; code=$?
[ "$code" = 75 ] && lacks "#602: неудаляемый мёртвый замок — код 75 за HEAVY_LOCK_WAIT" "$out" "ran" || bad "#602: неудаляемый мёртвый замок, код $code (124 — цикл без конца)"
rm -rf "$LN.d" "$LN.d.break"
# #602: removing a lock leaves no renamed copy behind
mkdir "$LN.d" && echo "999999 $(date +%s)" > "$LN.d/owner"
hn echo ran >/dev/null
[ -z "$(ls -d "$LN".d.rm.* 2>/dev/null)" ] && ok "#602: после снятия замка копии .rm.* не остались" || bad "#602: остались $(ls -d "$LN".d.rm.*)"
rm -rf "$LN.d"

echo "--- finish.sh"
cd .claude/worktrees/12-x
git remote remove origin 2>/dev/null
out="$(bash "$R/scripts/finish.sh" 2>&1)"; code=$?
[ $code != 0 ] && has "нет remote — понятная ошибка, не успех" "$out" "нет remote origin" || bad "finish без remote ($code): $out"
git remote add origin "$TMP/remote.git"
out="$(bash "$R/scripts/finish.sh" 2>&1)"; code=$?
[ $code != 0 ] && has "нет своих коммитов — PR не создаётся" "$out" "нет своих коммитов" || bad "finish без коммитов ($code): $out"
cd "$R"

echo
[ $FAIL = 0 ] && echo "ВСЕ ТЕСТЫ ЗЕЛЁНЫЕ: $N" || echo "ЕСТЬ КРАСНЫЕ (из $N)"
exit $FAIL
