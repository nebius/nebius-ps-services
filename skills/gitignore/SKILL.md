---
name: gitignore
description: Create or update a project's .gitignore file with sensible defaults for macOS and VS Code, then extend it for detected stacks (for example Node, Python, Go, Rust, Java, Terraform). Use when the user asks to generate, fix, or standardize .gitignore.
---

# Gitignore

Create or update `.gitignore` at the target repository root.

## Workflow

1. Detect stack markers from files in the repo:
   - Node: `package.json`, `pnpm-lock.yaml`, `yarn.lock`
   - Python: `pyproject.toml`, `requirements.txt`, `poetry.lock`
   - Go: `go.mod`
   - Rust: `Cargo.toml`
   - Java/Kotlin: `pom.xml`, `build.gradle`, `build.gradle.kts`
   - Terraform: `*.tf`, `.terraform.lock.hcl`
2. Start from `assets/gitignore.macos-vscode.template`.
3. Append only relevant stack-specific patterns.
4. If `.gitignore` already exists:
   - Keep user/custom entries.
   - Add missing standard patterns.
   - Avoid duplicates.
5. Never add rules that ignore source code broadly (for example `src/`, `*.ts`, `*.py`).

## Baseline Template

Use `assets/gitignore.macos-vscode.template` as the base for every generated file.

## Stack Add-ons

Add only when relevant:

- Node: `.npm/`, `.pnpm-store/`, `.yarn/`, `.next/`, `.nuxt/`
- Python: `.mypy_cache/`, `.ruff_cache/`, `.tox/`, `.nox/`, `.ipynb_checkpoints/`
- Go: `*.test`, `coverage.out`
- Rust: `target/` (keep `Cargo.lock` unless user explicitly wants to ignore it)
- Java/Kotlin: `.gradle/`, `out/`, `target/`
- Terraform: `.terraform/`, `*.tfstate`, `*.tfstate.*`, `crash.log`

## Output Rules

- Write the final file as `.gitignore` in repo root.
- Keep sections grouped with short comments.
- Keep output idempotent so re-running does not introduce churn.
