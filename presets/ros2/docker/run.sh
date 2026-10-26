#!/usr/bin/env bash
# Команда в контейнере ros:humble с копией src/ (рабочее дерево не пачкаем build/ и install/).
#   bash docker/run.sh <образ> '<команда>'
set -euo pipefail
IMAGE="$1"; CMD="$2"
ROOT="$(git rev-parse --show-toplevel)"
docker run --rm --network=none --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$ROOT/src:/src:ro" -v "$ROOT/docker:/stand:ro" "$IMAGE" bash -c "
    set -e; source /opt/ros/humble/setup.bash
    mkdir -p /tmp/ws && cp -r /src /tmp/ws/src && cd /tmp/ws
    export COLCON_DEFAULTS_FILE=/stand/colcon-defaults.yaml
    $CMD"
