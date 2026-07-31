# API and UI

Load this reference when generating web APIs, operator dashboards, or internal web tools.

## API Defaults (FastAPI)

- Use `FastAPI` with explicit request/response models.
- Keep schema types in dedicated modules (`schemas.py` or `api_models.py`).
- Use dependency injection for auth, DB/session, and external clients.
- Add `/healthz` and `/readyz` endpoints.
- Add request timeout, retry, and circuit-breaker boundaries around external calls.
- Return stable error payloads with trace/request IDs.

## API Security Baseline

- Require auth for non-health endpoints.
- Keep CORS explicit; avoid wildcard origins in production.
- Enforce input size limits and content-type checks.
- Validate and normalize all user input.
- Keep secrets in environment/secret manager only.
- Add rate limiting for internet-facing endpoints.

## API Runtime Recommendations

- Local dev: `uvicorn <package>.api:app --reload`
- Production: managed ASGI process (`gunicorn` with `uvicorn` workers or equivalent supervisor)
- Emit JSON logs in production with request IDs.

## UI Strategy

Choose based on target:

- Internal ops UI: Streamlit/Gradio/FastAPI templates are acceptable for speed.
- Public-facing React/TypeScript/Vite UI: route source scaffolding to
  `frontend-project` and keep Python as the API backend. Container files remain
  owned by `container`.

For Python-based UI apps:

- Put UI in `src/<package_name>/ui.py`.
- Keep business logic in shared service modules.
- Authenticate users before operational actions.
- Do not expose raw stack traces to end users.

## Suggested Layout

```text
src/<package_name>/
├── api.py
├── ui.py
├── api_models.py
├── dependencies.py
├── security.py
└── services/
```

## Minimal Tests

- API happy-path and auth failure tests.
- Schema validation tests for invalid payloads.
- One smoke test for each critical endpoint.
