.DEFAULT_GOAL := all

VENV ?= .venv
PYTHON ?= python3
VENV_PYTHON := $(VENV)/bin/python
VENV_PIP := $(VENV)/bin/pip
VENV_STAMP := $(VENV)/.installed

.PHONY: all env install fmt lint test test-unit test-integration coverage build check clean

all: check build

env: $(VENV_STAMP)

install: env

$(VENV_PYTHON):
	$(PYTHON) -m venv $(VENV)

$(VENV_STAMP): pyproject.toml Makefile | $(VENV_PYTHON)
	$(VENV_PIP) install --upgrade pip
	$(VENV_PIP) install -e ".[dev]"
	touch $(VENV_STAMP)

fmt: $(VENV_STAMP)
	$(VENV_PYTHON) -m ruff format src tests

lint: $(VENV_STAMP)
	$(VENV_PYTHON) -m ruff check src tests

test: test-unit

test-unit: $(VENV_STAMP)
	$(VENV_PYTHON) -m pytest -m "not integration" tests/unit

test-integration: $(VENV_STAMP)
	$(VENV_PYTHON) -m pytest -m integration tests/integration

coverage: $(VENV_STAMP)
	$(VENV_PYTHON) -m pytest --cov=github_report --cov-report=term-missing tests/unit

build: $(VENV_STAMP)
	rm -rf build dist
	$(VENV_PYTHON) -m build --wheel

check: lint test-unit

clean:
	rm -rf $(VENV) build dist .pytest_cache .ruff_cache .coverage coverage.xml
