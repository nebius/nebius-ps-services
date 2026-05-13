# Testing

Load this reference when creating or standardizing tests, pytest config, Makefiles, or CI.

## Objectives

- Keep local development tests fast enough to run constantly.
- Separate infrastructure-sensitive checks from the default unit lane.
- Centralize pytest and coverage configuration in `pyproject.toml`.
- Make CI behavior match local developer workflows.

## Default Layout

```text
tests/
├── conftest.py
├── unit/
│   ├── test_cli.py
│   ├── test_config.py
│   └── test_rendering.py
└── integration/
    └── test_cli_smoke.py
```

Use `tests/unit/` for:

- validation logic
- config loaders
- rendering/serialization
- CLI argument handling
- pure service helpers
- mocked boundaries around HTTP, cloud SDKs, subprocesses, and file IO when needed

Use `tests/integration/` for:

- end-to-end CLI smoke checks
- realistic app wiring
- filesystem-heavy flows
- container/runtime checks
- release validation that should not slow down every pull request

## Pytest Baseline

Keep pytest config minimal and central in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
addopts = "-ra --strict-config --strict-markers"
markers = [
  "integration: isolated integration tests intended for CI release validation",
]
testpaths = ["tests"]
```

Add coverage config there too:

```toml
[tool.coverage.run]
branch = true
source = ["<package_name>"]

[tool.coverage.report]
skip_empty = true
```

## Fast Unit Tests

Unit tests must not:

- access the network
- call real cloud APIs
- depend on real infrastructure
- rely on large fixtures or large datasets

Default guard in `tests/conftest.py`:

```python
from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def block_unit_test_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("integration"):
        return

    def _blocked(*args, **kwargs):
        raise AssertionError("Network access is disabled in unit tests.")

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
```

## Mocking Boundaries

Use `unittest.mock.patch` to isolate external boundaries:

```python
from unittest.mock import patch


@patch("<package_name>.clients.http.requests.get")
def test_fetch_status_uses_mocked_http(mock_get) -> None:
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {"status": "ok"}

    ...
```

Patch at the import path used by the code under test, not the library's global symbol.

Common patch targets:

- HTTP clients (`requests`, `httpx`, SDK adapters)
- cloud SDK client constructors
- `subprocess.run`
- filesystem probes or metadata lookups when the test is about decision logic rather than IO

## Makefile Pattern

Prefer explicit local targets:

<!-- markdownlint-disable MD010 -->

```make
.DEFAULT_GOAL := all

all: check build
test: test-unit
test-unit:
	python -m pytest -m "not integration" tests/unit
test-integration:
	python -m pytest -m integration tests/integration
coverage:
	python -m pytest --cov=<package_name> --cov-report=term-missing tests/unit
check: lint test-unit
```

<!-- markdownlint-enable MD010 -->

## CI Pattern

Recommended workflow split:

- Pull requests:
  - lint
  - fast unit tests
  - build
- Release tags or manual runs:
  - lint
  - unit tests
  - integration tests
  - coverage
  - packaging

Use `pytest-xdist` in CI for the fast lane when the test suite benefits from parallelism.

## Test Selection Guidance

- Add one unit test module per major pure-logic module or boundary adapter.
- Add integration tests only for meaningful cross-module behavior.
- Prefer smoke-style integration tests over large, slow scenario matrices in the default scaffold.
- Avoid generating placeholder tests that only assert `True`.

## Generic Coverage Strategy

Aim first at code with high change frequency or high regression cost:

- CLI parsing and output modes
- config/schema validation
- serialization and rendering
- diff/planning logic
- retry/timeout behavior
- build/release helpers

Leave highly environment-specific runtime code behind mocks in unit tests unless the user explicitly asks for heavier integration coverage.
