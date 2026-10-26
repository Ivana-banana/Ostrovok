# Пресет python-ml

`make setup PRESET=python-ml` копирует этот каталог в корень (без этого README).

| Цель | Что делает |
|---|---|
| `setup` | `uv sync --frozen` по `uv.lock`, `git lfs install --local` |
| `lint` | `ruff check` и `ruff format --check` |
| `check` | lint + `pytest` (без `e2e`) |
| `demo` | `python -m app.demo` — демо-путь жюри |
| `eval` | `scripts/eval_harness.py` по `eval/cases.json` и `src/app/checks.py`, baseline из `eval/baseline.json` |
| `eval-final` | то же по запечатанному holdout; только через `make eval-final` (печать sha256, журнал) |
| `e2e` | `pytest -m e2e` |

Код — `src/app/`: `contracts.py` (границы модулей), `baseline.py` (тривиальный ответ),
`model.py` (заглушка до ADR), `checks.py` (проверки сценариев и метрика). Веса — `models/`
через Git LFS (`gitattributes.append`). Зависимость добавлять: `uv add <пакет>` — lockfile
меняется отдельным PR.
