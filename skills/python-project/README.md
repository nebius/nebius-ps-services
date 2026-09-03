# Python Project

`python-project` scaffolds and hardens Python projects with a uv-first,
lockfile-backed workflow and reusable modern defaults.

It supports two scopes: standalone repository ownership and
`coordinated-candidate`, where `scaffold-project` assigns a bounded Python
component and this skill emits private candidate files without touching the
target or claiming root cross-cutting artifacts.

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
- Adds templates, tests, Makefile targets, and CI scaffolding when requested.
- Routes public React/Vite frontend source to `frontend-project` and Docker or
  Compose files to `container`.

## Architecture

```text
Python project request
  |
  v
Choose project profile
  |
  +--> base package layout
  +--> CLI or service/API templates
  +--> tests and fixtures
  +--> lint and CI config
  `--> docs and release metadata
```

## Workflow

1. Identify project type and risk profile.
2. Inspect existing files before scaffolding.
3. Add only the needed templates and dependencies through the selected
   package manager.
4. Align the lockfile, tests, linting, docs, and CI.
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
- `assets/`: project templates.
- `references/`: profile-specific guidance.
- `agents/openai.yaml`: UI metadata.
