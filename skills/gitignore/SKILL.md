---
name: gitignore
description: Create or update a project's .gitignore file with sensible defaults for macOS and VS Code, then extend it for detected stacks (for example Node, Python, Go, Rust, Java, Terraform). Use when the user asks to generate, fix, or standardize .gitignore.
---

# Gitignore

## Help

For `$gitignore --help` or `$gitignore -h`, return concise help and stop before
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

Create or update `.gitignore` at the target repository root.

## Invocation Scope

- `standalone`: create or additively update the target repository `.gitignore`.
- `coordinated-candidate`: receive the detected stack set, existing file,
  exact root path, and private bundle from `scaffold-project`; emit the complete
  additive candidate only in that bundle and never write the target.

The coordinated candidate must retain every existing rule and comment, add
only missing approved patterns, and return candidate path, mode, provenance,
and validation requirements. It owns no component-local files.

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

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.
