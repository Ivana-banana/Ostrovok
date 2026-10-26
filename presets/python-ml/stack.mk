# Пресет python-ml: uv + lockfile, ruff, pytest, eval/eval-final по сценариям, веса — Git LFS.
# Контракт целей — Makefile шаблона. Команды — только через uv run --frozen (lockfile).
STACK := python-ml
UV := uv run --frozen

.PHONY: stack-setup stack-lint stack-check stack-demo stack-eval stack-eval-final stack-e2e

stack-setup:
	uv sync --frozen
	@if command -v git-lfs >/dev/null; then git lfs install --local --skip-repo >/dev/null; \
	 else echo "git-lfs не установлен: веса в models/ не закоммитить"; fi

stack-lint:
	$(UV) ruff check src tests
	$(UV) ruff format --check src tests

stack-check: stack-lint
	$(UV) pytest

stack-demo:
	$(UV) python -m app.demo

stack-eval:
	$(UV) python scripts/eval_harness.py --checks src/app/checks.py

stack-eval-final:
	@[ -n "$(HOLDOUT_DIR)" ] || { echo "stack-eval-final запускает только make eval-final" >&2; exit 2; }
	$(UV) python scripts/eval_harness.py --checks src/app/checks.py --holdout-dir "$(HOLDOUT_DIR)"

stack-e2e:
	$(UV) pytest -m e2e
