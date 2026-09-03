# IaC, Automation, Security, Networking

Load this reference when infrastructure and production operations are in scope.

## IaC Integration

- Keep app code and IaC in the same repo only when lifecycle coupling is intentional.
- Use `infra/terraform/` for environment modules and remote state.
- Avoid embedding provider credentials in code or Terraform files.
- For full Terraform scaffolding, route to `$terraform` skill.

## Automation Baseline

- Set `.DEFAULT_GOAL := all` in `Makefile`.
- Add `Makefile` targets for `all`, `fmt`, `lint`, `test`, `test-unit`, `test-integration`, `coverage`, `build`, `check`.
- When shipping containers, publish immutable build tags (`sha-*` and release+sha tags).
- For Helm-delivered services, keep chart `version` independent from app SemVer.
- Add `.pre-commit-config.yaml` with at least Ruff and whitespace/end-of-file hooks.
- Add CI pipeline for:
  - Lint
  - Fast unit tests for pull requests
  - Build artifact for pull requests
  - Integration tests for release/manual runs
  - Coverage for release/manual runs
  - Security scanning
- Keep pipelines deterministic. Pin third-party actions to reviewed immutable
  references and pin the uv version used to validate `uv.lock`.

## Security Baseline

- Dependency scanning: `uv run --locked pip-audit` (or equivalent) in CI.
- Static analysis: Ruff rules plus optional Bandit/Semgrep.
- Secret scanning: detect-secrets/Gitleaks in CI.
- Generate SBOM for release artifacts when required by policy.
- Never log secrets, tokens, raw credentials, or private keys.
- Use least-privilege IAM and short-lived credentials.

## Networking Baseline

- Set explicit connect/read/write timeouts on all outbound calls.
- Use retry with bounded exponential backoff and jitter.
- Make retries idempotent (or guarded by idempotency keys).
- Enforce TLS verification; do not disable certificate checks.
- Reuse HTTP client sessions/connection pools.
- Validate and normalize CIDR/host inputs before applying network changes.

## Runtime Ops

- Add health checks and startup self-tests.
- Emit structured logs with correlation IDs.
- Export metrics for latency/error-rate/retry counts.
- Prefer production deployments pinned by container digest.
- Document runbooks for common failure modes and rollback steps.
