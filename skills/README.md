# Skills

This folder contains multiple ready-to-use Codex skills for developers.

## Included Skills

- `gitignore`
- `helmchart`
- `linter`
- `python-project`
- `publish-image`
- `publish-release`
- `release-generator`
- `shell-scripting`
- `terraform`

## Skills Installer

Script helper to install Codex skills into `~/.agents/skills`.

## What it does

- Installs/updates skills from a local source folder or GitHub URL.
- Recognizes skill folders by `SKILL.md`.
- Keeps installs idempotent across repeated runs.
- Avoids overwriting skills that belong to a different source.
- Removes stale skills only for the same source.

## Requirements

- `bash`
- `rsync`
- `git` (required for GitHub sources)

## Usage

```bash
./install-skills.sh [options] [source] [destination_dir]
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
```

## Notes

- Existing unmanaged folders in destination are never overwritten.
- If a skill exists but belongs to another source, it is skipped.
- A valid skill folder must contain `SKILL.md`.
