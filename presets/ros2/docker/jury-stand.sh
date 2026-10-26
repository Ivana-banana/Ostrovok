#!/usr/bin/env bash
# Стенд жюри: лимиты ресурсов и никакой сети. Шаги: офлайн colcon build → нода по launch
# → (если дан bag) ros2 bag play → проверка, что нода ответила.
#   bash docker/jury-stand.sh <образ> [каталог bag]
# Лимиты: STAND_CPUS (2), STAND_MEMORY (512m) — из условий, правьте по брифу.
set -euo pipefail
IMAGE="$1"; BAG="${2:-}"
ROOT="$(git rev-parse --show-toplevel)"
OUT="$ROOT/out/stand"; mkdir -p "$OUT"
BAG_MOUNT=()
if [ -n "$BAG" ]; then
  case "$BAG" in /*) ;; *) BAG="$ROOT/$BAG" ;; esac
  [ -d "$BAG" ] || { echo "нет bag: $BAG" >&2; exit 1; }
  BAG_MOUNT=(-v "$BAG:/bag:ro")
fi
docker run --rm --cpus="${STAND_CPUS:-2}" --memory="${STAND_MEMORY:-512m}" \
  --memory-swap="${STAND_MEMORY:-512m}" --network=none --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$ROOT/src:/src:ro" -v "$ROOT/docker:/stand:ro" -v "$OUT:/out" "${BAG_MOUNT[@]}" \
  "$IMAGE" bash /stand/stand-inside.sh
echo "стенд: логи в $OUT"
