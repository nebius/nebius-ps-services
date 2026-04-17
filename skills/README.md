# Skills

This repository contains public, reusable Codex skills for common engineering
workflows. Each skill lives in its own folder and is discovered by the
presence of `SKILL.md`.

## Included Skills

- End-to-end project alignment: `align`
- Disposable Ubuntu project container setup: `attach-ubuntu`
- GitHub Actions authoring and review: `github-workflows`
- Stack-aware `.gitignore` generation and cleanup: `gitignore`
- Helm chart hardening and validation: `helmchart`
- Shell, Markdown, and Python linting: `linter`
- Nebius cloud automation and quota workflows: `nebius`
- Nebius cxcli component onboarding: `onboard-nbs-cxcli`
- Helm chart release publishing: `publish-helm`
- Container image release publishing: `publish-image`
- Application release publishing: `publish-release`
- Python project scaffolding and hardening: `python-project`
- Manual release-script generation: `release-generator`
- Bash and shell automation engineering: `shell-scripting`
- Terraform module and repo engineering: `terraform`

## Using Skills in Codex Chat

Use the exact skill name with a leading `$` in the Codex chat box, then add
the task you want. For example, use `$align` for this project,
`$shell-scripting` to harden a Bash script, or `$terraform` to scaffold or
review Terraform code.

## Skill Details

### `align`

`align` is the end-to-end repair and consistency skill. Use it when a project
needs code, tests, CI, CLI behavior, examples, help output, and docs reviewed
together so broken flows, stale wording, and mismatched contracts are fixed in
one pass instead of being reported separately.

### `attach-ubuntu`

`attach-ubuntu` launches or reuses a per-project `ubuntu:24.04` Docker
container, mounts the project at `/workdir`, prepares attached-container VS
Code defaults, and helps create a disposable Ubuntu environment for local
testing on macOS with Docker Desktop and the Dev Containers extension.

### `github-workflows`

`github-workflows` is the repository workflow skill for creating, reviewing,
and standardizing GitHub Actions. Use it for PR and merge CI, release
automation, container publication, merge-bot safety, permissions hardening, and
monorepo-friendly workflow structure.

### `gitignore`

`gitignore` creates or updates a project `.gitignore` with sensible macOS and
VS Code defaults, then extends it for the detected stack. Use it when you want
a clean ignore policy for languages and tools such as Python, Node, Go, Java,
Rust, or Terraform.

### `helmchart`

`helmchart` applies Helm chart best practices across metadata, values,
templates, schema, and validation. Use it to create or harden charts, improve
default safety, add missing chart structure, and verify the chart renders and
lints cleanly.

### `linter`

`linter` runs a fix-first linting workflow for shell scripts, Markdown, and
Python. Use it when you want syntax checks, `shellcheck`, `markdownlint`, or
`ruff` cleanup applied conservatively, with source fixes preferred over
config-level suppressions.

### `nebius`

`nebius` is the cloud automation skill for Nebius SDK-based workflows,
including IAM bootstrap, object storage, VPC inspection, route analysis, quota
checks, and MK8s compatibility decisions. Use it when the task depends on live
Nebius service behavior rather than generic cloud assumptions.

### `onboard-nbs-cxcli`

`onboard-nbs-cxcli` is the repo-specific onboarding skill for adding Nebius
Terraform-backed components into `nebius-cxcli`. It helps decide whether a
change stays catalog-only or also needs wizard, validation, runtime handoff,
status, and documentation updates.

### `publish-helm`

`publish-helm` generates a Nebius OCI Helm chart publication flow with a
chart-local `CHANGELOG.md`, `publish-helm.sh`, and a tag-driven GitHub Actions
workflow. Use it when a chart needs a repeatable prep and publish process with
version checks and public pull verification.

### `publish-image`

`publish-image` generates the release assets for container images, including
`CHANGELOG.md`, `publish-image.sh`, and a tag-driven image publication
workflow. Use it when a project needs immutable image tagging and a standard
release path for container artifacts.

### `publish-release`

`publish-release` generates the default application release flow for this skill
set: a `CHANGELOG.md`, `publish-release.sh`, and a tag-driven GitHub Release
workflow. Use it when the preferred model is CI-backed release publication with
artifact and version verification.

### `python-project`

`python-project` scaffolds and hardens Python repositories with reusable modern
defaults such as `pyproject.toml`, setuptools-scm, `src/` layout, Ruff, pytest,
Typer, and Pydantic. Use it when you need a strong starting point for CLIs,
services, APIs, automation tools, or AI-oriented Python projects.

### `release-generator`

`release-generator` is the manual-only fallback for projects that explicitly
want a local `release.sh` workflow and no CI release pipeline. Use it only when
that manual operating model is required; otherwise prefer `publish-release`.

### `shell-scripting`

`shell-scripting` is the Bash engineering skill for creating, reviewing, and
hardening `.sh` automation. Use it for strict-mode scripts, safe argument
parsing, idempotent behavior, readable usage output, and practical shell
maintainability.

### `terraform`

`terraform` generates and improves Terraform modules and infrastructure
repositories with reusable structure, state guidance, validation, security
controls, examples, and CI expectations. Use it when the work is about module
interfaces, environment layout, provider strategy, or Terraform quality gates.

## Skills Installer

`install-skills.sh` installs or updates skills into `~/.agents/skills` by
default. It accepts a local source directory or a supported GitHub URL, treats
only folders containing `SKILL.md` as installable skills, keeps reruns
idempotent with `rsync`, skips unmanaged or other-source-owned destinations,
and can remove one installed skill by visible Codex skill name or folder name.

## Requirements

- `bash`
- `rsync`
- `git` for GitHub sources

## Usage

```bash
./install-skills.sh [options] [source] [destination_dir]
./install-skills.sh --remove-skill <skill_name> [destination_dir]
```

## Supported Sources

- Local directory path. The default source is the script directory, and it can
  be either a multi-skill folder or a single skill folder containing
  `SKILL.md`.
- GitHub repository URL:
  `https://github.com/<owner>/<repo>`
- GitHub tree URL:
  `https://github.com/<owner>/<repo>/tree/<ref>/<subpath>`

## Examples

```bash
# Install all skills from this repository into the default destination
./install-skills.sh

# Install from an explicit local source directory
./install-skills.sh /Users/example/test

# Install from a GitHub repository root
./install-skills.sh "https://github.com/openai/skills"

# Install from a nested GitHub skills folder
./install-skills.sh "https://github.com/openai/skills/tree/main/skills"

# Install one specific skill from a nested GitHub path
./install-skills.sh \
  "https://github.com/openai/skills/tree/main/skills/.curated/openai-docs"

# Install to a custom destination
./install-skills.sh \
  "https://github.com/openai/skills/tree/main/skills" \
  "~/.agents/skills"

# Remove an installed skill by its visible Codex skill name
./install-skills.sh --remove-skill nebius

# Remove an installed skill by its folder name
./install-skills.sh --remove-skill vendor-nebius
```

## Notes

- If newly installed skills are not visible, run `Developer: Restart Extension
  Host` in VS Code.
- A valid skill folder must contain `SKILL.md`.
- Existing unmanaged folders in the destination are never overwritten.
- If a skill exists but belongs to another source, it is skipped.
- `--remove-skill` accepts either the exact `name:` from `SKILL.md` or the
  installed folder name.
- `--remove-skill` removes the destination skill folder and its local manifest
  entries.
- Reinstalling from a source that still contains a removed skill will add it
  back.
- Stale skill cleanup only applies to skills previously installed from the same
  source.
