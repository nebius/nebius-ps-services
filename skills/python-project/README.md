# Python Project

`python-project` scaffolds and hardens Python projects with reusable modern
defaults.

## What It Does

- Creates or improves `pyproject.toml` based projects.
- Uses `src/` layout, setuptools-scm, Ruff, pytest, Typer, and Pydantic where
  appropriate.
- Supports CLI tools, systemd services, APIs, UI apps, IaC automation, security
  tooling, and AI/ML workflows.
- Adds templates, tests, Makefile targets, and CI scaffolding when requested.

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
3. Add only the needed templates and dependencies.
4. Align tests, linting, docs, and CI.
5. Run focused Python validation.

## Core Concepts

- Prefer `pyproject.toml` and `src/` layout.
- Keep compatibility shims minimal and justified.
- Do not introduce dependencies without a clear project need.
- Make generated examples executable where practical.

## Files

- `SKILL.md`: Python scaffolding workflow and guardrails.
- `assets/`: project templates.
- `references/`: profile-specific guidance.
- `agents/openai.yaml`: UI metadata.
