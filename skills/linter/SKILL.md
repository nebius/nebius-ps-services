---
name: linter
description: Lint and auto-fix Shell, Markdown, and Python files in a repository. Use when users ask to lint shell scripts with shellcheck and bash -n, lint all nested Markdown files with markdownlint (fix first, then .markdownlint.json fallback rules), or lint Python files with ruff (fix first, then targeted Ruff rules in pyproject.toml).
---

# Linter

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
