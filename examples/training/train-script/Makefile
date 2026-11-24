.DEFAULT_GOAL := all
UV := uv
EXTRA ?= ml
.PHONY: all sync lock-check lint fmt test test-unit test-integration coverage build smoke-wheel check
all: check smoke-wheel
lock-check:
	$(UV) lock --check
sync: lock-check
	$(UV) sync --locked --extra $(EXTRA)
lint:
	$(UV) run --locked --extra $(EXTRA) ruff check src tests scripts
	$(UV) run --locked --extra $(EXTRA) ruff format --check src tests scripts
	bash -n submit.sh train.slurm
fmt:
	$(UV) run --locked --extra $(EXTRA) ruff format src tests scripts
test:
	$(UV) run --locked --extra $(EXTRA) pytest
test-unit:
	$(UV) run --locked --extra $(EXTRA) pytest tests/unit
test-integration:
	$(UV) run --locked --extra $(EXTRA) pytest tests/integration
coverage:
	$(UV) run --locked --extra $(EXTRA) pytest -m 'not external' --cov=nebius_train --cov-report=term-missing
build:
	$(UV) build --no-sources
smoke-wheel:
	$(UV) run --locked --extra $(EXTRA) python scripts/smoke-wheel.py
check: lock-check lint test
