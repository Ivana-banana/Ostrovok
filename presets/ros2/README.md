# Пресет ros2

`make setup PRESET=ros2` копирует этот каталог в корень (без этого README).

| Цель | Что делает |
|---|---|
| `setup` | `uv sync --frozen` — хост: rosbags, pytest, ruff |
| `lint` | ruff по `src`, `tools`, `tests` |
| `check` | lint + pytest на хосте + `colcon build/test` в `ros:humble-ros-base` без сети |
| `demo` | стенд жюри: `--cpus`, `--memory`, `--network=none`; `BAG=<каталог>` — проиграть bag |
| `eval` / `eval-final` | `scripts/eval_harness.py` с проверками `tools/checks.py` |
| `e2e` | sample bag (rosbags, без ROS) → стенд жюри → нода ответила на `/result` |

Логика — `src/app_core/app_core/core.py` (без ROS, тестируется на хосте); нода —
обёртка `node.py`. Чтение bag без ROS — `tools/bag_read.py`. Лимиты стенда —
`STAND_CPUS`, `STAND_MEMORY`; правьте по брифу. Docker обязателен для `check`.
