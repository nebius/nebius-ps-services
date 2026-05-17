# Onboard Nebius CXCLI

`onboard-nebius-cxcli` guides onboarding a Nebius Terraform module into
`nebius-cxcli`.

## What It Does

- Decides whether onboarding can stay catalog-only or needs code-owned layers.
- Aligns component sources, CLI settings, wizard behavior, validation,
  deployment status, cluster handoffs, tests, and docs.
- Keeps module onboarding tied to actual `nebius-cxcli` contracts.

## Architecture

```text
Terraform module
  |
  v
component_sources.yaml and component_cli_settings.yaml
  |
  +--> optional wizard/provider code
  +--> optional validation profiles
  +--> optional runtime validation
  +--> optional status and handoff code
  |
  v
tests, docs, and changelog alignment
```

## Workflow

1. Inspect the module and existing component catalog patterns.
2. Decide whether catalog-only onboarding is sufficient.
3. Patch catalog entries and only the code-owned layers that are required.
4. Update focused tests, docs, and changelog entries.
5. Validate generated configs and affected CLI behavior.

## Core Concepts

- Prefer catalog-first onboarding.
- Do not hardcode component behavior when catalog data can express it.
- Keep generated artifacts, docs, and CLI contracts aligned.

## Files

- `SKILL.md`: onboarding workflow and guardrails.
- `references/touchpoints.md`: detailed cxcli touchpoint map.
- `agents/openai.yaml`: UI metadata.
