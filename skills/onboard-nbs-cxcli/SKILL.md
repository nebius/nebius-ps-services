---
name: onboard-nbs-cxcli
description: Onboard a Nebius Terraform module into nebius-cxcli. Use when adding a new Terraform-based infra component to component_sources.yaml, deciding whether onboarding can stay catalog-only or must also touch wizard_profiles.py, provider_options.py, validation_profiles.py, runtime_component_validation.py, cluster_handoffs.py, deployment_status.py, and the focused tests/docs that enforce this contract.
---

# Onboard NBS CXCLI

Use this skill when the task is to add or review a Nebius Terraform module as a
`nebius-cxcli` infra component.

## Use This Skill For

- Adding a new Terraform module to `services/nebius-cxcli/component_sources.yaml`
- Reviewing whether a module needs only catalog changes or also Python code
- Converting guided fields from static choices to live Nebius provider lookups
- Adding status polling, runtime validation, or cluster handoff behavior for a
  new infra component

Start with `references/touchpoints.md`.

## Workflow

1. Inspect the Terraform module first.
   Read `variables.tf`, `outputs.tf`, and any `locals.tf` or `main.tf` files
   that define effective defaults, resource kinds, or hidden sizing
   abstractions.
2. Default to catalog-only onboarding.
   Add `components.infra.<component-id>` in
   `services/nebius-cxcli/component_sources.yaml` with `source`, `ui`, and
   optional `status`, `defaults`, `wizard_profile`, `wizard`, and `input`.
3. Keep internal dispatch out of the public catalog.
   Do not add public `validation`, `runtime`, `handoff`, or legacy
   `resource_kind` fields.
4. Prefer Terraform introspection unless guided choices are necessary.
   If module inputs are already clear enough, omit both `wizard_profile` and
   `wizard`.
5. Prefer live provider lookups for cloud inventory.
   Reuse `services/nebius-cxcli/src/nebius_cxcli/provider_options.py` before
   inventing new sources. Keep static lists only for fixed service enums or
   module-owned abstractions.
6. Add code-owned layers only when the module contract demands them.
   - runtime validation: `validation_profiles.py` plus
     `runtime_component_validation.py`
   - cluster handoff: `cluster_handoffs.py`
   - new watcher kind: `deployment_status.py`
7. Update tests and docs in the same turn.
   Touch focused tests plus `README.md`, `docs/design.md`, and `CHANGELOG.md`
   under `services/nebius-cxcli`.

## Guardrails

- `wizard_profile` names are one-to-one with infra component ids.
- Terraform outputs are auto-discovered; do not add manual public output
  declarations to the catalog.
- Use `defaults` only for values that should be preseeded or shown explicitly in
  the wizard.
- Do not add backward-compatibility shims for old catalog fields unless the
  user explicitly asks.

## Validation

- Run the specific `pytest` modules for the touched layer.
- Run
  `python -m nebius_cxcli validate-sources component_sources.yaml`
  from `services/nebius-cxcli` when local module paths and external tools are
  available.

Use the focused test map in `references/touchpoints.md`.
