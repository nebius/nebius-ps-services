# Release Generator

`release-generator` is the manual release-script fallback. Use it only when the
user explicitly asks for local manual release publishing with no CI workflow.

## What It Does

- Generates or reviews a local `release.sh` script.
- Supports preparing, publishing, verifying, and retagging manual releases.
- Keeps tag format and release safety explicit.
- Defers to `publish-release` for the default tag-driven CI workflow.

## Architecture

```text
Explicit manual release request
  |
  v
release.sh helper
  |
  +--> prepare
  +--> publish
  +--> verify
  `--> retag safety
```

## Workflow

1. Confirm the user wants manual local release publishing.
2. Collect project name, artifacts, version source, and tag format.
3. Generate or patch `release.sh`.
4. Validate shell syntax and command safety.
5. Report manual release steps and risks.

## Core Concepts

- Do not use this for default CI-backed releases.
- Make destructive retagging explicit.
- Keep release commands reproducible and auditable.

## Files

- `SKILL.md`: manual release workflow and routing rule.
- `scripts/release.sh`: reference release helper.
- `agents/openai.yaml`: UI metadata.
