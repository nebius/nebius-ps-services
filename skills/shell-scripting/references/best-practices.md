# Bash Scripting Best Practices

## Baseline

- Shebang: `#!/usr/bin/env bash`
- Safety flags: `set -euo pipefail`
- Avoid implicit globals; prefer `local` in functions.

## Input and Args

- Support `-h` / `--help`.
- Fail on unknown options.
- Support `--` to end option parsing when relevant.
- Validate required positional args with clear error messages.

## Output and UX

- Use `printf` for reliable formatting.
- Separate concerns:
  - success/info to stdout
  - warnings/errors to stderr
- Keep logs short and consistent.
- Respect `NO_COLOR` and non-interactive terminals.

## Safety

- Do not assume commands exist; validate with `command -v`.
- Prefer non-destructive defaults.
- Require explicit flags for destructive operations.
- Use temporary paths via `mktemp`.
- Use `trap` cleanup for temporary resources.

## Idempotency

- Re-running should converge to a stable state.
- Avoid appending duplicates.
- Check before creating/modifying/deleting resources.

## Portability Notes

- Target Bash intentionally; avoid shell-agnostic claims unless tested.
- Account for macOS/Linux differences when using platform-specific commands.

## Verification Checklist

1. `bash -n script.sh` passes.
2. Help output is clear and complete.
3. Unknown options fail with a non-zero exit code.
4. Basic happy-path run succeeds.
5. Re-running does not corrupt state.
