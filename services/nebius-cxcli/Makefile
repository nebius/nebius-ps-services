.DEFAULT_GOAL := all

VENV ?= .venv
PYTHON ?= python3
VENV_PYTHON := $(VENV)/bin/python
VENV_PIP := $(VENV)/bin/pip

.PHONY: all install fmt lint test build check

all: check build

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PIP) install --upgrade pip
	$(VENV_PIP) install -e ".[dev]"

fmt:
	$(VENV_PYTHON) -m ruff format src tests

lint:
	$(VENV_PYTHON) -m ruff check src tests

test:
	$(VENV_PYTHON) -m pytest

build:
	rm -rf build dist
	$(VENV_PYTHON) -m build --wheel

check: lint test
