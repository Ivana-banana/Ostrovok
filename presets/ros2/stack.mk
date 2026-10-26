# Пресет ros2: colcon-пакет в src/, Docker на ros:humble-ros-base, стенд жюри
# (--cpus, --memory, --network=none), чтение bag без ROS (rosbags) на хосте.
# Логика — чистый Python (app_core/core.py): тестируется на хосте без ROS.
STACK := ros2
UV := uv run --frozen
IMAGE := hackathon-ros:jury
BAG ?=

.PHONY: stack-setup stack-lint stack-check stack-demo stack-eval stack-eval-final stack-e2e stack-image

stack-setup:
	uv sync --frozen
	@command -v docker >/dev/null || echo "нет docker: colcon и стенд жюри недоступны (make check упадёт)"

stack-image:
	@command -v docker >/dev/null || { echo "нет docker: colcon build/test и стенд жюри идут только в контейнере ros:humble" >&2; exit 2; }
	docker build -q -f docker/Dockerfile -t $(IMAGE) . >/dev/null

stack-lint:
	$(UV) ruff check src tools tests
	$(UV) ruff format --check src tools tests

stack-check: stack-lint stack-image
	$(UV) pytest
	bash docker/run.sh $(IMAGE) 'colcon build --event-handlers console_cohesion- && colcon test --return-code-on-test-failure --event-handlers console_cohesion- && colcon test-result'

stack-demo: stack-image
	bash docker/jury-stand.sh $(IMAGE) $(BAG)

stack-eval:
	$(UV) python scripts/eval_harness.py --checks tools/checks.py

stack-eval-final:
	@[ -n "$(HOLDOUT_DIR)" ] || { echo "stack-eval-final запускает только make eval-final" >&2; exit 2; }
	$(UV) python scripts/eval_harness.py --checks tools/checks.py --holdout-dir "$(HOLDOUT_DIR)"

stack-e2e: stack-image
	$(UV) python tools/make_sample_bag.py out/sample_bag
	bash docker/jury-stand.sh $(IMAGE) out/sample_bag
