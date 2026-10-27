# Пресет web-fullstack: FastAPI (uv, pytest, ruff) + React/Vite/TS (Vitest, Playwright).
# Демо-путь живёт с первого коммита: API на заглушке + страница + e2e сценария демо.
# Контракт целей — Makefile шаблона.
STACK := web-fullstack
NICE := nice -n 15
UV := cd backend && $(NICE) uv run --frozen
PORT ?= 8080
# Воркеры тестов: не все ядра, чтобы человек мог работать параллельно (#242).
TEST_JOBS ?= 4

.PHONY: stack-setup stack-lint stack-check stack-demo stack-eval stack-eval-final stack-e2e

stack-setup:
	cd backend && uv sync --frozen
	cd web && npm ci --no-audit --no-fund

stack-lint:
	$(UV) ruff check src tests
	$(UV) ruff format --check src tests
	cd web && npm run -s typecheck && npm run -s build

stack-check: stack-lint
	$(UV) pytest -n $(TEST_JOBS)
	cd web && $(NICE) npx vitest run --maxWorkers=$(TEST_JOBS)

stack-demo:
	cd web && npm run -s build
	@echo "демо: http://localhost:$(PORT)"
	cd backend && uv run --frozen uvicorn api.main:app --port $(PORT)

stack-eval:
	$(UV) python ../scripts/eval_harness.py --checks src/api/checks.py \
	  --cases ../eval/cases.json --baseline ../eval/baseline.json

stack-eval-final:
	@[ -n "$(HOLDOUT_DIR)" ] || { echo "stack-eval-final запускает только make eval-final" >&2; exit 2; }
	$(UV) python ../scripts/eval_harness.py --checks src/api/checks.py \
	  --holdout-dir "$(HOLDOUT_DIR)" --baseline ../eval/baseline.json

stack-e2e:
	cd web && npm run -s build && npx playwright install chromium >/dev/null && npx playwright test
