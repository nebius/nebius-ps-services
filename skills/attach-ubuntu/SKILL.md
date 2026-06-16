---
name: attach-ubuntu
description: Launch or reuse an Ubuntu Docker container for the current project, bind-mount it at /workdir, preconfigure VS Code attached-container defaults, and best-effort open the project in a new VS Code Dev Containers window. Use when users want a disposable Ubuntu test environment on macOS with Docker Desktop and the Dev Containers extension.
---

# Attach Ubuntu

Use this skill when the user wants a quick Ubuntu test container for the current
project without adding a repo-local `.devcontainer/` as the primary path.

## Workflow

1. Use `scripts/attach-ubuntu.sh` from the target project directory when
   script execution is permitted by the current user and repository policy.
2. Keep one container per project folder by deriving the container name from the
   current directory unless the user explicitly overrides it.
3. For a standalone project, bind-mount the current host directory to
   `/workdir` and set the container working directory to `/workdir`.
4. When the current project folder lives inside a larger Git repository and
   does not contain its own valid `.git`, bind-mount the repo root at
   `/opt/attach-ubuntu/repo-root` instead, then expose the requested project
   subdirectory through a `/workdir` symlink inside the container so
   version-aware tools like `setuptools-scm` still see the real repository.
5. Merge a VS Code attached-container config for the derived container name so
   manual fallback opens `/workdir` and keeps the `openai.chatgpt` extension
   present without colliding with other `ubuntu:24.04` attach sessions.
6. Bootstrap the container toolchain after startup:
   - install base Ubuntu build tools such as `make`, `build-essential`, `git`,
     `curl`, and Python tooling
   - detect a preferred Python version from `.python-version`, `runtime.txt`,
     or `pyproject.toml` `requires-python` when possible
   - if `pyproject.toml` exists, prefer the project's own `make env` or
     `make install` target with a container-only virtual environment at
     `/opt/attach-ubuntu/venv`; otherwise fall back to creating that venv and
     installing the project in editable mode
7. Best-effort open a new VS Code window with the `attached-container` remote
   URI. If that attach request fails on the user’s installation, fall back to:
   `Dev Containers: Attach to Running Container...`

## Notes

- The supported VS Code attach entrypoint remains the Command Palette or Remote
  Explorer. The script still uses the internal `attached-container` URI as an
  automation shortcut because it works on current VS Code builds, but keep the
  manual fallback in the output.
- VS Code defaults to image-level attached-container config, but the script
  writes a container-name config directly because it is more stable when
  multiple `ubuntu:24.04` attach windows are active at the same time.
- The script auto-activates `/opt/attach-ubuntu/venv` in interactive Bash
  terminals when that virtual environment exists, and it exports
  `VENV=/opt/attach-ubuntu/venv` so later `make all` runs use the container
  venv instead of a host-created `.venv`.
- The script is idempotent. It reuses the existing container when the image,
  mount source, and working directory already match; otherwise it recreates it.
- Optional cleanup is built in via `--stop` and `--remove`, and bootstrap can
  be skipped with `--no-bootstrap`.

## Execution Mode

Scripts in this skill are reference-only by default. Execute
`scripts/attach-ubuntu.sh` only when the user explicitly starts the request
with `Run` or `Execute` (or equivalent). If execution is not clearly requested,
explain the relevant command instead of running it.

## Commands

- Launch or reuse the container and request a VS Code attach:
  `./attach-ubuntu/scripts/attach-ubuntu.sh`
- Prepare the container only:
  `./attach-ubuntu/scripts/attach-ubuntu.sh --no-open`
- Prepare the container but skip toolchain and dependency installation:
  `./attach-ubuntu/scripts/attach-ubuntu.sh --no-open --no-bootstrap`
- Stop the per-project container:
  `./attach-ubuntu/scripts/attach-ubuntu.sh --stop`
- Remove the per-project container:
  `./attach-ubuntu/scripts/attach-ubuntu.sh --remove`

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings back
into this skill's local source materials before completion when the current task
contract allows source edits. Update the narrowest appropriate surface:
`SKILL.md` for runtime rules, `references/` for detailed guidance, `assets/`
for reusable templates, `scripts/` for deterministic helpers, and README or
changelog entries for human-facing or release-note updates.

If the current task is explicitly read-only/report-only, or source writes are
outside this skill's task contract, do not edit skill sources; report the
skipped source update instead.

Do not capture secrets, private URLs, customer data, raw logs, one-off local
state, or unverified/vendor-specific claims. If a useful learning is not safe,
not evidence-backed, or outside this skill's scope, report that it was skipped.
