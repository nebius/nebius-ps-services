---
name: shell-scripting
description: "Create, refactor, review, or harden Bash scripts, shell automation, and CLI wrappers with strict mode, safe parsing, idempotency, structured logs, readable help, and color fallback."
---

# Shell Scripting

## Help

For `$shell-scripting --help` or `$shell-scripting -h`, return concise help and stop before
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

## Overview

Write scripts that are safe by default, easy to read, and predictable across repeated runs.
For new scripts, start from `assets/script-template.sh`.

## Invocation Scope

- `standalone`: create, refactor, or review the selected target script.
- `coordinated-candidate`: receive exact `.sh` paths, runtime assumptions,
  exclusions, and private bundle from `scaffold-project`; emit exact candidates
  only in that bundle and never write the target.

In coordinated-candidate scope, do not create a root Makefile, CI workflow,
application source, infrastructure, container, deployment, or agent
instruction file. Return candidate path, executable mode, provenance, and
shell validation requirements.

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

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Resources

- `assets/script-template.sh`: baseline script template with rich usage/log output.
- `references/best-practices.md`: expanded checklist and patterns.
