#!/usr/bin/env bash
# Режим правил: «сборка» или «финал». Источник — строка «Режим: …» в CLAUDE.md.
# Правду держит main: если есть origin/main, режим берётся из его CLAUDE.md, иначе — из
# CLAUDE.md рабочей копии. Нет строки или значение не распознано — «сборка» (строже).
# HACK_MODE=сборка|финал — переопределение для тестов хуков.
#
#   bash scripts/mode.sh            # печатает «сборка» или «финал»
#   bash scripts/mode.sh --source   # то же и откуда взято: «финал origin/main»
set -uo pipefail

parse() { sed -n 's/^[*_ ]*Режим:[*_ ]*\([^*_ |]*\).*/\1/p' | head -1; }

if [ -n "${HACK_MODE:-}" ]; then
  mode="$HACK_MODE" src=env
else
  mode="" src=""
  if git rev-parse -q --verify origin/main >/dev/null 2>&1; then
    mode="$(git show origin/main:CLAUDE.md 2>/dev/null | parse)" src=origin/main
  fi
  if [ -z "$mode" ]; then
    top="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
    mode="$(parse < "$top/CLAUDE.md" 2>/dev/null)" src=CLAUDE.md
  fi
fi

case "$mode" in
  финал) ;;
  *) mode=сборка ;;
esac

if [ "${1:-}" = --source ]; then echo "$mode $src"; else echo "$mode"; fi
