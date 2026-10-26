#!/usr/bin/env bash
# PreToolUse(Edit|Write|MultiEdit): правка существующего конфига линтера или форматтера —
# вопрос человеку. Агент ослабляет конфиг, чтобы проверка прошла (CLAUDE.md, «Проверка»).
# Новый конфиг не спрашиваем: его создают при setup.
set -uo pipefail

input="$(cat)"
command -v jq >/dev/null 2>&1 || exit 0
f="$(printf '%s' "$input" | jq -r '.tool_input.file_path // ""')"
[ -n "$f" ] && [ -e "$f" ] || exit 0
name="${f##*[/\\]}"
printf '%s' "$name" | grep -Eq '^(\.eslintrc(\..+)?|eslint\.config\..+|\.prettierrc(\..+)?|prettier\.config\..+|biome\.jsonc?|\.?ruff\.toml|\.flake8|mypy\.ini|\.pylintrc|\.stylelintrc(\..+)?|stylelint\.config\..+|\.golangci\.ya?ml|\.clang-tidy)$' || exit 0

. "$(cd "$(dirname "$0")" && pwd)/lib.sh"
hook_log check-config "$name"
jq -n --arg n "$name" '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "ask",
  permissionDecisionReason: "Конфиг линтера \($n): чини код, а не конфиг. Разреши, только если неверно само правило."}}'
