---
name: shell-scripting
description: Create, refactor, or review Bash shell scripts with strict mode, safe argument parsing, idempotent behavior, and rich CLI output (structured logs plus readable usage/help with color fallback). Use when users ask for .sh scripts, shell automation, CLI wrappers, or script hardening.
---

# Shell Scripting

## Overview

Write scripts that are safe by default, easy to read, and predictable across repeated runs.
For new scripts, start from `assets/script-template.sh`.

## Workflow

1. Confirm scope and runtime assumptions (bash version, OS expectations, required tools).
2. Start from `assets/script-template.sh` and adapt it.
3. Keep parsing explicit (`-h`, `--help`, `--`) and fail on unknown flags.
4. Keep operations idempotent where practical (safe re-runs, no destructive defaults).
5. Validate with:
   - `bash -n <script>`
   - a small functional smoke run with safe inputs

## Output Style

Use structured output helpers:

- `log_error`: red + bold prefix (`ERROR:`), stderr
- `log_warn`: amber, stderr
- `log_note`: dim informational note
- `log_success`: green success output

Use ANSI styling only when interactive:

- enable color when `-t 1` or `-t 2`
- disable color when `TERM=dumb` or `NO_COLOR` is set

Keep usage output readable:

- bold section headers (`Usage`, `Options`, `Examples`)
- highlight command examples in cyan
- keep notes concise and actionable

## Core Practices

- Use `#!/usr/bin/env bash` and `set -euo pipefail`.
- Quote variable expansions unless intentionally using word splitting.
- Use `local` for function-scoped variables.
- Prefer `printf` over `echo` for structured output.
- Validate required commands early (for example with `command -v` checks).
- Use `mktemp` for temporary files/dirs and cleanup with `trap`.
- Avoid destructive actions by default; require explicit opt-in for risky actions.
- Keep scripts composable and testable with small functions.

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

## Resources

- `assets/script-template.sh`: baseline script template with rich usage/log output.
- `references/best-practices.md`: expanded checklist and patterns.
