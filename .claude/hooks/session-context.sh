#!/usr/bin/env bash
# SessionStart: stdout попадает в контекст сессии. Показывает: ветку и worktree, режим
# правил, ближайшую веху из docs/timeline.md, мои задачи, занятые area:, кандидатов на
# перехват, открытые blocker (scripts/issues.sh board). Ничего не блокирует.
# Нет gh, авторизации или сети — одна строка предупреждения вместо списка.
set -uo pipefail

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
top="$(git rev-parse --show-toplevel)"
cd "$top" || exit 0

branch="$(git branch --show-current 2>/dev/null)"
dirty="$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
if [ "$(git rev-parse --git-dir)" = "$(git rev-parse --git-common-dir)" ]; then where="основная копия"; else where="worktree"; fi
echo "[git] $where $top; ветка: ${branch:-detached}; незакоммиченных путей: $dirty"

if [ "$where" = "основная копия" ] && [ "$branch" = main ]; then
  echo "[git] ВНИМАНИЕ: основная копия на main. Задачу, меняющую файлы, делай в своём worktree:"
  echo "      bash scripts/issues.sh take <N>, затем git worktree add .claude/worktrees/<N>-<тема> -b worktree-<N>-<тема> origin/main (GIT.md)"
  [ "$dirty" != 0 ] && echo "      Незакоммиченные изменения здесь могут быть чужими — не коммить и не трогай их."
fi

if [ -f scripts/mode.sh ]; then
  read -r mode src < <(bash scripts/mode.sh --source)
  local_mode="$(HACK_MODE= git show HEAD:CLAUDE.md 2>/dev/null | sed -n 's/^[*_ ]*Режим:[*_ ]*\([^*_ |]*\).*/\1/p' | head -1)"
  echo "[режим] $mode (по $src) — правила CLAUDE.md, «Режимы»"
  if [ -n "$local_mode" ] && [ "$local_mode" != "$mode" ]; then
    echo "[режим] в твоей ветке «$local_mode», в main «$mode» — влей origin/main."
  fi
fi

# Ближайшая веха: первая строка таблицы docs/timeline.md с датой «ГГГГ-ММ-ДД ЧЧ:ММ» не в прошлом.
# Как и режим, вехи берутся из origin/main: основная копия может отстать (#225).
timeline="$(git show origin/main:docs/timeline.md 2>/dev/null || cat docs/timeline.md 2>/dev/null)"
if [ -n "$timeline" ]; then
  now="${ISSUES_NOW:-$(date +%s)}"
  next=""
  while IFS='|' read -r _ id when what _; do
    when="$(printf '%s' "$when" | xargs)"
    t="$(date -d "$when" +%s 2>/dev/null)" || continue
    if [ "$t" -ge "$now" ]; then
      left=$(( (t - now) / 60 ))
      next="$(printf '%s' "$id" | xargs) — $when (через $((left / 60)) ч $((left % 60)) мин): $(printf '%s' "$what" | xargs)"
      break
    fi
  done < <(printf '%s\n' "$timeline" | grep -E '^\|[^|]*\|[[:space:]]*[0-9]{4}-[0-9]{2}-[0-9]{2}')
  if [ -n "$next" ]; then echo "[веха] $next"; else echo "[веха] в docs/timeline.md нет будущих вех с датой"; fi
fi

[ "${SKIP_ISSUES:-0}" = 1 ] && exit 0
[ -f scripts/issues.sh ] || exit 0
if ! command -v gh >/dev/null 2>&1; then
  echo "[issues] нет gh — задачи не видны. Установите GitHub CLI и выполните gh auth login."
  exit 0
fi
if ! timeout 6 gh auth status >/dev/null 2>&1; then
  echo "[issues] gh не авторизован или нет сети — задачи не видны (gh auth login)."
  exit 0
fi
if ! ISSUES_TIMEOUT=6 timeout 15 bash scripts/issues.sh board 2>/dev/null; then
  echo "[issues] GitHub не ответил — занятость не видна (bash scripts/issues.sh mine)."
fi
exit 0
