# Onboarding Touchpoints

Use this file as the repo-specific map for onboarding a Nebius Terraform module
into `services/nebius-cxcli`.

## Start Here

Open these files first:

- `services/nebius-cxcli/component_sources.yaml`
- `services/nebius-cxcli/README.md`
- `services/nebius-cxcli/docs/design.md`
- `services/nebius-cxcli/src/nebius_cxcli/component_sources.py`
- the target Terraform module's `README.md`, `variables.tf`, `outputs.tf`,
  `locals.tf`, and `main.tf`

## Public Catalog Contract

Minimal infra entry:

```yaml
components:
  infra:
    <component-id>:
      source:
        portable: git::https://github.com/org/repo.git//modules/<module>?ref=<ref>
        local: ../../platform-infra/modules/<module>
      ui:
        title: Human title
        group: Compute
        enabled: false
      status:
        kind: nebius.service.kind
        parent_input: parent_id
        name_input: name
      defaults:
        inputs.some_field: some-value
      wizard:
        inputs.some_lookup_field:
          options:
            from: project_networks
      input:
        inputs.some_consumer_field: other-component.output_alias
```

Relevant rules:

- `component_sources.yaml` is strict-schema. Unsupported keys fail fast.
- Public infra keys are `source`, `ui`, `status`, `defaults`, `wizard_profile`,
  `wizard`, and `input`.
- Outputs are auto-discovered from Terraform outputs.
- Public `validation`, `runtime`, `handoff`, and `resource_kind` are not
  supported.

## Decide Whether Catalog-Only Is Enough

Catalog-only onboarding is usually enough when:

- the module inputs are already well-described in Terraform
- required values can be entered as normal strings, bools, numbers, lists, or
  maps
- no feature toggle or upstream selection needs same-pass prompt expansion to
  reveal dependent fields
- status polling can be expressed with an existing `status.kind`
- no cross-field runtime rule is needed beyond Terraform validation
- no deploy/bootstrap cluster handoff is needed
- empty optional outputs do not require special render/apply handling

If all of those are true, touch:

- `services/nebius-cxcli/component_sources.yaml`
- focused tests
- `services/nebius-cxcli/README.md`
- `services/nebius-cxcli/docs/design.md`
- `services/nebius-cxcli/CHANGELOG.md`

## Code-Owned Layers

### `services/nebius-cxcli/src/nebius_cxcli/wizard_profiles.py`

Touch this when a bundled infra component needs a reusable built-in
`wizard_profile`.

Use it for:

- repeated component-specific guided choices
- existing live provider lookups that should be wired by default
- common or base components where bundled UX should be intentional
- stable fixed enums that belong in the bundled UX
- exposing important module defaults explicitly in the wizard

Do not use it to create new Terraform inputs.

### `services/nebius-cxcli/src/nebius_cxcli/provider_options.py`

Touch this only when no existing provider source can fetch the right live
choices.

Check existing sources first:

- `project_subnets`
- `project_networks`
- `tenant_projects`
- `mk8s_control_plane_versions`
- `mk8s_compatible_platforms`
- `compute_platforms`
- `compute_platform_presets`

Provider-layer changes are appropriate for:

- project-scoped Nebius inventory
- compatibility-filtered live choices
- chained lookups driven by another selected field
- dynamic lookup paths that must resolve against sibling or override fields

Keep provider args path-based so the CLI can normalize them correctly:

- `project_id_path`
- `fallback_project_id_path`
- `platform_path`
- `version_path`

### `services/nebius-cxcli/src/nebius_cxcli/cli.py`

Touch this when onboarding exposes a generic wizard or orchestration gap rather
than a component-local catalog issue.

Typical reasons:

- dependent prompts must appear immediately after a toggle or upstream choice
- prompt ordering needs to account for newly required fields
- render/apply should treat an empty generated artifact set as a no-op
- a provider-backed field now depends on normalized path resolution in the
  wizard loop

### `services/nebius-cxcli/src/nebius_cxcli/validation_profiles.py`

Add a mapping here only when the component needs code-owned runtime rules.

### `services/nebius-cxcli/src/nebius_cxcli/runtime_component_validation.py`

Touch this when the module needs cross-field validation that Terraform alone
cannot express cleanly at CLI/runtime level, or when the wizard can otherwise
produce a Terraform-invalid conditional shape.

### `services/nebius-cxcli/src/nebius_cxcli/cluster_handoffs.py`

Touch this only when the component produces a cluster contract that local
deploy/bootstrap flows must consume.

### `services/nebius-cxcli/src/nebius_cxcli/deployment_status.py`

Touch this when the new `status.kind` needs a watcher that is not already
implemented.

## Static vs Live Choice Rule

Prefer live provider lookups when the field represents live Nebius inventory,
availability, or compatibility.

Keep static lists when the field is:

- a fixed service enum
- a module-owned abstraction layer
- a policy/default choice rather than a cloud inventory resource

Current examples:

- `managed-postgresql.inputs.tier` stays static because it maps to module-owned
  sizing profiles in `platform-infra/modules/managed-postgresql/locals.tf`.
- `object-storage.inputs.versioning_policy` and `inputs.object_audit_logging`
  stay static because they are fixed service enums in the module contract.
- jump-host `platform` and `preset` are live-lookups because they represent
  current compute inventory.
- MK8s `k8s_version`, platform, and preset guidance should stay provider-backed
  because they depend on current compatibility and available inventory.

## End-to-End UX Checks

Before calling onboarding complete, walk the user path:

- `create`: does the wizard surface every field required by an enabled feature
  in the same run
- `render`: does the generated output stay syntactically valid when optional
  sections are empty
- `deploy`: does the first failure happen in CLI validation instead of at an
  avoidable Terraform precondition when practical

## Focused Test Map

Update and run the tests that match the touched layer:

- `services/nebius-cxcli/tests/test_component_sources.py`
- `services/nebius-cxcli/tests/test_components_runtime_discovery.py`
- `services/nebius-cxcli/tests/test_wizard_provider_field_specs.py`
- `services/nebius-cxcli/tests/test_tf_variable_discovery_and_provider_checks.py`
- `services/nebius-cxcli/tests/test_provider_option_plugins.py`
- `services/nebius-cxcli/tests/test_strict_runtime_dynamic_validation.py`
- `services/nebius-cxcli/tests/test_runtime_plugin_validation.py`
- `services/nebius-cxcli/tests/test_deployment_status.py`
- `services/nebius-cxcli/tests/test_render.py`
- `services/nebius-cxcli/tests/test_cli_command_coverage.py`

## Common Validation Commands

Focused test pattern:

```bash
cd services/nebius-cxcli
python -m pytest tests/test_component_sources.py tests/test_wizard_provider_field_specs.py
```

Provider/wizard-heavy changes:

```bash
cd services/nebius-cxcli
python -m pytest \
  tests/test_cli_command_coverage.py \
  tests/test_provider_option_plugins.py \
  tests/test_tf_variable_discovery_and_provider_checks.py \
  tests/test_runtime_plugin_validation.py \
  tests/test_strict_runtime_dynamic_validation.py \
  tests/test_wizard_provider_field_specs.py \
  tests/test_wizard_prompt_interrupts.py
```

Catalog and source-contract validation:

```bash
cd services/nebius-cxcli
python -m nebius_cxcli validate-sources component_sources.yaml
```
