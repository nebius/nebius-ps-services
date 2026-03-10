# mysterybox-bridge-webhook

Python webhook service for `mysterybox-bridge`.

## Local Dev

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m ruff check src tests
python -m pytest -q -m "not integration" tests/unit
python -m pytest -q -m integration tests/integration
```

Or run:

```bash
make install
make check
make test-integration
make coverage
```

## Run Locally

```bash
mysterybox-bridge-webhook
```

## Runtime Notes

- `/healthz`: liveness endpoint.
- `/readyz`: readiness endpoint with a lightweight cached Nebius auth/API check.
- `MYSTERYBOX_READINESS_CACHE_TTL` (default: `30`) controls readiness check cache duration.
