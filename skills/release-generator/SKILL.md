---
name: release-generator
description: "Use only when the user explicitly asks for manual release publishing with a local release.sh shell script and no CI workflow; otherwise prefer publish-release for the default tag-driven GitHub Release workflow."
---

# Release Generator

## Overview

Use this skill only for a manual release workflow with the bundled
`scripts/release.sh` template when the user explicitly asks for shell-script
release handling without a CI workflow. For the default repo pattern, use the
`publish-release` skill instead. Treat scripts as references unless the user
explicitly asks to execute them.

## Routing Rule

- Default choice: use `publish-release` for tag-driven GitHub Releases with a
  CI workflow.
- Use `release-generator` only when the user explicitly requests manual release
  prep/publish with `release.sh` and explicitly does not want a CI release
  workflow.

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

Use on a working branch to update `CHANGELOG.md`, commit only that changelog
change, and push.

```bash
./release.sh --prep vX.Y.Z
```

Note: `--prep` is idempotent and keeps `## [Unreleased]` clean while merging
new entries into the target tag section. On a brand-new local release branch,
the first `--prep` push auto-sets `origin/<branch>` as upstream.

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
