# Gitignore

`gitignore` creates or updates project `.gitignore` files with a reusable
baseline and detected stack-specific additions.

## What It Does

- Adds safe macOS and VS Code defaults.
- Detects common technology stacks such as Node, Python, Go, Rust, Java, and
  Terraform.
- Adds missing ignore rules without removing user-owned entries.
- Keeps generated, local, cache, and secret-like files out of source control.

## Architecture

```text
Project files
  |
  v
Detect baseline and stack needs
  |
  v
Patch .gitignore
  |
  v
Review final rules for accidental overreach
```

## Workflow

1. Inspect the repository layout and existing `.gitignore`.
2. Add the reusable macOS and editor baseline if missing.
3. Add stack-specific sections based on detected files.
4. Preserve existing project-specific rules.
5. Report the sections added or updated.

## Core Concepts

- Patch existing files rather than replacing them.
- Do not ignore source files or checked-in templates.
- Keep local secrets and generated caches out of the repository.

## Files

- `SKILL.md`: runtime workflow and output rules.
- `assets/gitignore.macos-vscode.template`: reusable baseline template.
- `agents/openai.yaml`: UI metadata.
