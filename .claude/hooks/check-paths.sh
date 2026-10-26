#!/usr/bin/env bash
# PreToolUse: пути, которые агенту нельзя трогать.
# 1. Печать holdout. eval/holdout.tar.gz и всё eval/holdout* не читает и не правит ни один
#    инструмент агента (Read, Grep, Glob, Edit, Write, Bash с этим путём в команде).
#    Holdout открывает только `make eval-final` (scripts/eval-final.sh) на заморозке.
# 2. Режим «финал»: новый md-файл, кроме README.md и файлов в .claude/, не создаётся —
#    отчёт идёт в чат, итог — в описание PR.
# Codex (.codex/hooks.json): правка — apply_patch; проверяются пути патча, а не его текст
# (CLAUDE.md упоминает eval/holdout — правка такого документа не трогает holdout).
# Отказ — код 2, причина в stderr.
set -uo pipefail

input="$(cat)"
command -v jq >/dev/null 2>&1 || exit 0
tool="$(printf '%s' "$input" | jq -r '.tool_name // ""')"
cwd="$(printf '%s' "$input" | jq -r '.cwd // "."')"
HOOKS="$(cd "$(dirname "$0")" && pwd)"

sealed() { printf '%s' "$1" | grep -Eq '(^|[/[:space:]"'"'"'=])eval/holdout'; }
. "$HOOKS/lib.sh"
deny() { (cd "$cwd" 2>/dev/null && hook_log check-paths "$1"); echo "$1" >&2; exit 2; }
SEAL_MSG="Holdout запечатан: его читает только make eval-final на заморозке (CLAUDE.md, «Режимы»). Работай по eval/cases и make eval."

case "$tool" in
  Read|Edit|Write|MultiEdit|NotebookEdit)
    path="$(printf '%s' "$input" | jq -r '.tool_input.file_path // .tool_input.notebook_path // ""')"
    sealed "$path" && deny "$SEAL_MSG"
    ;;
  Grep|Glob)
    q="$(printf '%s' "$input" | jq -r '[.tool_input.path, .tool_input.pattern, .tool_input.glob] | map(select(. != null)) | join(" ")')"
    sealed "$q" && deny "$SEAL_MSG"
    ;;
  Bash)
    c="$(printf '%s' "$input" | hook_command)"
    sealed "$c" && deny "$SEAL_MSG"
    ;;
  apply_patch)
    while IFS= read -r p; do sealed "$p" && deny "$SEAL_MSG"; done < <(printf '%s' "$input" | hook_command | patch_paths)
    ;;
esac

# Новый md в режиме «финал».
new_md() {
  local path="$1" dir top mode
  case "$path" in *.md) ;; *) return 0 ;; esac
  path="${path//\\//}" # Windows: C:\a\b.md → C:/a/b.md
  case "$path" in /* | [A-Za-z]:/*) ;; *) path="$cwd/$path" ;; esac # C:/… — тоже абсолютный (#191)
  [ -e "$path" ] && return 0
  case "/$path" in */README.md|*/.arch/*|*/_arch/*|*/scratchpad/*) return 0 ;; esac
  dir="$(dirname "$path")"
  while [ ! -d "$dir" ] && [ "$(dirname "$dir")" != "$dir" ]; do dir="$(dirname "$dir")"; done
  top="$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null)" || return 0
  # .claude/ — относительно корня worktree: .claude/worktrees/<N>-x/docs/a.md — это docs/a.md.
  # Путь от корня — через --show-prefix каталога: корень git (C:/…) и путь (/tmp/…) на Windows пишутся по-разному (#191).
  case "$(git -C "$dir" rev-parse --show-prefix)${path#"$dir"/}" in .claude/*) return 0 ;; esac
  mode="$(cd "$top" && bash "$top/scripts/mode.sh" 2>/dev/null || bash "$HOOKS/../../scripts/mode.sh")"
  if [ "$mode" = финал ]; then
    deny "Режим «финал»: новые md-файлы запрещены, кроме README.md. Отчёт — в чат, итог — в описание PR (CLAUDE.md, «Документы»)."
  fi
  return 0
}
case "$tool" in
  Write) new_md "$(printf '%s' "$input" | jq -r '.tool_input.file_path // ""')" ;;
  apply_patch) while IFS= read -r p; do new_md "$p"; done < <(printf '%s' "$input" | hook_command | patch_paths added) ;;
esac
exit 0
