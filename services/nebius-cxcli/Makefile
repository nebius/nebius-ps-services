.DEFAULT_GOAL := all

VENV ?= .venv
PYTHON ?= python3
VENV_PYTHON := $(VENV)/bin/python
VENV_PIP := $(VENV)/bin/pip
VENV_STAMP := $(VENV)/.installed

.PHONY: all env venv install fmt lint test test-unit test-integration coverage build check clean

all: $(VENV_STAMP)
	$(MAKE) -j2 check build

env: $(VENV_STAMP)

venv: env

install: env

$(VENV_PYTHON):
	$(PYTHON) -m venv $(VENV)

$(VENV_STAMP): pyproject.toml | $(VENV_PYTHON)
	$(VENV_PIP) install --upgrade pip setuptools
	$(VENV_PIP) install -e ".[dev]"
	touch $(VENV_STAMP)

fmt: $(VENV_STAMP)
	$(VENV_PYTHON) -m ruff format src tests

lint: $(VENV_STAMP)
	$(VENV_PYTHON) -m ruff check src tests

test: test-unit

test-unit: $(VENV_STAMP)
	$(VENV_PYTHON) -m pytest -m "not integration"

test-integration: $(VENV_STAMP)
	@if [ -d tests/integration ] && find tests/integration -type f \( -name 'test_*.py' -o -name '*_test.py' \) | grep -q .; then \
		$(VENV_PYTHON) -m pytest -m integration tests/integration; \
	else \
		echo "No integration tests under tests/integration"; \
	fi

coverage: $(VENV_STAMP)
	$(VENV_PYTHON) -m pytest --cov=nebius_cxcli --cov-report=term-missing -m "not integration"

build: $(VENV_STAMP)
	rm -rf build dist
	$(VENV_PYTHON) -m build --wheel --no-isolation

check: lint test

clean:
	rm -rf build dist .pytest_cache .ruff_cache .coverage coverage.xml
