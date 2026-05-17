# Publish Release

`publish-release` generates a default application release publication flow with
GitHub Releases.

## What It Does

- Creates a project-local `CHANGELOG.md`.
- Creates a `publish-release.sh` helper.
- Creates a tag-driven GitHub Release workflow.
- Adds artifact and version verification where applicable.

## Architecture

```text
Application project
  |
  +--> changelog
  +--> publish-release.sh
  `--> release publish workflow
        |
        v
Tag-driven GitHub Release
```

## Workflow

1. Collect project name, artifact paths, version source, and tag pattern.
2. Add or update release assets from templates.
3. Wire the GitHub Actions workflow.
4. Validate shell syntax, workflow YAML, and artifact assumptions.
5. Report release prep and publication steps.

## Core Concepts

- Prefer tag-driven GitHub Releases for default release automation.
- Keep release metadata close to the project being released.
- Use `release-generator` only when the user explicitly wants local manual
  releases and no CI workflow.

## Files

- `SKILL.md`: application release workflow and guardrails.
- `assets/`: changelog, shell helper, and workflow templates.
- `agents/openai.yaml`: UI metadata.
