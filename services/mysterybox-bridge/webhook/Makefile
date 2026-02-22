VENV ?= .venv
PYTHON ?= $(VENV)/bin/python

.PHONY: install fmt lint test build check

install:
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

fmt:
	$(PYTHON) -m ruff format src tests

lint:
	$(PYTHON) -m ruff check src tests

test:
	$(PYTHON) -m pytest -q tests

build:
	$(PYTHON) -m build --wheel

check: lint test
