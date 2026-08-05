---
name: apply-security
description: "Use as a security reviewer, adviser, and safe remediation helper for software design, implementation, code review, infrastructure, deployment, Helm, Kubernetes, Terraform, CI/CD workflows, Bash scripts, and application code in Python, Java, JavaScript, TypeScript, or Rust. Use when work may affect secrets, public exposure, IAM/RBAC, auth, input handling, injection, deserialization, crypto, dependencies, containers, workflows, or supply-chain risk. Advises implicitly; patches only when the current task allows edits and the fix is low-risk."
---

# Apply Security

## Help

For `$apply-security --help` or `$apply-security -h`, return concise help and stop before
any workflow step. Include the purpose, invocation policy, public usage/actions,
and `-h, --help` plus only documented skill-level options; say "No additional
public flags" when none exist. For internal or coordinator-only skills, state
that boundary and that no standalone public workflow action exists. After the
selected `SKILL.md` is loaded, help is report-only: do not call any additional
tools, inspect project state, or modify files, private state, Git, or external
systems. Never
expose private helper actions or treat help as workflow authorization.

## Purpose

Review codebases for practical security risk and apply minimal,
behavior-preserving remediations. Prefer secure defaults, controlled opt-ins,
and clear exceptions over removing features or rewriting unrelated code.

Act as the general security reviewer for agent sessions that involve design,
implementation, review, validation, infrastructure, deployment, or automation
work. When loaded implicitly, stay inside the current task scope and look for
security implications before or alongside normal engineering work.

## Invocation Policy

Implicit invocation is allowed. When selected implicitly, default to security
advice and changed-scope review for the current design, code, diff, or
implementation task.

Do not expand into a broad full-repository scan unless the user asks for a
repository-wide scan or the coordinator skill explicitly defines that scope.
Do not patch merely because this skill was loaded implicitly. Apply changes
only when the user request or current workflow already authorizes edits, and
only when the fix satisfies this skill's safe auto-fix policy. Plan first and
ask for explicit approval for high-risk or externally visible public exposure,
IAM/RBAC, auth, crypto, serialization, database, availability, credential, or
external-route changes. The low-risk direct-patch examples below remain valid
when they are compatible with the current task scope and repository contract.

## Use This Skill For

- Security review and advice during design, implementation, code review, and
  post-change validation sessions.
- Security scans across Terraform, Kubernetes manifests, Helm charts, CI/CD
  workflows, shell scripts, and application code.
- Remediation plans that identify exact files, risk, fix strategy, expected
  feature impact, and validation.
- Safe patching for small, reviewable changes such as redaction, least
  privilege workflow permissions, non-breaking Helm values, compatible
  security contexts, and obvious shell quoting fixes.
- Verification of security patches with repository-native checks.
- Explaining findings, tradeoffs, exceptions, and safe override designs.

## Non-Goals

- Do not treat this skill as a replacement for threat modeling, penetration
  testing, formal compliance attestation, or project-specific security policy.
- Do not take over purely non-security style, formatting, documentation, or
  generic code-quality work when no security-relevant surface is in scope.
- Do not rotate credentials, rewrite history, delete resources, disable
  features, change auth flows, or alter externally visible behavior without
  explicit user approval.
- Do not invent cloud, framework, CLI, package-manager, or scanner behavior.
  Verify version-sensitive behavior against current official docs or mark it
  unverified.

## Inputs Accepted

- A repository, branch, PR, diff, directory, file list, chart, module, workflow,
  or pasted code.
- A design, architecture note, implementation plan, requirements document,
  threat-sensitive feature, or current changed scope.
- Optional mode: `scan`, `plan`, `patch`, `verify`, or `explain`.
- Optional output format: Markdown, JSON, or SARIF-style findings.
- Optional constraints: target environment, production sensitivity, approved
  scanners, files to exclude, or changes the user explicitly approves.

Default to `scan` when the user asks for a review and does not ask for edits.
Default to `plan` when the user asks what should be fixed. Use `patch` only
when the user asks to fix, harden, remediate, or apply changes.

When invoked implicitly during an implementation or design task, default to
advisory review of the current changed or proposed scope. If edits are already
allowed by the active task, apply only safe low-risk remediations and report
larger security concerns as findings or approval-needed plans.

## Required References

- Read `references/rulebook.md` before scanning, planning, or patching.
- Read `references/examples.md` before writing remediations that need concrete
  implementation patterns or before explaining safe alternatives.
- Read `references/reporting-and-validation.md` before producing JSON/SARIF,
  running verification, listing limitations, or giving the final merge
  checklist.

If a finding or fix depends on a product, cloud, API, CLI, framework, package
manager, or version-specific behavior, verify the current official vendor
documentation before recommending or changing it. If the repository evidence or
official docs do not confirm the behavior, lower confidence and explain what is
missing.

## Workflow

1. Establish scope and mode.
   - Inspect the requested paths, current diff, relevant manifests, lockfiles,
     package manifests, workflow files, and existing scanner configuration.
   - Preserve unrelated user changes. Do not refactor unrelated code.
   - Do not print, persist, or copy secrets. Redact suspicious values.

2. Review by risk area.
   - Use stable finding IDs such as `TF-IAM-001`, `K8S-RUN-001`,
     `HELM-IMG-001`, `CI-PERM-001`, `PY-CMD-001`, `JAVA-DESER-001`,
     `JS-EVAL-001`, `TS-TYPE-001`, `RS-UNSAFE-001`, and `SH-EVAL-001`.
   - Classify each finding by severity, confidence, exploitability, and blast
     radius.
   - When runtime context matters, mark confidence `Medium` or `Low` and name
     the missing evidence.

3. Choose the safest remediation path.
   - Prefer minimal diffs that remove or reduce risk without changing intended
     behavior.
   - Replace unsafe hardcoded behavior with secure configurable defaults,
     explicit opt-ins, and documented exceptions.
   - Keep public APIs, auth flows, serialization formats, schemas, externally
     visible routes, and production availability unchanged unless the user
     explicitly approves a breaking change.
   - Do not add dependencies, scanners, new services, or compatibility shims
     unless the repository already uses them or the user approves them.

4. Apply changes only inside the approved boundary.
   - Implicit invocation does not by itself approve edits, broad scans, live
     checks, dependency installation, or external changes.
   - Safe to patch directly: obvious log redaction, missing
     `sensitive = true`, least-privilege workflow `permissions` when compatible,
     non-breaking Helm values, compatible pod/container security contexts,
     simple path validation, and Bash quoting where no word splitting is
     intended.
   - Plan first: public exposure, IAM/RBAC narrowing, NetworkPolicy default
     deny, TypeScript strictness, crypto, auth, CORS, deserialization, data
     retention, external network access, shell strict mode, or anything that
     may affect availability.
   - Never auto-fix without explicit approval: credential rotation, git history
     rewriting, deleting resources, disabling features, changing public APIs,
     changing database schemas, replacing serialization protocols, changing
     auth algorithms, or changing externally visible routes.

5. Verify and report.
   - Run the narrowest repository-native checks available. Missing tools are a
     limitation, not an automatic failure.
   - Avoid validation commands that mutate files, create cache/build
     directories, contact external registries, install missing tools, or use
     live credentials unless that behavior is explicitly approved and safe for
     the target scope.
   - Do not claim a finding is fixed unless validation passed or the limitation
     is explicitly documented.
   - Explain every change with risk, change, compatibility note, verification,
     remaining risk, and safe override where applicable.

## Safe Override Design

When risky behavior is required, prefer an explicit, narrow exception:

- explicit opt-in value such as `allowPrivileged`, `allowPublicExposure`,
  `allowUnsafeDeserialization`, `allowShellExecution`, or `allowInsecureTls`
- `securityException.owner`
- `securityException.reason`
- `securityException.scope`
- `securityException.reviewBy`
- visible warning in scan reports

Do not hide exceptions in broad global toggles. Apply exceptions only to the
specific resource, function, workflow, or chart value.

## Output Contract

For `scan`, return prioritized findings with:

- finding ID
- area
- file and line
- severity
- confidence
- exploitability
- blast radius
- risk
- root cause
- recommended fix
- feature impact
- safe override

For `patch`, return:

- summary of each change
- reason and risk addressed
- compatibility note
- verification commands and results
- remaining risks

Use Markdown by default. Provide JSON or SARIF-style output when requested.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Resources

- `references/rulebook.md`: review areas, severity guidance, auto-fix policy,
  safe override requirements, and no-auto-fix boundaries.
- `references/examples.md`: before/after remediation snippets for Terraform,
  Kubernetes, Helm, CI/CD, Python, Java, JavaScript, TypeScript, Rust, and Bash.
- `references/reporting-and-validation.md`: output schemas, validation command
  selection, limitations, staged rollout guidance, and final merge checklist.
