#!/usr/bin/env bash
# Копирует presets/<имя>/ в корень репозитория (вызывает `make setup PRESET=<имя>`).
# Существующие файлы не перезаписывает. README.md пресета не копируется. Файлы
# <имя>.append дописываются в .<имя> корня (gitignore, gitattributes, env.example) один раз.
#   bash scripts/setup-preset.sh <python-ml|web-fullstack|ros2>
set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
name="${1:?имя пресета}"
src="presets/$name"
[ -d "$src" ] || { echo "нет пресета $name; есть: $(ls presets | tr '\n' ' ')" >&2; exit 2; }
if [ -f stack.mk ]; then
  have="$(sed -n 's/^STACK *:*= *//p' stack.mk | head -1)"
  [ "$have" = "$name" ] || { echo "стек уже выбран: ${have:-свой stack.mk}. Второй пресет поверх не ставим." >&2; exit 2; }
fi

copied=0 skipped=0
while IFS= read -r -d '' f; do
  rel="${f#./}"
  case "$rel" in README.md|*.append) continue ;; esac
  if [ -e "$rel" ]; then skipped=$((skipped + 1)); continue; fi
  mkdir -p "$(dirname "$rel")"
  cp -p "$src/$rel" "$rel"
  copied=$((copied + 1))
done < <(cd "$src" && find . -type f \
  -not -path '*/node_modules/*' -not -path '*/.venv/*' -not -path '*/__pycache__/*' -print0)

for a in "$src"/*.append; do
  [ -f "$a" ] || continue
  target=".$(basename "$a" .append)"
  marker="# --- preset $name"
  grep -qxF "$marker" "$target" 2>/dev/null && continue
  { echo; echo "$marker"; cat "$a"; } >> "$target"
done
[ -f .env ] && [ -f "$src/env.example.append" ] && ! grep -qxF "# --- preset $name" .env \
  && { echo; echo "# --- preset $name"; cat "$src/env.example.append"; } >> .env

echo "пресет $name: скопировано файлов $copied, уже были $skipped; дальше — make check"
