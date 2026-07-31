# GitHub Workflows

`github-workflows` creates, reviews, and standardizes GitHub Actions workflows
for repository automation.

In `coordinated-candidate` scope it returns exact private workflow candidates
for assigned `.github/workflows/` paths and performs no target or GitHub write.

## What It Does

- Designs PR and merge CI workflows.
- Reviews permissions, triggers, concurrency, and checkout behavior.
- Creates release, image, and chart publication workflows.
- Consumes `container` build, platform, cache, SBOM, provenance, vulnerability,
  and verification requirements for image workflows without redesigning the
  image or runtime contract.
- Keeps workflow filenames, service scope, docs, and scripts aligned.
- Applies least-privilege defaults where practical.

## Architecture

```text
Repository automation need
  |
  v
Choose workflow pattern
  |
  v
Render or patch workflow YAML
  |
  v
Align scripts, docs, and changelog
  |
  v
Validate syntax and workflow contracts
```

## Workflow

1. Identify the workflow purpose and trigger model.
2. Check existing workflow conventions in the repository.
3. Apply the smallest workflow change that satisfies the objective.
4. Align related scripts, documentation, and release notes.
5. Validate YAML and GitHub Actions behavior with available local tools.

## Core Concepts

- Prefer service-scoped workflows over broad catch-all automation.
- Use minimal permissions.
- Avoid hardcoded service lists when a catalog or shared source of truth
  already exists.
- Keep bot-safe merge and publish behavior explicit.
- Keep image/runtime design with `container` and registry release execution
  with `publish-image`; this skill owns the GitHub Actions YAML.

## Files

- `SKILL.md`: workflow design and review guidance.
- `assets/`: reusable workflow templates.
- `references/`: workflow pattern guidance.
- `agents/openai.yaml`: UI metadata.
