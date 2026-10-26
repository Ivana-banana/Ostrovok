#!/usr/bin/env bash
# Каждой ловушке данных — тест. Ловушки — eval/traps.json (пишет /hackathon-kickoff из
# 00b-probe.md). Тест ловушки содержит её id (TRAP-1, …) в имени или комментарии.
# Входит в `make check`. Нет eval/traps.json — молчит.
set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" || exit 0
[ -f eval/traps.json ] || exit 0
dirs=()
for d in tests src backend web; do [ -d "$d" ] && dirs+=("$d"); done
missing=""
for id in $(jq -r '.[].id' eval/traps.json | tr -d "\r"); do
  [ "${#dirs[@]}" -gt 0 ] && grep -rqsw --exclude-dir=fixtures --exclude-dir=node_modules \
    --exclude-dir=.venv -- "$id" "${dirs[@]}" && continue
  missing+=" $id"
done
if [ -n "$missing" ]; then
  echo "[traps] нет теста на ловушки:$missing (eval/traps.json; id — в имени или комментарии теста)" >&2
  exit 1
fi
echo "[traps] тест есть у каждой ловушки: $(jq length eval/traps.json | tr -d "\r")"
