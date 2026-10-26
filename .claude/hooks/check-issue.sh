#!/usr/bin/env bash
# PreToolUse: без взятой задачи не работаем. Правка файла (Edit, Write, MultiEdit,
# NotebookEdit) и git commit / git push через Bash разрешены только в worktree задачи:
# ветка worktree-<N>-<тема>, задача #N открыта и назначена на меня.
# Проверку делает `scripts/issues.sh check`. Не проверяются: файлы вне репозитория,
# белый список (.arch/, _arch/ — рабочие папки планировщика; scratchpad агента),
# ветки arch/<run> (team.py), удаление ветки через push (уборка).
# Codex (.codex/hooks.json): правка — apply_patch, проверяется каждый путь патча.
# Отказ — код 2, причина уходит агенту в stderr. Нет jq — пропуск.
set -uo pipefail

input="$(cat)"
command -v jq >/dev/null 2>&1 || exit 0
. "$(dirname "$0")/lib.sh"
tool="$(printf '%s' "$input" | jq -r '.tool_name // ""')"
cwd="$(printf '%s' "$input" | jq -r '.cwd // "."')"

abs() { # путь от cwd; Windows: C:\a\b -> C:/a/b
  local t="${1//\\//}"
  case "$t" in /*|[A-Za-z]:/*) printf '%s\n' "$t" ;; ?*) printf '%s\n' "$cwd/$t" ;; esac
}

case "$tool" in
  Edit|Write|MultiEdit|NotebookEdit)
    targets="$(abs "$(printf '%s' "$input" | jq -r '.tool_input.file_path // .tool_input.notebook_path // ""')")"
    ;;
  apply_patch)
    targets="$(printf '%s' "$input" | hook_command | patch_paths | while IFS= read -r p; do abs "$p"; done)"
    ;;
  Bash)
    # Тело heredoc — данные, а не команда: смотрим строки до первой строки с «<<».
    cmd="$(printf '%s' "$input" | hook_command | awk '{print} /<</{exit}' | tr -d "\"'")"
    printf '%s' "$cmd" | grep -Eq '(^|[;&|(]|[[:space:]])git([[:space:]]+-C[[:space:]]+[^[:space:]]+)?[[:space:]]+(commit|push)([[:space:]]|$)' || exit 0
    if printf '%s' "$cmd" | grep -Eq 'git([[:space:]]+-C[[:space:]]+[^[:space:]]+)?[[:space:]]+push[[:space:]].*(--delete|[[:space:]]-d[[:space:]]|[[:space:]]:[^[:space:]]+)'; then
      exit 0
    fi
    target="$(printf '%s' "$cmd" | sed -nE 's/.*git[[:space:]]+-C[[:space:]]+([^[:space:]]+)[[:space:]]+(commit|push).*/\1/p' | head -1)"
    [ -n "$target" ] || target="$(printf '%s' "$cmd" | sed -nE 's/^[[:space:]]*cd[[:space:]]+([^[:space:];&]+).*/\1/p' | head -1)"
    [ -n "$target" ] || target="$cwd"
    targets="$(abs "$target")"
    ;;
  *) exit 0 ;;
esac
[ -n "$targets" ] || exit 0

# Скрипт — из того worktree, где идёт работа; в старой ветке без него — копия рядом с хуком.
check_target() {
  local target="$1" dir script reason
  dir="$target"
  while [ ! -d "$dir" ] && [ "$dir" != / ] && [ "$dir" != . ]; do dir="$(dirname "$dir")"; done
  script="$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null)/scripts/issues.sh"
  [ -f "$script" ] || script="$(cd "$(dirname "$0")/../.." && pwd)/scripts/issues.sh"
  [ -f "$script" ] || return 0
  if ! reason="$(bash "$script" check "$target" 2>&1)"; then
    (cd "$dir" && hook_log check-issue "$reason")
    echo "Без взятой задачи работать нельзя (CLAUDE.md, «Как работаем»). $reason" >&2
    return 2
  fi
  [ -n "$reason" ] && echo "$reason" >&2
  return 0
}
while IFS= read -r target; do
  [ -n "$target" ] || continue
  check_target "$target" || exit 2
done <<< "$targets"
exit 0
