# Пресет web-fullstack

`make setup PRESET=web-fullstack` копирует этот каталог в корень (без этого README).

| Цель | Что делает |
|---|---|
| `setup` | `uv sync --frozen` в `backend/`, `npm ci` в `web/` |
| `lint` | ruff, `tsc --noEmit`, `vite build` |
| `check` | lint + pytest + vitest |
| `demo` | сборка фронтенда, FastAPI на `:8000` отдаёт API и страницу |
| `eval` / `eval-final` | `scripts/eval_harness.py` с проверками `backend/src/api/checks.py` |
| `e2e` | Playwright: сценарий демо через настоящий бэкенд (ставит chromium при первом запуске) |

Контракт API — `backend/src/api/contracts.py` и зеркало `web/src/contracts.ts`.
Демо-путь на заглушке (`api/model.py`, поле `stub`) работает с первого коммита и честно
подписан. Docker: `docker compose up --build` → <http://localhost:8000>.
