---
name: release-generator
description: "Provide neutral guidance and templates for automating release workflows with release.sh, including changelog prep, tagging, publishing GitHub releases, and verifying artifacts."
---

# Release Generator

## Overview

Use this skill to guide a generic release workflow with the bundled
`scripts/release.sh` template, including changelog prep, tag and publish, and
release verification. Treat scripts as references unless the user explicitly
asks to execute them.

## Prerequisites

1. Ensure the target repo is the intended project for the release workflow.
2. Ensure `release.sh` exists at the repo root. Copy it from
   `scripts/release.sh` in this skill if needed, and customize defaults.
3. Ensure `git`, `python3`, `python -m build`, and `gh` CLI are available.
4. Ensure GitHub auth is configured via `GH_TOKEN`, `GITHUB_TOKEN`, or
   `gh auth login`.

## Tag Format

Use one of the supported tag formats:

```bash
vMAJOR.MINOR.PATCH
# or
<prefix>-vMAJOR.MINOR.PATCH
```

## Prepare a Release

Use on a working branch to update `CHANGELOG.md`, commit, and push.

```bash
./release.sh --prep vX.Y.Z
```

Note: `--prep` is idempotent and keeps `## [Unreleased]` clean while merging
new entries into the target tag section.

## Publish a Release

Use only on `main` when the working tree is clean and up to date with
`origin/main`.

```bash
./release.sh --publish vX.Y.Z
```

The script tags, builds the wheel, creates the GitHub release, and uploads the
wheel asset.

## Verify a Release

Use to download the wheel from GitHub Releases and verify integrity.

```bash
./release.sh --verify vX.Y.Z
```

## Retagging Safety

Use `--force-retag` only when explicitly requested. Retagging deletes the
existing tag and any GitHub release with that tag.

## Modes

- Default: Treat scripts as reference. Copy or adapt code into the repo, do not
  execute anything.
- Execute mode: Only execute scripts if the user explicitly asks using one of
  these phrases: "run" or "execute".

Note: If execute mode is not clearly requested, do not run anything. The user
must explicitly begin the request with Run/Execute (or equivalent). Example:
`$release-generator Run the release script now.`

## Resources

1. `scripts/release.sh` contains the canonical release workflow. Copy it to the
   repo root as `release.sh` before using commands. Update `TAG_PREFIX` and
   `WHEEL_PATTERN` defaults inside the script as needed.
