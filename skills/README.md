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

### VS Code Refresh

- The script does not restart VS Code automatically.
- If newly installed skills are not visible, run `Developer: Restart Extension Host` manually in VS Code.

### Output Styling

- The script uses colored/styled terminal output for status messages.
- Colors are automatically disabled for non-interactive output.
- Set `NO_COLOR=1` to force plain output.

## Examples

```bash
# Install from default source (script directory)
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
