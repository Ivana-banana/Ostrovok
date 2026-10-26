#!/usr/bin/env bash
# Stop hook по режиму (CLAUDE.md, «Режимы»):
#   сборка — ход не заканчивается с красным `make check`;
#   финал  — молчит: тесты запускает только человек по просьбе «полный прогон».
# Молчит также, если: стек не выбран (нет stack.mk); в рабочей копии нет своей работы
# (нет правок и коммитов сверх origin/main); ничего не менялось с последнего зелёного.
# Локальный отказ от хука (среда красная не по вине кода): SKIP_TEST_HOOK=1.
# Молчит при `Tests: off` или `Tests: ask` в CLAUDE.local.md (скилл /tests). finish.sh всё равно гоняет make check.
# Общий замок тяжёлых прогонов (#330): на одном ноутбуке несколько сессий; параллельный make check роняет тяжёлые
# тесты по таймауту. Хук ждёт замок HEAVY_LOCK до HEAVY_LOCK_WAIT с; не дождался — блок «не запускался», не зелёный.
# Нет flock (macOS, Git Bash) — запасной замок из scripts/heavy.sh (mkdir, #595). HEAVY_LOCK= пуст — без замка.
set -uo pipefail
[ -n "${SKIP_TEST_HOOK:-}" ] && exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
cd "$(git rev-parse --show-toplevel)" || exit 0
# CLAUDE.local.md не в git: в worktree его может не быть — тогда берём из основной копии.
main="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
grep -qsE '^Tests: *(off|ask) *$' CLAUDE.local.md "$main/CLAUDE.local.md" && exit 0
HOOKS="$(cd "$(dirname "$0")" && pwd)"

[ "$(bash scripts/mode.sh 2>/dev/null || bash "$HOOKS/../../scripts/mode.sh")" = финал ] && exit 0
[ -f stack.mk ] || exit 0

ahead=0
git rev-parse -q --verify origin/main >/dev/null && ahead="$(git rev-list --count origin/main..HEAD 2>/dev/null || echo 0)"
[ -z "$(git status --porcelain --untracked-files=normal)" ] && [ "$ahead" = 0 ] && exit 0

MARKER="$(git rev-parse --git-dir)/last-green-check"
if [ -f "$MARKER" ]; then
  changed="$(find . -type f -newer "$MARKER" \
    -not -path './.git/*' -not -path './.claude/worktrees/*' -not -path './data/*' -not -path './outputs/*' \
    -not -path '*/node_modules/*' -not -path '*/.venv/*' -not -path '*/__pycache__/*' \
    -not -path '*/.pytest_cache/*' -not -path '*/.ruff_cache/*' -not -path './build/*' \
    -not -path './install/*' -not -path './log/*' -not -path '*/dist/*' 2>/dev/null | head -1)"
  [ -z "$changed" ] && exit 0
fi

LOCK="${HEAVY_LOCK-/tmp/hack-heavy-tests.lock}"
HEAVY="$HOOKS/../../scripts/heavy.sh"
if [ -n "$LOCK" ] && { command -v flock >/dev/null 2>&1 || [ -f "$HEAVY" ]; }; then
  RUN="${CHECK_TIMEOUT:-540}"
  WAIT="${HEAVY_LOCK_WAIT:-600}"
  # нечисловые значения сломали бы $((…)) — хук упал бы без блока (#333)
  case "$RUN" in '' | *[!0-9]*) RUN=540 ;; esac
  case "$WAIT" in '' | *[!0-9]*) WAIT=600 ;; esac
  [ "$RUN" -gt 1140 ] && RUN=1140
  # ожидание и прогон вместе — в лимите хука (settings.json: 1200 с), иначе Claude Code убьёт хук без блока
  [ "$((WAIT + RUN))" -gt 1140 ] && WAIT=$((1140 - RUN))
  [ "$WAIT" -lt 0 ] && WAIT=0
  if command -v flock >/dev/null 2>&1; then
    # -E 75: свой код, когда замок не получен — make и timeout его не дают (PR #332)
    OUT="$(flock -E 75 -w "$WAIT" "$LOCK" timeout "$RUN" make -s check 2>&1)"; STATUS=$?
  else
    # нет flock: запасной замок из heavy.sh, те же WAIT и код 75 (#595)
    # shellcheck source=../../scripts/heavy.sh
    OUT="$(. "$HEAVY"; HEAVY_LOCK_WAIT="$WAIT" heavy timeout "$RUN" make -s check 2>&1)"; STATUS=$?
  fi
  if [ "$STATUS" -eq 75 ] || [ "$STATUS" -eq 64 ] || [ "$STATUS" -eq 66 ]; then
    why="замок занят ($LOCK, ждали $WAIT с) — другая сессия гоняет тяжёлое. Повтори проверку, когда замок освободится."
    [ "$STATUS" -eq 75 ] || why="flock не взял замок $LOCK (код $STATUS): $(printf '%s' "$OUT" | tail -n 3)"
    . "$HOOKS/lib.sh"; hook_log check-tests "make check не запускался, код flock $STATUS"
    jq -n --arg why "$why" '{decision: "block", reason: ("Режим «сборка»: make check не запускался: " + $why)}'
    exit 0
  fi
else
  RUN="${CHECK_TIMEOUT:-540}"
  case "$RUN" in '' | *[!0-9]*) RUN=540 ;; esac
  OUT="$(timeout "$RUN" make -s check 2>&1)"; STATUS=$?
fi
if [ "$STATUS" -eq 0 ]; then
  touch "$MARKER"
  exit 0
fi
TAIL="$(printf '%s' "$OUT" | tail -n 40)"
. "$HOOKS/lib.sh"; hook_log check-tests "make check код $STATUS"
jq -n --arg out "$TAIL" --arg s "$STATUS" \
  '{decision: "block", reason: ("Режим «сборка»: make check красный (код " + $s + ") — почини до конца хода:\n" + $out)}'
exit 0
