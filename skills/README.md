# Skills Installer

Shell installer to sync Codex skills into `~/.agents/skills`.

## What it does

- Installs/updates skills from a local source folder or GitHub URL.
- Recognizes skill folders by `SKILL.md`.
- Keeps installs idempotent across repeated runs.
- Avoids overwriting skills that belong to a different source.
- Removes stale skills only for the same source.

## Requirements

- `bash`
- `rsync`
- `git` (required for `--pull` and GitHub sources)

## Usage

```bash
./install-skills.sh [options] [source] [destination_dir]
```

### Source

- Local directory path (default: script directory).
- GitHub repository URL: `https://github.com/<owner>/<repo>`
- GitHub nested folder URL: `https://github.com/<owner>/<repo>/tree/<ref>/<subpath>`

### Destination

- Default: `~/.agents/skills`

### Options

- `--pull`: Run `git pull --ff-only` for a local git source before syncing.
- `-h`, `--help`: Show help.

### Automatic Behavior

- If one or more skills are installed/updated, the script triggers `Developer: Restart Extension Host` automatically (best effort on macOS, non-fatal).

## Examples

```bash
# Install from default source (script directory)
./install-skills.sh

# Install from explicit local source folder
./install-skills.sh /Users/rezab/test

# Pull latest changes from local git repo then install
./install-skills.sh --pull /path/to/local/skills-repo

# Install from GitHub nested skills folder
./install-skills.sh "https://github.com/openai/skills/tree/main/skills"

# Install from GitHub nested skills folder to custom destination
./install-skills.sh "https://github.com/openai/skills/tree/main/skills" "~/.agents/skills"
```

## Notes

- `--pull` only works with local git working trees and fails on dirty/detached state.
- Existing unmanaged folders in destination are never overwritten.
- If a skill exists but belongs to another source, it is skipped.
- A valid skill folder must contain `SKILL.md`.
