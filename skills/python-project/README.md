# Python Project

`python-project` scaffolds and hardens Python projects with a uv-first,
lockfile-backed workflow and reusable modern defaults.

Creation is coordinated by `scaffold-project`, including Python-only projects.
This skill produces private candidates and completes Python readiness after
apply. Existing Python maintenance runs directly. See the
[producer contract](references/project-contract.md) and the shared
[creation routing](../scaffold-project/references/creation-routing.md).

## What It Does

- Creates or improves `pyproject.toml` based projects.
- Uses `uv.lock` as the generated, reviewed install graph for new uv projects,
  while preserving an existing package manager unless migration is requested.
- Separates runtime dependencies, development groups, and consumer extras, and
  keeps local and CI execution locked to the reviewed graph.
- Uses `src/` layout, setuptools-scm, Ruff, pytest, Typer, and Pydantic where
  appropriate.
- Supports CLI tools, systemd services, APIs, Python-native internal UI apps,
  IaC automation, security tooling, and AI/ML workflows.
- Requires each generated systemd unit to name a matching generated,
  importable service module instead of assuming a fixed package path.
- Teaches behavior-first unit/integration and optional contract/E2E tests,
  regression placement, boundary mocks, isolated fixtures and meaningful coverage.
- Separates test layers from speed and network permission; uses pytest-socket
  for accidental socket protection with explicit enforcement limits.
- Registers every marker used by the default test selection, including `e2e`
  before any E2E tests exist, so strict marker checks accept a fresh scaffold.
- Runs fast hermetic tests on PRs and validates the installed wheel separately;
  external tests require opt-in and a selected non-production target preflight.
- Makes xdist conditional on measured benefit. See [testing](references/testing.md).
- Routes public React/Vite frontend source to `frontend-project` and Docker or
  Compose files to `container`.

## Architecture

```text
Python creation request
  |
  v
scaffold-project -> Python candidate producer
  |
  v
Choose core profile
  |
  +--> base package layout
  +--> CLI or service/API templates
  +--> tests and fixtures
  +--> component lint and test tooling
  `--> component docs and package metadata

Root README/Makefile -> scaffold-project
Root ignore rules -> gitignore; root CI -> github-workflows
```

## Workflow

1. Route creation to the coordinator once; keep maintenance direct.
2. Inspect existing files before scaffolding.
3. Add only the needed templates and dependencies through the selected
   package manager.
4. After apply, bootstrap the real lockfile and project environment.
5. Run focused Python validation.

## Core Concepts

- Prefer `pyproject.toml`, generated `uv.lock`, and `src/` layout for new
  projects.
- Treat requirements files as derived exports, not a second dependency
  authority.
- Add compatibility shims only when the user explicitly requests them.
- Do not introduce dependencies without a clear project need.
- Make generated examples executable where practical.

## Files

- `SKILL.md`: Python scaffolding workflow and guardrails.
- `scripts/python_project.py`: offline render/validate and post-apply bootstrap.
- `scripts/python_bootstrap.py`: environment isolation and readiness evidence.
- `assets/`: project templates.
- `references/`: profile-specific guidance.
- `agents/openai.yaml`: UI metadata.

## Source Validation

Install the declared `scripts/requirements-test.txt` into a disposable environment,
then run both `scripts/test_python_project_contract.py` and
`scripts/test_testing_templates.py`. The latter renders disposable projects and
tests actual pytest behavior and installed-wheel negative controls. Runtime
triggering and comparative model quality remain separate evidence.

Core executable profiles are package/library, CLI, API and systemd. Extended
profiles remain guidance and are not silently accepted by the producer.
Readiness includes lint, tests and installed-wheel verification; it never
activates services. New metadata omits unspecified author/license declarations
instead of referencing nonexistent files.
