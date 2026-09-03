---
name: python-project
description: "Scaffold or harden Python repos with uv, pyproject.toml/uv.lock, src layout, Ruff, pytest, CLIs, services, and CI. Preserve an existing package manager unless uv migration is requested; route domain infrastructure elsewhere."
---

# Python Project

## Help

For `$python-project --help` or `$python-project -h`, return concise help and stop before
any workflow step. State the purpose and invocation policy. Show exact usage
for every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

## Purpose

Scaffold production-grade Python repositories with conservative, uv-first
defaults and one authoritative dependency workflow.

## Invocation Scope

Determine scope before generating files:

- `standalone`: own a complete Python repository root and retain the normal
  output contract below.
- `coordinated-candidate`: receive an assigned Python component path, exact
  owned paths, root exclusions, and private bundle from `scaffold-project`.
  Generate exact candidate bytes only in that bundle and never write the
  target.

In coordinated-candidate scope, do not create or modify repository-root
`.gitignore`, README, Makefile, `.github/`, `infra/`, Helm charts, Dockerfiles,
Compose, standalone frontend code, or agent instructions. Return root
integration requirements to the coordinator. A component-local README,
Makefile, tests, and Python configuration are allowed only when explicitly
assigned. Never generate, template, or hand-edit `uv.lock` in a candidate
bundle; report dependency resolution and lock generation as a pending
post-integration step for separate authorization.

## Use This Skill For

- Creating a new Python project from scratch.
- Standardizing an existing Python repo layout and tooling.
- Adding or improving:
  - CLI applications
  - systemd services/timers
  - API services and UI apps
  - IaC and automation integration
  - security and networking controls
  - AI/ML pipelines and model-serving structure

## Defaults

- Use one canonical uv project workflow for new scaffolds. Existing projects
  retain their current package manager unless the user requests migration; an
  authorized migration removes dual install and lock authorities unless the
  user explicitly requires them.
- `pyproject.toml` with PEP 621 metadata.
- `pyproject.toml` declares supported Python, direct dependencies, development
  groups, consumer extras, and approved sources. Keep profile dependencies out
  of the scaffold until their profile is selected.
- Generate `uv.lock` with uv, commit and review it, and never hand-edit or
  template it. Treat requirements files as derived exports for external
  consumers, not a second source of truth.
- Prefer `pyproject.toml` as the single packaging metadata source. Add a minimal
  `setup.py` compatibility shim only when the user explicitly requests it for a
  named existing build or release entrypoint.
- `setuptools` + `setuptools-scm` for build/versioning.
- `src/` package layout.
- If the package exposes `__version__`, a runtime resolver that prefers live
  SCM state in source checkouts and falls back to metadata/generated version
  files for installed artifacts.
- `ruff` for linting/formatting checks.
- `pytest` for tests.
- Split test layout:
  - `tests/unit/` for fast local development tests.
  - `tests/integration/` for isolated release/CI validation.
  - `tests/conftest.py` for shared fixtures and network guards.
- `Typer` + `Rich` for CLI UX.
- `Pydantic` for config/schema validation.
- `Makefile` with `.DEFAULT_GOAL := all` and aggregate `all` target (for example `all: check build`).
- CI pattern:
  - Pull requests: `lint`, fast unit tests, `build`.
  - Release/manual runs: `lint`, `unit`, `integration`, `coverage`, `packaging`.
- Optional packaged systemd assets in `src/<package>/systemd/`.

## Workflow

1. Gather missing essentials only:
   - Project name (distribution) and import package name.
   - Project kind: deployable application or published library.
   - Existing package manager and lock authority, plus whether migration to uv
     is explicitly requested.
   - Python version range (default: `>=3.11,<3.14`).
   - Workload profile(s): `cli`, `systemd`, `api`, `ui`, `iac`, `automation`, `security`, `networking`, `ai-ml`.
   - When `systemd` is selected, the fully qualified service module. Generate
     that importable module under `src/`; do not assume an `agent.main` path.
   - Runtime target (local VM, container, Kubernetes, hybrid).
2. Start from:
   - `references/base-layout.md`
   - `references/dependency-management.md`
   - `references/testing.md`
   - `assets/pyproject.toml.template`
   - `assets/Makefile.template`
   - `assets/tests-conftest.py.template`
3. Load only relevant profile references:
   - `references/cli-systemd.md`
   - `references/api-ui.md`
   - `references/iac-automation-security-networking.md`
   - `references/ai-ml.md`
   - `references/testing.md` when adding or standardizing tests
4. Generate scaffolding and output in this order:
   - Directory tree
   - Full file contents (one file at a time)
   - Exact locked bootstrap/lint/test/build/run commands
   - Dependency-resolution result or an explicit pending reason
   - Security + operations checklist
5. Keep placeholders (`TODO`) for environment-specific values and never invent secrets.

For dependency additions, removals, upgrades, migration, stale-lock failures,
or resolver conflicts, read `references/dependency-management.md` and follow
its mutation, application-versus-library, index-trust, and failure-preservation
rules. Inspect `uv self version`, concise command help, and long-form
`uv help <command>` before using version-sensitive options.

## Output Contract

In standalone scope, always include:

- `pyproject.toml`
- For new or migrated uv projects, a committed `uv.lock` generated by uv after
  final dependency selection. If safe resolution was not run, report it as
  pending instead of synthesizing the file.
- `.gitignore`
- `README.md`
- `src/<package>/__init__.py`
- `src/<package>/__main__.py`
- `tests/conftest.py`
- `tests/unit/`
- `tests/integration/`

Add these when selected:

- `cli`: `src/<package>/cli.py` and `[project.scripts]`.
- `systemd`: an importable source module matching the selected service module,
  `src/<package>/systemd/*.service`, optional `*.timer`, and package-data
  configuration.
  - Provision the service environment with a locked uv sync and bind
    `ExecStart` to the exact project-owned `.venv` interpreter and selected
    module, never ambient Python or an assumed module path.
  - Add service arguments only when the matching parser is generated and
    tested.
- `api`: `src/<package>/api.py` (ASGI app) and production run guidance.
- `ui`: `src/<package>/ui.py` and auth/network boundary notes.
- `iac`: `infra/terraform/` (or point to `$terraform` skill for full scaffolding).
- `automation`: `Makefile`, `.github/workflows/ci.yml`, `.pre-commit-config.yaml`.
  - `Makefile` must set `.DEFAULT_GOAL := all`.
  - `Makefile` must include an `all` target that aggregates primary checks/build.
  - `Makefile` should expose `test-unit`, `test-integration`, and `coverage`.
  - Makefile and CI must check lock freshness, sync with `--locked`, and run
    project commands with `uv run --locked`; do not retain a parallel pip
    install path.
  - CI should keep PR validation fast and move integration/coverage to release or manual runs.
- `ai-ml`: `src/<package>/ml/` split for train/eval/infer pipelines.

In coordinated-candidate scope, limit this list to paths explicitly assigned
inside the component. Return candidate path, mode, provenance, and validation
requirements rather than writing the target.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Non-Negotiable Guardrails

- No secret material in repo, samples, logs, tests, or docs.
- Keep uv index credentials external to repository configuration and lockfiles.
- Keep uv's default `first-index` strategy; do not weaken index selection to
  work around a resolution failure. Use named indexes and `explicit = true`
  when an index should serve only explicitly bound packages.
- Do not use `--frozen` as the CI freshness check, use broad `--upgrade` for a
  scoped dependency request outside the isolated library lower-bound lane, or
  use `--active` against an unrelated environment by default.
- Preserve uv's resolver explanation on failure. Do not silently widen Python
  support, weaken constraints, switch indexes, or broaden the upgrade.
- Ignore runtime, build, cache, credential, and local config artifacts.
- Prefer bounded dependency ranges (`>=x,<y`) and avoid unconstrained pins unless required.
- Keep commands idempotent where practical.
- Use typed boundaries for external inputs (Pydantic models/dataclasses).
- Return explicit non-zero exit codes for CLI failures.
- Avoid shelling out when a Python API exists; if shell is required, set explicit timeouts and sanitize args.
- Keep networking code timeout-safe and retry-safe.
- Unit tests must not access the network, real cloud APIs, or external infrastructure.
- Integration tests must be explicitly marked and isolated from the fast unit lane.
- Prefer patching external clients with `unittest.mock.patch` in unit tests.
- Keep fixtures small and deterministic; avoid large datasets in default scaffolds.
- Route public standalone React/TypeScript/Vite source to `frontend-project`
  and container artifacts to `container`.

## Versioning and Release Pattern

Default to `setuptools-scm` with SemVer tags:

- Tag format: `<project>-vMAJOR.MINOR.PATCH`
- If the package exports `__version__`, do not import the generated
  `_version.py` file directly from `__init__.py` for source/editable checkouts.
  Prefer a `runtime_version.py` helper that tries live SCM state first, then
  package metadata, then the generated version file.
- Generated runtime version file: `src/<package>/_version.py`
- Do not manually edit the version in `pyproject.toml` when SCM versioning is enabled.

For containerized/Helm-delivered apps, keep version layers related but independent:

- App version (source of functional behavior):
  - SemVer from tags.
- Image version (source of deployed artifact):
  - publish immutable tags (`sha-<shortsha>`, and release tags like `X.Y.Z` + `X.Y.Z-g<shortsha>`).
  - prefer production deploys pinned by digest.
- Helm chart versioning:
  - `Chart.yaml.version` tracks chart packaging changes.
  - `Chart.yaml.appVersion` tracks the default app/image SemVer.
  - do not force chart `version` to equal app SemVer.

## Templates and References

- `assets/pyproject.toml.template`: baseline project metadata and tooling.
- `assets/cli.py.template`: Typer-based CLI starter.
- `assets/api.py.template`: FastAPI starter with health endpoint.
- `assets/systemd.service.template`: hardened service unit baseline requiring
  `{{service_module}}` to match a generated importable source module.
- `assets/Makefile.template`: local developer workflow and fast test targets.
- `assets/tests-conftest.py.template`: pytest fixture baseline with unit-test network blocking.
- `assets/test-cli.py.template`: sample unit tests for a Typer CLI.
- `assets/test-integration-cli.py.template`: sample integration smoke test layout.
- `assets/github-actions-ci.yml.template`: CI with fast PR checks and fuller release/manual validation.

Use detailed references only when needed:

- `references/base-layout.md`
- `references/dependency-management.md`
- `references/cli-systemd.md`
- `references/api-ui.md`
- `references/iac-automation-security-networking.md`
- `references/ai-ml.md`
- `references/testing.md`
