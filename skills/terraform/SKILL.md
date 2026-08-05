---
name: terraform
description: "Use for Terraform repo/module hardening: scaffold, standardize, or improve Terraform project structure, state/backends, environment roots, module interfaces, validation/test strategy, security controls, CI checks, and Terraform best practices. Do not use for nebius-cxcli wizard, validation, status, or component-catalog wiring unless the task is Terraform module structure."
---

# Terraform

## Help

For `$terraform --help` or `$terraform -h`, return concise help and stop before
any workflow step. Include the purpose, invocation policy, public usage/actions,
and `-h, --help` plus only documented skill-level options; say "No additional
public flags" when none exist. For internal or coordinator-only skills, state
that boundary and that no standalone public workflow action exists. After the
selected `SKILL.md` is loaded, help is report-only: do not call any additional
tools, inspect project state, or modify files, private state, Git, or external
systems. Never
expose private helper actions or treat help as workflow authorization.

## Purpose

Generate production-grade Terraform scaffolding and enforce module and environment best practices.

## Invocation Scope

- `standalone`: own the selected Terraform repository or Terraform root and
  retain the normal output contract.
- `coordinated-candidate`: receive the assigned Terraform root, exact path
  ownership, root exclusions, and private bundle from `scaffold-project`;
  generate exact candidates only in that bundle and never write the target.

In coordinated-candidate scope, treat layout profiles as relative to the
assigned Terraform root. Never claim repository-root README, `.gitignore`,
Makefile, `.github/`, Helm, application source, containers, or agent
instructions. Return those integration requirements to their root owner.
Exact assignments may include Terraform-local files, such as a Makefile or
`.gitignore` below the repository root.

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
3. Read `references/terraform-standards.md` before creating, hardening, or
   reviewing Terraform files, then implement using those standards.
4. Provide output in this exact order:
   - Directory tree.
   - Full contents of each created file (one file at a time).
   - Short "How to use" with exact commands (`init`, `plan`, `apply`) and safe environment/var-file handling.
   - Notes on security, state, locking, upgrades, and CI hooks.

In coordinated-candidate scope, replace direct file output with candidate
path, mode, provenance, and validation requirements for the assigned paths.

## Standards Reference

Read `references/terraform-standards.md` before creating, hardening, or
reviewing Terraform files. It owns layout profiles, implementation standards,
documentation requirements, generation rules, secret-handling decision logic,
remote state/locking guidance, environment management, refactor/import
guidance, release strategy, and quality gates.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.
