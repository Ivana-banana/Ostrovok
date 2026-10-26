#!/usr/bin/env bash
# Stop hook: ход не считается завершённым, если в файлах, которые могут попасть
# в коммит (изменённые относительно HEAD и новые, не игнорируемые), лежит похожее
# на секрет значение. Ключи API и прочие секреты живут только в .env.
# Ложное срабатывание: дописать в строку комментарий `secret-scan: allow`.
#
# Хук запускается в конце каждого хода каждой сессии. Claude Code ждёт его 30 с и
# бросает, а процесс остаётся — на почте копии копились сотнями. Поэтому: одна копия
# на рабочую копию (lock), поиск только в изменённых файлах, свой предел времени.
# Не успел — молчит: секреты при коммите ещё раз ловит .githooks/pre-commit.
# Аргумент --git — режим pre-commit: проверяет индекс, нарушение — код 1 и текст в stderr.
set -uo pipefail

LIMIT_S=20
MODE="${1:-stop}"

GIT_DIR="$(git rev-parse --git-dir 2>/dev/null)" || exit 0
cd "$(git rev-parse --show-toplevel)" || exit 0
# Lock — файл с PID, только встроенные команды bash: в Git Bash на Windows запуск
# mkdir/rmdir/stat занимал секунды. Жив прежний процесс — выходим. Убитый
# процесс оставляет файл, но `kill -0` его не находит, и запуск идёт.
LOCK="$GIT_DIR/check-secrets.pid"
if read -r OTHER 2>/dev/null < "$LOCK" && [ -n "$OTHER" ] && [ "$OTHER" != "$$" ] && kill -0 "$OTHER" 2>/dev/null; then
  exit 0
fi
echo "$$" >| "$LOCK" 2>/dev/null

if [ "$MODE" = --git ]; then
  mapfile -t FILES < <(git diff --cached --name-only --diff-filter=AM | grep -v '^\.claude/hooks/' | sort -u)
else
  mapfile -t FILES < <(
    { git diff --name-only HEAD 2>/dev/null; git ls-files -o --exclude-standard 2>/dev/null; } \
      | grep -v '^\.claude/hooks/' | sort -u
  )
fi
[ "${#FILES[@]}" -eq 0 ] && exit 0

FOUND=""

for f in "${FILES[@]}"; do
  case "${f##*/}" in
    .env.example) ;;
    .env|.env.*) FOUND+="$f: файл окружения не в .gitignore"$'\n' ;;
  esac
done

EXISTING=()
for f in "${FILES[@]}"; do [ -f "$f" ] && EXISTING+=("$f"); done

if [ "${#EXISTING[@]}" -gt 0 ]; then
  HITS="$(timeout "$LIMIT_S" grep -I -n -i -E \
      -e '-----BEGIN [A-Z ]*PRIVATE KEY-----' \
      -e '(token|secret|password|passwd|api[_-]?key)["'"'"']?[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9_.+/=-]{16,}' \
      -e 'gh[pousr]_[A-Za-z0-9]{36}' \
      -e 'sk-[A-Za-z0-9_-]{20,}' \
      -- "${EXISTING[@]}" 2>/dev/null \
    | grep -v -i -E 'secret-scan: allow|your|example|changeme|placeholder|xxxx|<[a-z_]+>|\$\{|process\.env|os\.environ|getenv' \
    | cut -d: -f1,2)"
  [ -n "$HITS" ] && FOUND+="$(printf '%s' "$HITS" | sed 's/$/: похоже на секрет/')"$'\n'
fi

[ -z "$FOUND" ] && exit 0
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"; hook_log check-secrets "$FOUND"

if [ "$MODE" = --git ]; then
  printf 'Коммит остановлен: возможные секреты (значения не выводятся):\n%sВынести в .env; ложное срабатывание — `secret-scan: allow` в строке.\n' "$FOUND" >&2
  exit 1
fi
jq -n --arg found "$FOUND" \
  '{decision: "block", reason: ("Возможные секреты в файлах, которые попадут в коммит (значения не выводятся):\n" + $found + "Вынести в .env (в .gitignore) и .env.example с плейсхолдером. Ложное срабатывание — пометить строку `secret-scan: allow`.")}'
exit 0
