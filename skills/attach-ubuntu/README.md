# Attach Ubuntu

`attach-ubuntu` creates or reuses a disposable Ubuntu container for the current
project on macOS. It helps test Linux behavior while keeping the host machine
clean.

## What It Does

- Starts or reuses an Ubuntu Docker container for a project.
- Bind-mounts the project into the container at `/workdir`.
- Prepares attached-container VS Code defaults.
- Installs baseline Ubuntu build tooling and Python project dependencies when
  requested by the helper.
- Best-effort opens a Dev Containers window.

## Architecture

```text
Current project
  |
  v
attach-ubuntu helper
  |
  v
Docker container with /workdir bind mount
  |
  v
Optional VS Code Dev Containers attachment
```

## Workflow

1. Confirm Docker Desktop and the Dev Containers extension are available.
2. Identify the current project root and container name.
3. Launch or reuse the project container.
4. Mount the project at `/workdir`.
5. Prepare container-local tooling and editor defaults.
6. Return the container status and commands for manual follow-up.

## Core Concepts

- The container is disposable; project files stay on the host bind mount.
- Git metadata is preserved because the repository is mounted directly.
- The helper is meant for local validation, not production deployment.

## Files

- `SKILL.md`: runtime instructions and execution policy.
- `scripts/attach-ubuntu.sh`: container launch and attachment helper.
- `agents/openai.yaml`: UI metadata.
