# Nebius

`nebius` supports Nebius cloud automation with the Nebius SDK and related
infrastructure workflows.

## What It Does

- Builds and reviews Nebius IAM, Object Storage, VPC, route, and quota
  automation.
- Defines a canonical CLI project-selection fallback through the config-owned
  current profile without parsing the CLI's YAML configuration directly.
- Helps design service accounts, access keys, Terraform state buckets, and
  auth bootstrap flows.
- Captures the current Nebius VPC pool/subnet model for explicit subnet CIDRs,
  inherited network-pool mode, live allocation overlap checks, pool-tree
  compatibility, and safe parent private-pool extension.
- Supports MK8s GPU compatibility, quota readiness, and operator decisions.
- Documents observability endpoints and onboarding patterns.

## Architecture

```text
Nebius task
  |
  v
Select domain reference or helper
  |
  +--> IAM and auth
  +--> VPC and routes
  +--> quotas and capacity
  +--> observability
  `--> MK8s GPU readiness
  |
  v
Implement, inspect, or validate with least exposure
```

## Workflow

1. Identify the Nebius domain and safety level.
2. Load only the relevant reference file or asset.
3. Prefer SDK-supported behavior and documented API contracts.
4. Avoid printing secrets or live sensitive identifiers.
5. Run local or disposable validation when safe.
6. Report cleanup, residual risk, and any unverified live behavior.

## Core Concepts

- Treat live cloud changes as high impact.
- Use placeholders in public docs.
- Prefer environment-driven or catalog-driven behavior over hardcoding.
- Prefer an explicit task project. When a workflow permits the user's CLI
  default as authority, resolve it with `nebius profile current` followed by
  `nebius config get parent-id --profile <profile>`.
- For VPC automation, distinguish parent network private pools, explicit
  subnet child pools, inherited subnet mode, and existing live allocations
  before proposing CIDRs or changing pools.
- Cleanup evidence matters when live resources are created.

## Files

- `SKILL.md`: Nebius workflow, guardrails, assets, and references.
- `references/`: focused Nebius domain guidance.
- `scripts/`: read-only inspection helpers.
- `assets/`: reusable IAM, observability, and GPU examples.
- `agents/openai.yaml`: UI metadata.
