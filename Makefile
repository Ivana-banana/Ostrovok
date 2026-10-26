# Единый интерфейс сборки. Агенты, jury и submission-checklist вызывают только эти цели.
# Цели наполняет стек: stack.mk в корне (его копирует `make setup PRESET=<имя>`; стек с нуля —
# свой stack.mk по тому же контракту, README, «Стек с нуля»).
#
# Контракт stack.mk: STACK := <имя>; цели stack-setup, stack-lint, stack-check, stack-demo,
# stack-eval, stack-eval-final (получает HOLDOUT_DIR — распакованный holdout), stack-e2e.
SHELL := bash
PRESET ?=
empty :=
space := $(empty) $(empty)
PRESETS := $(subst $(space),|,$(notdir $(wildcard presets/*)))

-include stack.mk

define need_stack
	@[ -f stack.mk ] || { echo "стек не выбран: make setup PRESET=<$(PRESETS)> или свой stack.mk (README)" >&2; exit 2; }
endef

.PHONY: help setup lint check demo eval eval-final e2e hooks-test kickoff-test codex-sync codex-test template-test

help:            ## список целей
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:[^#]*## /\t/' | sort

setup:           ## окружение; первый раз: make setup PRESET=<python-ml|web-fullstack|ros2>
	@git config core.hooksPath .githooks 2>/dev/null || true
	@[ -f .env ] || cp .env.example .env
ifneq ($(PRESET),)
	@bash scripts/setup-preset.sh $(PRESET)
	@$(MAKE) --no-print-directory stack-setup
else
	@if [ -f stack.mk ]; then $(MAKE) --no-print-directory stack-setup; \
	 else echo "хуки подключены; стек не выбран: make setup PRESET=<$(PRESETS)>"; fi
endif

lint:            ## сборка и lint без тестов (finish.sh в режиме «финал»); без стека — синтаксис скриптов шаблона
	@if [ -f stack.mk ]; then $(MAKE) --no-print-directory stack-lint; else \
	  echo "стек не выбран — lint скриптов шаблона"; \
	  for f in scripts/*.sh .claude/hooks/*.sh .githooks/*; do bash -n "$$f" || exit 1; done; \
	  python3 -m py_compile .claude/skills/hackathon-kickoff/kickoff.py scripts/codex-sync.py; fi

check:           ## lint + тесты + тест на каждую ловушку (режим «сборка»: перед PR, finish.sh); без стека — template-test
	@if [ -f stack.mk ]; then bash scripts/check-traps.sh && $(MAKE) --no-print-directory stack-check; else \
	  echo "стек не выбран — проверяю сам шаблон (make template-test)"; $(MAKE) --no-print-directory template-test; fi

demo:            ## поднять демо-путь (то, что увидит жюри)
	$(need_stack)
	@$(MAKE) --no-print-directory stack-demo

eval:            ## видимые eval-кейсы + baseline (тривиальный ответ из probe)
	$(need_stack)
	@$(MAKE) --no-print-directory stack-eval

eval-final:      ## запечатанный holdout: только режим «финал», чистое дерево, журнал
	$(need_stack)
	@bash scripts/eval-final.sh

e2e:             ## сквозной сценарий демо в роли пользователя
	$(need_stack)
	@$(MAKE) --no-print-directory stack-e2e

hooks-test:      ## тесты хуков и scripts/*.sh
	@bash .claude/hooks/test-hooks.sh

kickoff-test:    ## сухой прогон /hackathon-kickoff на tests/fixtures/_arch
	@bash .claude/skills/hackathon-kickoff/test-kickoff.sh

codex-sync:      ## слой Codex (.codex/agents, .agents/skills) из .claude/ — после правки роли или скилла
	@python3 scripts/codex-sync.py

codex-test:      ## слой Codex совпадает с .claude/; .codex/hooks.json ссылается на живые скрипты
	@python3 scripts/codex-sync.py --check
	@jq -e . .codex/hooks.json >/dev/null && jq -r '.. | .command? // empty' .codex/hooks.json \
	  | grep -oE '\.claude/hooks/[a-z-]+\.sh' | sort -u | while read -r f; do \
	    [ -x "$$f" ] || { echo "в .codex/hooks.json нет скрипта $$f" >&2; exit 1; }; done; \
	  echo ".codex/hooks.json: скрипты на месте"

template-test: hooks-test kickoff-test codex-test  ## всё, что проверяет сам шаблон
