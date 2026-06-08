---
name: terraform
description: "Use for Terraform repo/module hardening: scaffold, standardize, or improve Terraform project structure, state/backends, environment roots, module interfaces, validation/test strategy, security controls, CI checks, and Terraform best practices. Do not use for onboarding Nebius Terraform modules into nebius-cxcli; use onboard-nebius-cxcli."
---

# Terraform

Generate production-grade Terraform scaffolding and enforce module and environment best practices.

## Scope and Guardrails

- Treat Terraform as infrastructure/platform IaC only (networking, IAM, compute, managed Kubernetes infrastructure, storage, observability foundations).
- Do not implement application deployment workflows in Terraform; recommend GitOps or CI/CD for app rollout.
- Never write real secrets to files or output.
- Never commit secret-bearing `*.tfvars`.
- Prefer vendor-documented Terraform language/provider features.
- Verify behavior against official Terraform documentation before asserting feature support.
- Do not conflate features across Terraform versions.
- State Terraform/backend/provider version constraints explicitly when behavior depends on version/capability.
- Default to fail-fast behavior and one canonical path; do not add backward-compatibility shims unless the user explicitly asks.
- For provider fields and status outputs, confirm support from `terraform providers schema -json` before implementing, and prefer enforcing this check in CI when adding new provider-dependent fields/outputs.

## Workflow

1. Collect missing essentials only; ask concise follow-ups for only what is missing:
   - Project/module name and short purpose.
   - Target Terraform version (default: `>= 1.10.0, < 2.0.0`).
   - Providers and version constraints (child modules: minimum supported versions; root modules: minimum plus explicit upper bounds).
   - Remote state choice:
     - HCP Terraform (`cloud` block) or backend (`s3`, `azurerm`, `gcs`, etc).
     - State naming scheme (`org`, `project`, `env`, `region`) and environments (default: `dev`, `stage`, `prod`).
   - Secret handling policy:
     - Allowed in state for credentials/passwords (default: no), or must be omitted from state/plan where possible.
2. Choose one structure profile and keep it consistent:
   - module-library profile (`modules/*` with `examples/`)
   - environment-roots profile (`envs/*` roots that call shared modules)
3. Implement using the standards below.
4. Provide output in this exact order:
   - Directory tree.
   - Full contents of each created file (one file at a time).
   - Short "How to use" with exact commands (`init`, `plan`, `apply`) and safe environment/var-file handling.
   - Notes on security, state, locking, upgrades, and CI hooks.

## Layout Profiles

Use one of these structures unless the user asks otherwise.

### Profile A: Module Library (preferred for reusable modules)

```text
.
├── README.md
├── CHANGELOG.md
├── .gitignore
├── Makefile
├── modules/
│   ├── <component-a>/
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   ├── outputs.tf
│   │   ├── versions.tf
│   │   ├── locals.tf
│   │   ├── README.md
│   │   └── examples/
│   │       ├── minimal/
│   │       │   ├── main.tf
│   │       │   └── versions.tf
│   │       └── advanced/
│   │           ├── main.tf
│   │           └── versions.tf
│   └── <component-b>/
│       ├── main.tf
│       ├── variables.tf
│       ├── outputs.tf
│       ├── versions.tf
│       ├── locals.tf
│       └── README.md
└── (optional) envs/
```

### Profile B: Environment Roots

```text
.
├── README.md
├── Makefile
├── modules/
│   └── <component>/
│       ├── main.tf
│       ├── variables.tf
│       ├── outputs.tf
│       ├── versions.tf
│       ├── locals.tf
│       └── README.md
└── envs/
    ├── dev/
    │   ├── main.tf
    │   ├── versions.tf
    │   ├── backend.tf
    │   ├── terraform.tfvars.example
    │   └── README.md
    ├── stage/
    │   └── (same as dev)
    └── prod/
        └── (same as dev)
```

If using HCP Terraform `cloud` blocks, omit per-env `backend.tf`.

## Implementation Standards

1. Module consumability and versioning:
   - Make root module externally consumable.
   - Show Git tag source example: `source = "git::<REPO_URL>?ref=vX.Y.Z"`.
   - Show registry example: `source = "<namespace>/<name>/<provider>"` with `version = "~> X.Y"`.
   - Include `CHANGELOG.md` and upgrade expectations.
   - Every module declares `required_providers` with `source` and a minimum version known to work.
   - Child modules: prefer minimum-only constraints unless there is a known hard incompatibility that requires an upper bound.
   - Root modules: set minimum plus explicit upper bounds; `.terraform.lock.hcl` pins exact selected provider versions.
   - For pre-1.0 providers, use explicit upper bounds (for example `>= 0.5.55, < 0.6.0`).
   - Lock file policy:
     - reusable child modules: do not keep `.terraform.lock.hcl` in module directories
     - root configurations where `init` runs (`envs/*`, `examples/*`, validation roots): keep lock files
     - CI must fail on lock drift: run `terraform init -lockfile=readonly` in each root.
     - For mixed dev/CI platforms, add required hashes with `terraform providers lock -platform=<os_arch>` to reduce lock churn.
2. Terraform language structure:
   - Include `main.tf`, `variables.tf`, `outputs.tf`, `versions.tf` at root.
   - `versions.tf` defines `required_version` and `required_providers`.
   - In `variables.tf`, include `type`, `description`, `nullable` where relevant, and validation for critical inputs (names, CIDRs, regions).
   - Validation and assertions:
     - input validation (`>= 0.13`): use variable `validation` blocks for single-input constraints.
     - plan/apply invariants (`>= 1.2`): use `precondition`/`postcondition` for cross-input and resource invariants.
     - operational assertions (`>= 1.5`): use `check` blocks for non-blocking health/invariant checks.
   - Avoid required inputs that are not used by resources.
   - Normalize optional maps/objects before merge/length operations using `coalesce(try(..., null), {})`.
   - Preserve caller metadata objects; when layering labels, merge labels instead of replacing full metadata.
   - Use defaults only for non-sensitive, non-env-specific values.
   - In `outputs.tf`, add descriptions and mark sensitive outputs appropriately.
   - Export integration-critical computed fields when provider schema exposes them (for example endpoints, CA materials).
   - Use `locals.tf` for naming/tag conventions and computed values.
3. Sensitive data handling:
   - Use placeholders in examples; no real secrets.
   - Provide `terraform.tfvars.example` with comments.
   - Prefer not managing secret values in Terraform whenever possible; pass references/metadata and integrate with secret managers.
   - Handle features by Terraform version:
     - Terraform `>= 1.11`: use provider-supported write-only managed resource arguments for secrets that must not persist in plan/state.
     - Terraform `>= 1.10`: use `ephemeral = true` on variables and child module outputs, and use `ephemeral` blocks; ephemeral values are omitted from state/plan and have reference restrictions.
     - Root outputs cannot be `ephemeral`; `terraform output` reads state, so ephemeral values are not for later retrieval from root output/state.
     - Terraform `>= 0.15`: use `sensitive = true` on variables/outputs to redact CLI/HCP UI output only; values still persist in state and plan.
   - Apply this decision logic when secrets must be omitted from state/plan:
     - First: avoid passing secret values through Terraform at all when architecture allows it.
     - If Terraform `>= 1.11` and provider/resource supports write-only arguments, use write-only arguments.
     - Else if Terraform `>= 1.10`, use ephemeral variables, child module outputs, and ephemeral blocks; document reference limits.
     - Else, omission is unsupported; fall back to `sensitive = true`, hardened remote state, and secret-manager injection.
   - `.gitignore` guidance (include and briefly explain intent):
     - `.terraform/`: local working directory and cached backend/provider data.
     - `*.tfstate`, `*.tfstate.*`, `*.tfstate.backup`, `.terraform.tfstate.lock.info`: state and lock artifacts that can contain sensitive data.
     - `*.tfvars`, `*.tfvars.json`, `terraform.tfvars`: local variable files that often contain secrets.
     - `.terraformrc`, `terraform.rc`: local CLI configuration files.
     - `*.tfplan`, `plan.out`: plan artifacts that can embed sensitive values.
4. Remote state and locking:
   - Default to remote state for team/shared infrastructure.
   - Do not hardcode backend credentials.
   - Use partial backend configuration and identity/env credentials.
   - Note backend limitation: backend blocks cannot use variables/locals.
   - Explain locking expectations:
     - `s3`: prefer S3 lockfile-based locking via `use_lockfile = true`; DynamoDB locking is deprecated and should only be used for migration/legacy compatibility.
     - `azurerm`: rely on Azure Blob lease-based native locking.
   - Document safe lock recovery (`force-unlock` only with confirmed lock ID).
5. Environment management:
   - Use separate root modules per environment (`envs/<env>/`) for isolation.
   - Each env calls root module with `source = "../.."` for local development.
   - Also show remote source pinning for real usage (Git tag or registry).
   - Use unique backend key naming per environment.
   - Provide secure variable guidance (CI secrets, secret manager, `TF_VAR_*`, or HCP variables).
   - Do not rely on Terraform workspaces for security-boundary isolation (separate credentials/access controls); prefer separate roots unless explicitly requested otherwise.
6. Refactors and adoption:
   - Safe address refactors (`>= 1.1`): use `moved` blocks for renames/splits of resources/modules to avoid destructive recreation.
   - Treat removal of established `moved` blocks as a breaking change.
   - Existing infrastructure adoption (`>= 1.5`): prefer configuration-driven `import` blocks over ad hoc `terraform import`.
   - For import bootstrapping, optionally use `terraform plan -generate-config-out=<file>` to scaffold configuration before cleanup/hardening.
7. Release strategy:
   - For monorepos with multiple modules, choose one explicit versioning model:
     - single repo-wide SemVer tags
     - per-module tags (for example `<module>/v1.2.3`) with matching VCS refs in `source`
     - registry publishing per module for independent version streams
   - Do not mix strategies implicitly.
8. Quality gates:
   - Recommend at minimum:
     - `terraform fmt -check -recursive`
     - `terraform validate` (module roots)
     - `terraform validate` for each `examples/*` root
     - `tflint` (if enabled)
     - `checkov` or `tfsec`
   - Provide minimal pre-commit and CI setup.
   - Include `Makefile` targets (`fmt`, `validate`, `test-all`) unless user asks not to.
   - Make `validate` non-destructive: `terraform init -backend=false -upgrade=false` before validate.
   - In CI roots, run `terraform init -lockfile=readonly` before plan/validate to prevent silent lockfile rewrites.
   - For provider-dependent outputs/fields (including write-only args), verify assumptions with `terraform providers schema -json`.
   - Run `terraform test` only when test files exist and provider mocking/integration setup is available.
   - Never run `apply`/destroy in tests unless the user explicitly approves integration provisioning.

## Documentation Requirements

`README.md` must include:

- What the module does and does not do.
- Usage examples for local path, Git tag, and registry.
- Inputs/outputs summary and optional `terraform-docs` regeneration note.
- Fail-fast invariants and notable preconditions.
- List of runnable examples (`examples/minimal` and any advanced variants).
- Backend/state expectations and environment workflow.

## Generation Rules

- Use clear placeholders and `TODO` markers where user-specific values are required.
- Keep naming consistent and predictable.
- Root modules own backend config and provider/auth configuration; backend secrets stay in partial `-backend-config` or environment identity, not in VCS.
- Child modules should not configure providers; declare requirements and expected aliases, and let roots pass provider configurations.
- Split into `modules/<component>/` submodules where reuse/separation improves clarity.
- Prefer this secret-handling order and state assumptions explicitly: write-only arguments (when available), then ephemeral values, then `sensitive = true` plus hardened remote state and secret manager.
- Keep output concise and technically precise.
- Include minimal Terraform snippets only when they materially clarify implementation.
- Do not guess provider behavior; if provider support cannot be confirmed from official docs, explicitly state that uncertainty.
- Default to no legacy compatibility layers when changing module contracts unless the user explicitly asks for compatibility.
