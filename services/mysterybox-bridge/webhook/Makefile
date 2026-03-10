VENV ?= .venv
PYTHON ?= $(VENV)/bin/python
.DEFAULT_GOAL := all

.PHONY: all install fmt lint test test-unit test-integration coverage build check

all: check build

install:
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

fmt:
	$(PYTHON) -m ruff format src tests

lint:
	$(PYTHON) -m ruff check src tests

test: test-unit

test-unit:
	$(PYTHON) -m pytest -q -m "not integration" tests/unit

test-integration:
	$(PYTHON) -m pytest -q -m integration tests/integration

coverage:
	$(PYTHON) -m pytest --cov=mysterybox_bridge --cov-report=term-missing tests/unit

build:
	$(PYTHON) -m build --wheel

check: lint test-unit
