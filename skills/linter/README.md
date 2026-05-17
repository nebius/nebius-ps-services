# Linter

`linter` runs a fix-first linting workflow for shell scripts, Markdown, and
Python files.

## What It Does

- Checks shell scripts with `bash -n` and `shellcheck`.
- Checks Markdown with `markdownlint`.
- Checks Python with `ruff`.
- Applies safe automatic fixes before adding fallback config.
- Reports commands run and remaining manual fixes.

## Architecture

```text
Repository files
  |
  v
Detect Shell, Markdown, and Python surfaces
  |
  v
Run fix-first linting workflow
  |
  v
Patch safe issues or report blockers
```

## Workflow

1. Identify relevant file types.
2. Run the narrowest useful lint checks.
3. Apply safe automatic fixes when requested or appropriate.
4. Add targeted fallback lint config only when needed.
5. Re-run checks and report status.

## Core Concepts

- Prefer fixing files over weakening lint rules.
- Keep fallback config narrow and justified.
- Do not hide real syntax or formatting failures.

## Files

- `SKILL.md`: lint workflow and command policy.
- `scripts/lint-repo.sh`: reusable lint helper.
- `agents/openai.yaml`: UI metadata.
