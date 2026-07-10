# Terraform Standards

Read this file before `terraform` creates, hardens, or reviews Terraform files.
It owns layout profiles, implementation standards, documentation requirements,
generation rules, and quality gates.

## Layout Profiles

Use one of these structures unless the user asks otherwise.

### Profile A: Module Library

Prefer this profile for reusable modules.

```text
.
|-- README.md
|-- CHANGELOG.md
|-- .gitignore
|-- Makefile
|-- modules/
|   |-- <component-a>/
|   |   |-- main.tf
|   |   |-- variables.tf
|   |   |-- outputs.tf
|   |   |-- versions.tf
|   |   |-- locals.tf
|   |   |-- README.md
|   |   `-- examples/
|   |       |-- minimal/
|   |       |   |-- main.tf
|   |       |   `-- versions.tf
|   |       `-- advanced/
|   |           |-- main.tf
|   |           `-- versions.tf
|   `-- <component-b>/
|       |-- main.tf
|       |-- variables.tf
|       |-- outputs.tf
|       |-- versions.tf
|       |-- locals.tf
|       `-- README.md
`-- (optional) envs/
```

### Profile B: Environment Roots

```text
.
|-- README.md
|-- Makefile
|-- modules/
|   `-- <component>/
|       |-- main.tf
|       |-- variables.tf
|       |-- outputs.tf
|       |-- versions.tf
|       |-- locals.tf
|       `-- README.md
`-- envs/
    |-- dev/
    |   |-- main.tf
    |   |-- versions.tf
    |   |-- backend.tf
    |   |-- terraform.tfvars.example
    |   `-- README.md
    |-- stage/
    |   `-- (same as dev)
    `-- prod/
        `-- (same as dev)
```

If using HCP Terraform `cloud` blocks, omit per-env `backend.tf`.

## Implementation Standards

### Module Consumability And Versioning

- Make root module externally consumable.
- Show Git tag source example: `source = "git::<REPO_URL>?ref=vX.Y.Z"`.
- Show registry example: `source = "<namespace>/<name>/<provider>"` with
  `version = "~> X.Y"`.
- Include `CHANGELOG.md` and upgrade expectations.
- Every module declares `required_providers` with `source` and a minimum
  version known to work.
- Child modules: prefer minimum-only constraints unless there is a known hard
  incompatibility that requires an upper bound.
- Root modules: set minimum plus explicit upper bounds; `.terraform.lock.hcl`
  pins exact selected provider versions.
- For pre-1.0 providers, use explicit upper bounds, for example
  `>= 0.5.55, < 0.6.0`.
- Reusable child modules: do not keep `.terraform.lock.hcl` in module
  directories.
- Root configurations where `init` runs, such as `envs/*`, `examples/*`, and
  validation roots, should keep lock files.
- CI must fail on lock drift with `terraform init -lockfile=readonly` in each
  root.
- For mixed dev/CI platforms, add required hashes with
  `terraform providers lock -platform=<os_arch>` to reduce lock churn.

### Terraform Language Structure

- Include `main.tf`, `variables.tf`, `outputs.tf`, and `versions.tf` at root.
- `versions.tf` defines `required_version` and `required_providers`.
- In `variables.tf`, include `type`, `description`, `nullable` where relevant,
  and validation for critical inputs such as names, CIDRs, and regions.
- Input validation (`>= 0.13`): use variable `validation` blocks for
  single-input constraints.
- Plan/apply invariants (`>= 1.2`): use `precondition` and `postcondition` for
  cross-input and resource invariants.
- Operational assertions (`>= 1.5`): use `check` blocks for non-blocking
  health and invariant checks.
- Avoid required inputs that are not used by resources.
- Normalize optional maps/objects before merge or length operations using
  `coalesce(try(..., null), {})`.
- Preserve caller metadata objects; when layering labels, merge labels instead
  of replacing full metadata.
- Use defaults only for non-sensitive, non-env-specific values.
- In `outputs.tf`, add descriptions and mark sensitive outputs appropriately.
- Export integration-critical computed fields when provider schema exposes
  them, for example endpoints or CA materials.
- Use `locals.tf` for naming/tag conventions and computed values.

### Sensitive Data Handling

- Use placeholders in examples; no real secrets.
- Provide `terraform.tfvars.example` with comments.
- Prefer not managing secret values in Terraform whenever possible; pass
  references/metadata and integrate with secret managers.
- Terraform `>= 1.11`: use provider-supported write-only managed resource
  arguments for secrets that must not persist in plan/state.
- Terraform `>= 1.10`: use `ephemeral = true` on variables and child module
  outputs, and use `ephemeral` blocks; ephemeral values are omitted from
  state/plan and have reference restrictions.
- Root outputs cannot be `ephemeral`; `terraform output` reads state, so
  ephemeral values are not for later retrieval from root output/state.
- Terraform `>= 0.15`: use `sensitive = true` on variables/outputs to redact
  CLI/HCP UI output only; values still persist in state and plan.
- Secret omission decision logic:
  1. Avoid passing secret values through Terraform when architecture allows it.
  2. If Terraform `>= 1.11` and the provider/resource supports write-only
     arguments, use write-only arguments.
  3. Else if Terraform `>= 1.10`, use ephemeral variables, child module
     outputs, and ephemeral blocks; document reference limits.
  4. Else, omission is unsupported; fall back to `sensitive = true`, hardened
     remote state, and secret-manager injection.
- `.gitignore` should cover `.terraform/`, `*.tfstate`, `*.tfstate.*`,
  `*.tfstate.backup`, `.terraform.tfstate.lock.info`, `*.tfvars`,
  `*.tfvars.json`, `terraform.tfvars`, `.terraformrc`, `terraform.rc`,
  `*.tfplan`, and `plan.out`.

## Remote State And Locking

- Default to remote state for team/shared infrastructure.
- Do not hardcode backend credentials.
- Use partial backend configuration and identity/env credentials.
- Backend blocks cannot use variables/locals.
- `s3`: prefer S3 lockfile-based locking via `use_lockfile = true`; DynamoDB
  locking is deprecated and should only be used for migration or legacy
  compatibility.
- `azurerm`: rely on Azure Blob lease-based native locking.
- Document safe lock recovery with `force-unlock` only after confirming the
  lock ID.

## Environment Management

- Use separate root modules per environment, such as `envs/<env>/`, for
  isolation.
- Each env calls root module with `source = "../.."` for local development.
- Also show remote source pinning for real usage, using a Git tag or registry.
- Use unique backend key naming per environment.
- Provide secure variable guidance through CI secrets, secret manager,
  `TF_VAR_*`, or HCP variables.
- Do not rely on Terraform workspaces for security-boundary isolation with
  separate credentials/access controls; prefer separate roots unless
  explicitly requested otherwise.

## Refactors And Adoption

- Safe address refactors (`>= 1.1`): use `moved` blocks for renames/splits of
  resources/modules to avoid destructive recreation.
- Treat removal of established `moved` blocks as a breaking change.
- Existing infrastructure adoption (`>= 1.5`): prefer configuration-driven
  `import` blocks over ad hoc `terraform import`.
- For import bootstrapping, optionally use
  `terraform plan -generate-config-out=<file>` to scaffold configuration
  before cleanup/hardening.

## Release Strategy

For monorepos with multiple modules, choose one explicit versioning model:

- single repo-wide SemVer tags
- per-module tags, for example `<module>/v1.2.3`, with matching VCS refs in
  `source`
- registry publishing per module for independent version streams

Do not mix strategies implicitly.

## Quality Gates

- Recommend at minimum:
  - `terraform fmt -check -recursive`
  - `terraform validate` for module roots
  - `terraform validate` for each `examples/*` root
  - `tflint` if enabled
  - `checkov` or `tfsec`
- Provide minimal pre-commit and CI setup.
- Include `Makefile` targets such as `fmt`, `validate`, and `test-all` unless
  the user asks not to.
- Make `validate` non-destructive: `terraform init -backend=false -upgrade=false`
  before validate.
- In CI roots, run `terraform init -lockfile=readonly` before plan/validate to
  prevent silent lockfile rewrites.
- For provider-dependent outputs/fields, including write-only args, verify
  assumptions with `terraform providers schema -json`.
- Run `terraform test` only when test files exist and provider
  mocking/integration setup is available.
- Never run `apply` or `destroy` in tests unless the user explicitly approves
  integration provisioning.

## Documentation Requirements

`README.md` must include:

- What the module does and does not do.
- Usage examples for local path, Git tag, and registry.
- Inputs/outputs summary and optional `terraform-docs` regeneration note.
- Fail-fast invariants and notable preconditions.
- List of runnable examples, such as `examples/minimal` and any advanced
  variants.
- Backend/state expectations and environment workflow.

## Generation Rules

- Use clear placeholders and `TODO` markers where user-specific values are
  required.
- Keep naming consistent and predictable.
- Root modules own backend config and provider/auth configuration; backend
  secrets stay in partial `-backend-config` or environment identity, not in
  VCS.
- Child modules should not configure providers; declare requirements and
  expected aliases, and let roots pass provider configurations.
- Split into `modules/<component>/` submodules where reuse/separation improves
  clarity.
- Prefer this secret-handling order and state assumptions explicitly:
  write-only arguments when available, then ephemeral values, then
  `sensitive = true` plus hardened remote state and secret manager.
- Keep output concise and technically precise.
- Include minimal Terraform snippets only when they materially clarify
  implementation.
- Do not guess provider behavior; if provider support cannot be confirmed from
  official docs, explicitly state that uncertainty.
- Default to no legacy compatibility layers when changing module contracts
  unless the user explicitly asks for compatibility.
