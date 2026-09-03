---
name: attach-ubuntu
description: "Use only when explicitly asked for a disposable Ubuntu Docker test environment on macOS: mount the project at /workdir, configure VS Code Dev Containers, and best-effort open a new window."
---

# Attach Ubuntu

## Help

For `$attach-ubuntu --help` or `$attach-ubuntu -h`, return concise help and stop before
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

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.
