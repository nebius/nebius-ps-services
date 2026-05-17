# Shell Scripting

`shell-scripting` creates, refactors, and reviews Bash and shell automation.

## What It Does

- Builds strict, idempotent shell scripts.
- Uses safe argument parsing and clear usage output.
- Adds readable logging and color fallback where appropriate.
- Validates scripts with syntax and lint checks.
- Keeps shell behavior aligned with docs and examples.

## Architecture

```text
Shell automation request
  |
  v
Inspect existing script or requirements
  |
  v
Apply Bash safety and UX conventions
  |
  v
Validate with shell-focused checks
```

## Workflow

1. Identify the target shell and portability requirements.
2. Inspect existing scripts and calling docs.
3. Patch or create the smallest script that satisfies the workflow.
4. Add usage, logging, dry-run, and idempotency behavior when relevant.
5. Run `bash -n` and `shellcheck` when available.

## Core Concepts

- Prefer strict mode and explicit error handling.
- Quote variables and avoid unsafe word splitting.
- Keep destructive actions guarded and visible.
- Avoid clever shell when straightforward code is safer.

## Files

- `SKILL.md`: shell workflow, practices, and output style.
- `assets/script-template.sh`: reusable script template.
- `references/best-practices.md`: detailed shell guidance.
- `agents/openai.yaml`: UI metadata.
