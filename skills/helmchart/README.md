# Helm Chart

`helmchart` reviews, hardens, refactors, creates, lints, and standardizes Helm
charts.

## What It Does

- Reviews chart metadata, values, schemas, templates, notes, and README files.
- Checks Kubernetes and Helm rendering behavior.
- Improves values design, validation, security context, RBAC, and chart CI.
- Uses focused Helm commands to validate actual rendered output.

## Architecture

```text
Helm chart
  |
  +--> Chart.yaml
  +--> values.yaml and values.schema.json
  +--> templates/
  +--> README and NOTES
  `--> chart CI
        |
        v
helmchart applies review, patch, and validation workflow
```

## Workflow

1. Inspect chart structure and chart-owned files.
2. Review values and template contracts.
3. Patch only chart surfaces that are in scope.
4. Run focused `helm lint` and `helm template` validation.
5. Report rendered behavior, remaining risks, and follow-up checks.

## Core Concepts

- Validate Helm whitespace and conditional logic by rendering, not by visual
  indentation alone.
- Keep upstream-owned and chart-owned surfaces separate.
- Avoid changing plain Kubernetes, Kustomize, Docker, or Terraform files unless
  the task explicitly connects them to the chart.

## Files

- `SKILL.md`: chart workflow, validation commands, and guardrails.
- `scripts/validate-chart.sh`: reusable chart validation helper.
- `references/best-practices-checklist.md`: chart review checklist.
- `evals/trigger-prompts.csv`: trigger examples.
- `agents/openai.yaml`: UI metadata.
