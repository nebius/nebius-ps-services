---
name: linter
description: "Lint and auto-fix repository Shell, Markdown, or Python files using shellcheck/bash -n, markdownlint with project fallbacks, or Ruff with targeted pyproject rules."
---

# Linter

## Help

For `$linter --help` or `$linter -h`, return concise help and stop before
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

Run repo linting with a fix-first workflow and conservative config fallback when direct fixes are not enough.

## Workflow

1. Confirm the repo root path.
2. Run `scripts/lint-repo.sh --root <path>`.
3. Prefer direct source fixes first.
4. Apply fallback config rules only for unresolved rule IDs.

## Checks

- Shell:
  - Discover `*.sh` and `*.bash` files.
  - Run `bash -n` for syntax checks.
  - Run `shellcheck -x` for static analysis.
- Markdown:
  - Discover all nested `.md` files.
  - Run `markdownlint --fix` first.
  - If issues remain, run an extra formatter pass (`prettier --prose-wrap always` or `mdformat` when available).
  - Re-run `markdownlint`; only if still unresolved, create `.markdownlint.json` fallback rules when safe.
- Python:
  - Discover all `*.py` files.
  - Run `ruff check --fix` first.
  - Re-run `ruff check`; if unresolved, add targeted Ruff ignores in `pyproject.toml` when safe.

## Execution Mode

Scripts in this skill are reference-only by default.
Execute scripts only when the user explicitly starts the request with `Run` or `Execute` (or equivalent).

## Commands

```bash
# Full pass: fix first, then fallback config only if required
scripts/lint-repo.sh --root .

# Check-only mode
scripts/lint-repo.sh --root . --no-fix --no-config-fallback
```

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.
