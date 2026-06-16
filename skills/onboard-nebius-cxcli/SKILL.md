---
name: onboard-nebius-cxcli
description: "Use to onboard a Nebius Terraform module or infra component into nebius-cxcli: component catalog, component_sources.yaml, component add, render/deploy wiring, generated roots, catalog-only vs code-owned layers, wizard_profiles.py, provider_options.py, cli.py, runtime validation, cluster handoffs, deployment status, tests, and docs."
---

# Onboard Nebius CXCLI

Use this skill when the task is to add or review a Nebius Terraform module as a
`nebius-cxcli` infra component.

## Use This Skill For

- Adding a new Terraform module to `services/nebius-cxcli/component_sources.yaml`
- Reviewing whether a module needs only catalog changes or also Python code
- Converting guided fields from static choices to live Nebius provider lookups
- Hardening bundled wizard UX for common or base components
- Adding status polling, runtime validation, or cluster handoff behavior for a
  new infra component

Start with `references/touchpoints.md`.

## Workflow

1. Inspect the Terraform module first.
   Read the module `README.md`, `variables.tf`, `outputs.tf`, and any
   `locals.tf` or `main.tf` files that define effective defaults, validation
   rules, preconditions, resource kinds, or hidden sizing abstractions.
2. Default to catalog-only onboarding.
   Add `components.infra.<component-id>` in
   `services/nebius-cxcli/component_sources.yaml` with `source`, `ui`, and
   optional `status`, `defaults`, `wizard_profile`, `wizard`, and `input`.
3. Keep internal dispatch out of the public catalog.
   Do not add public `validation`, `runtime`, `handoff`, or legacy
   `resource_kind` fields.
4. Prefer Terraform introspection unless guided choices are necessary.
   If module inputs are already clear enough, omit both `wizard_profile` and
   `wizard`. Add a built-in `wizard_profile` when the bundled component is
   common enough that the default UX should be intentional rather than purely
   schema-driven.
5. Prefer live provider lookups for cloud inventory and compatibility-bound
   choices.
   Reuse `services/nebius-cxcli/src/nebius_cxcli/provider_options.py` before
   inventing new sources. Keep static lists only for fixed service enums or
   module-owned abstractions.
6. Audit the create/render/deploy path, not just the catalog entry.
   - If enabling one field should reveal follow-up prompts, make sure the
     wizard can expand those prompts in the same pass instead of leaving them
     for a later rerun.
   - If Terraform has conditional preconditions, decide whether the CLI should
     fail earlier with runtime validation so users do not discover basic shape
     errors only at `terraform apply`.
   - If optional downstream output can be empty, make render/deploy treat that
     as an explicit no-op instead of emitting invalid artifacts.
7. Add code-owned layers only when the module contract demands them.
   - runtime validation: `validation_profiles.py` plus
     `runtime_component_validation.py`
   - generic wizard or orchestration behavior: `cli.py`
   - cluster handoff: `cluster_handoffs.py`
   - new watcher kind: `deployment_status.py`
8. Update tests and docs in the same turn.
   Touch focused tests plus `README.md`, `docs/design.md`, and `CHANGELOG.md`
   under `services/nebius-cxcli`.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings back
into this skill's local source materials before completion when the current task
contract allows source edits. Update the narrowest appropriate surface:
`SKILL.md` for runtime rules, `references/` for detailed guidance, `assets/`
for reusable templates, `scripts/` for deterministic helpers, and README or
changelog entries for human-facing or release-note updates.

If the current task is explicitly read-only/report-only, or source writes are
outside this skill's task contract, do not edit skill sources; report the
skipped source update instead.

Do not capture secrets, private URLs, customer data, raw logs, one-off local
state, or unverified/vendor-specific claims. If a useful learning is not safe,
not evidence-backed, or outside this skill's scope, report that it was skipped.

## Guardrails

- `wizard_profile` names are one-to-one with infra component ids.
- Terraform outputs are auto-discovered; do not add manual public output
  declarations to the catalog.
- Use `defaults` only for values that should be preseeded or shown explicitly in
  the wizard. Do not rely on hidden defaults to satisfy conditional branches.
- Mirror user-facing conditional requirements in the CLI when the wizard could
  otherwise generate a Terraform-invalid combination.
- Do not add backward-compatibility shims for old catalog fields unless the
  user explicitly asks.

## Validation

- Run the specific `pytest` modules for the touched layer.
- Run
  `python -m nebius_cxcli validate-sources component_sources.yaml`
  from `services/nebius-cxcli` when local module paths and external tools are
  available.

Use the focused test map in `references/touchpoints.md`.

## Output Contract

When using this skill, report:

- whether onboarding stayed catalog-only or required code-owned layers
- component catalog changes made or reviewed
- wizard, provider lookup, validation, status, or handoff touchpoints changed
- tests and `validate-sources` checks run
- docs and changelog surfaces updated
- any unresolved live-provider, Terraform, or deploy-path uncertainty
