# Terraform

`terraform` generates and hardens Terraform modules and infrastructure
repositories.

In `coordinated-candidate` scope it emits exact private candidates under an
assigned Terraform root and does not claim shared repository-root artifacts.

## What It Does

- Scaffolds module and multi-environment Terraform layouts.
- Reviews state, backend, provider, variable, output, and validation design.
- Adds examples, documentation, CI checks, and security guardrails.
- Keeps generated Terraform idiomatic and maintainable.

## Architecture

```text
Terraform request
  |
  v
Choose layout profile
  |
  +--> reusable module
  +--> environment repository
  `--> examples and tests
        |
        v
Validation, docs, and CI alignment
```

## Workflow

1. Identify whether the target is a module or environment repo.
2. Inspect existing Terraform files and provider constraints.
3. Add or patch layout, variables, outputs, examples, and docs.
4. Run `terraform fmt` and `terraform validate` when available.
5. Report any skipped plan/apply steps and required credentials.

## Core Concepts

- Prefer explicit variables, outputs, and validation blocks.
- Keep state/backend guidance clear and environment-specific.
- Do not run apply-like operations without explicit confirmation.
- Avoid hardcoded provider or environment values in reusable modules.

## Files

- `SKILL.md`: Terraform structure, workflow, and guardrails.
- `agents/openai.yaml`: UI metadata.
- `references/terraform-standards.md`: layout profiles, implementation
  standards, documentation requirements, generation rules, and quality gates.
