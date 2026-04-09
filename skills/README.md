# Skills

This folder contains multiple ready-to-use Codex skills for developers.

## Included Skills

- `attach-ubuntu`
- `gitignore`
- `github-workflows`
- `helmchart`
- `linter`
- `nebius`
- `onboard-nbs-cxcli`
- `python-project`
- `publish-image`
- `publish-release`
- `release-generator`
- `shell-scripting`
- `terraform`

`attach-ubuntu` launches or reuses a per-project `ubuntu:24.04` Docker
container, bind-mounts the current folder at `/workdir`, preconfigures VS Code
attached-container defaults, bootstraps base build tools plus project Python
dependencies when `pyproject.toml` is present, mounts repo Git metadata for
subproject versioning workflows, isolates dependencies in a container-only
virtual environment so the host repo stays clean, and best-effort opens the
project in a new Dev Containers window on macOS with Docker Desktop.

`github-workflows` provides repo-aligned GitHub Actions assets and references
for PR/merge CI, release publication, container image publishing, and workflow
hardening.

`onboard-nbs-cxcli` is the repo-specific onboarding guide for adding Nebius
Terraform modules to `services/nebius-cxcli`, including when a change stops at
`component_sources.yaml` and when it must also touch wizard/provider/status,
runtime validation, or cluster handoff code.

`publish-release` scaffolds the local release helper script and changelog flow,
including first-push upstream setup for new local release branches during
`--prep`, strict clean-worktree checks that include untracked files, duplicate
tag blocking before changelog edits, local publish blocking when the target
release section is empty, markdownlint-safe blank-line preservation between
release sections, idempotent reruns while the tag remains unreleased, and
source-checkout runtime-version verification before the tag push.

`release-generator` is the manual-only fallback for projects where the user
explicitly wants release prep/publish driven by a local `release.sh` script and
no CI workflow. Otherwise prefer `publish-release`.

## Skills Installer

Script helper to install Codex skills into `~/.agents/skills`.

## What it does

- Installs/updates skills from a local source folder or GitHub URL.
- Recognizes skill folders by `SKILL.md`.
- Keeps installs idempotent across repeated runs.
- Avoids overwriting skills that belong to a different source.
- Supports removing one installed skill by its visible Codex skill name.
- Removes stale skills only for the same source.

## Requirements

- `bash`
- `rsync`
- `git` (required for GitHub sources)

## Usage

```bash
./install-skills.sh [options] [source] [destination_dir]
./install-skills.sh --remove-skill <skill_name> [destination_dir]
```

### Source

- Local directory path (default: script directory). Can be either:
  - a folder containing multiple skills
  - a single skill folder (contains `SKILL.md`)
- GitHub repository URL: `https://github.com/<owner>/<repo>`
- GitHub nested folder URL: `https://github.com/<owner>/<repo>/tree/<ref>/<subpath>`

### Destination

- Default: `~/.agents/skills`

### Options

- `-h`, `--help`: Show help.
- `--remove-skill <skill_name>`: Remove one installed skill from the destination.
  You can pass either:
  - the skill `name:` from `SKILL.md`, which is the name Codex shows in VS Code
    and uses for routing
  - the installed skill folder name under `~/.agents/skills`

## Examples

```bash
# Install from default source (script directory) to the default destination
./install-skills.sh

# Install from explicit local source folder
./install-skills.sh /Users/example/test

# Install from GitHub nested skills folder
./install-skills.sh "https://github.com/openai/skills/tree/main/skills"

# Install one specific skill from a nested GitHub path
./install-skills.sh "https://github.com/openai/skills/tree/main/skills/.curated/openai-docs"

# Install from GitHub nested skills folder to custom destination
./install-skills.sh "https://github.com/openai/skills/tree/main/skills" "~/.agents/skills"

# Remove an installed skill by the same name shown in Codex chat
./install-skills.sh --remove-skill nebius

# Remove an installed skill by its folder name
./install-skills.sh --remove-skill vendor-nebius
```

## Notes

- Existing unmanaged folders in destination are never overwritten.
- If a skill exists but belongs to another source, it is skipped.
- A valid skill folder must contain `SKILL.md`.
- `--remove-skill` deletes the matching skill folder from the destination and
  removes its local manifest entries.
- After removing a skill, reload the VS Code extension host to refresh skill
  discovery.
- Reinstalling from a source that still contains the skill will add it back.
- The installer uses `rsync --delete` inside each managed skill directory, and
  it also removes stale skills that were previously installed from the same
  source.
