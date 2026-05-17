# Publish Image

`publish-image` generates a container image publication workflow.

## What It Does

- Creates a project-local `CHANGELOG.md`.
- Creates a `publish-image.sh` helper.
- Creates a tag-driven GitHub Actions image publication workflow.
- Supports manual dispatch controls and immutable tagging.

## Architecture

```text
Containerized project
  |
  +--> changelog
  +--> publish-image.sh
  `--> image publish workflow
        |
        v
Build, tag, and publish image
```

## Workflow

1. Collect project name, image name, registry, Dockerfile path, and tag policy.
2. Add release assets from templates.
3. Wire workflow triggers and permissions.
4. Validate shell, workflow YAML, and image metadata assumptions.
5. Report publish commands and remaining registry prerequisites.

## Core Concepts

- Prefer immutable release tags.
- Keep registry credentials out of source control.
- Keep script behavior, workflow behavior, docs, and changelog aligned.

## Files

- `SKILL.md`: image publication workflow and guardrails.
- `assets/`: changelog, shell helper, and workflow templates.
- `agents/openai.yaml`: UI metadata.
