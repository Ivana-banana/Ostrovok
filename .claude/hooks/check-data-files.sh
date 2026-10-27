#!/usr/bin/env bash
# Страж коммита: данные и крупные файлы не попадают в git мимо Git LFS.
# Проверяет индекс (staged):
#   - файл в data/ или outputs/ (кроме README.md и .gitkeep) вне LFS — отказ: данные лежат
#     вне git, загрузка — командой из README, сырые данные коллектора — в GitHub Releases; маленькие примеры — в tests/fixtures/;
#   - любой файл ≥ MAX_FILE_MB (по умолчанию 5) вне LFS — отказ: git lfs track + .gitattributes.
# Режимы:
#   pre-tool — PreToolUse на Bash: срабатывает только на `git commit`, отказывает JSON-ом;
#   (без аргумента) — .githooks/pre-commit и ручной запуск: код 1 при нарушении.
set -uo pipefail

mode="${1:-git}"
if [ "$mode" = pre-tool ]; then
  input="$(cat)"
  # Codex может прислать команду массивом argv (lib.sh, hook_command)
  cmd="$(printf '%s' "$input" | jq -r '.tool_input.command // "" | if type == "array" then join(" ") else tostring end' 2>/dev/null)"
  printf '%s' "$cmd" | grep -qE '(^|[;&|[:space:]])git([[:space:]]+-C[[:space:]]+[^[:space:]]+)?[[:space:]]+commit' || exit 0
  dir="$(printf '%s' "$cmd" | sed -nE 's/.*git[[:space:]]+-C[[:space:]]+([^[:space:]]+).*/\1/p' | tr -d "\"'")"
  [ -z "$dir" ] && dir="$(printf '%s' "$cmd" | sed -nE 's/^[[:space:]]*cd[[:space:]]+([^[:space:];&]+).*/\1/p' | tr -d "\"'")"
  [ -z "$dir" ] && dir="$(printf '%s' "$input" | jq -r '.cwd // ""' 2>/dev/null)"
  [ -n "$dir" ] && { cd "$dir" 2>/dev/null || exit 0; }
fi
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
cd "$(git rev-parse --show-toplevel)" || exit 0

MAX_MB="${MAX_FILE_MB:-5}"
FOUND=""
while IFS= read -r -d '' f; do
  [ -f "$f" ] || continue
  lfs="$(git check-attr filter -- "$f" | awk '{print $NF}')"
  [ "$lfs" = lfs ] && continue
  case "$f" in
    data/README.md|outputs/README.md|*/.gitkeep|.gitkeep) ;;
    data/*|outputs/*)
      FOUND+="$f: данные в git не кладём (загрузка — по README; сырые данные коллектора — в GitHub Releases; пример — в tests/fixtures/)"$'\n'
      continue ;;
  esac
  size=$(( $(wc -c < "$f") / 1024 / 1024 ))
  if [ "$size" -ge "$MAX_MB" ]; then
    FOUND+="$f: ${size} МБ ≥ ${MAX_MB} МБ вне Git LFS (git lfs track '<шаблон>' и .gitattributes)"$'\n'
  fi
done < <(git diff --cached --name-only -z --diff-filter=AM)

[ -z "$FOUND" ] && exit 0

MSG="Коммит остановлен стражем данных:"$'\n'"$FOUND""Убрать из индекса: git restore --staged <путь>."
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"; hook_log check-data-files "$FOUND"
if [ "$mode" = pre-tool ]; then
  jq -n --arg r "$MSG" '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $r}}'
  exit 0
fi
printf '%s\n' "$MSG" >&2
exit 1
