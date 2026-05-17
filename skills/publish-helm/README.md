# Publish Helm

`publish-helm` generates a Nebius OCI Helm chart publication flow.

## What It Does

- Creates a chart-local `CHANGELOG.md`.
- Creates a chart-local `publish-helm.sh`.
- Registers the chart in a shared tag-driven GitHub Actions workflow.
- Supports release prep and publication checks.
- Includes public pull verification guidance where applicable.

## Architecture

```text
Helm chart
  |
  +--> chart-local changelog
  +--> publish-helm.sh helper
  `--> shared helm-chart-publish workflow registration
        |
        v
Tag-driven OCI chart publication
```

## Workflow

1. Collect chart name, path, version, registry, and release tag pattern.
2. Create or update chart-local release assets.
3. Register the chart in the shared publish workflow.
4. Validate script syntax, chart metadata, and workflow YAML.
5. Report prep, publish, and verification commands.

## Core Concepts

- Keep chart release notes with the chart.
- Separate release preparation from publication.
- Avoid hardcoded per-chart workflow branches when shared registration is
  available.

## Files

- `SKILL.md`: Helm publication workflow and guardrails.
- `assets/`: changelog and publish helper templates.
- `agents/openai.yaml`: UI metadata.
