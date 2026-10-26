#!/usr/bin/env bash
# Общее для хуков. hook_log <хук> <причина> — строка в журнал отказов
# <git-common-dir>/hook-refusals.log (один на машину, общий для всех worktree, вне git).
# Журнал читает /retro: какие правила срабатывали и сколько раз.
hook_log() {
  local d
  d="$(git rev-parse --git-common-dir 2>/dev/null)" || return 0
  printf '%s\t%s\t%s\t%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$1" \
    "$(git branch --show-current 2>/dev/null)" "$(printf '%s' "$2" | tr '\n\t' '  ' | cut -c1-300)" \
    >> "$d/hook-refusals.log" 2>/dev/null || true
}

# Вход хуков Codex (.codex/hooks.json) — тот же JSON, что у Claude Code, с двумя отличиями:
#   - правка файла — tool_name "apply_patch", текст патча в tool_input.command;
#   - tool_input.command у Bash может прийти массивом argv.
# hook_command — команда Bash (или текст патча) строкой. Вход — JSON на stdin.
hook_command() {
  jq -r '.tool_input.command // "" | if type == "array" then join(" ") else tostring end'
}

# patch_paths — пути файлов из патча apply_patch (stdin), по одному в строке:
# «*** Add File: p», «*** Update File: p», «*** Delete File: p», «*** Move to: p».
# patch_paths added — только новые файлы (Add File, Move to).
patch_paths() {
  if [ "${1:-}" = added ]; then
    sed -nE 's/^\*\*\* (Add File|Move to): (.+)$/\2/p'
  else
    sed -nE 's/^\*\*\* (Add File|Update File|Delete File|Move to): (.+)$/\2/p'
  fi | sed -E 's/[[:space:]]+$//'
}
