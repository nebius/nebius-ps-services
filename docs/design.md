<!-- markdownlint-disable MD001 MD024 -->
<!-- maintain-project-specs:design:start schema=maintain-project-specs/design-v2 -->
# Project Design

<!-- FEATURE: FEAT-001 reqs=REQ-001 status=ready delivery=unassessed priority=P0 version=1 -->
### FEAT-001: Root repository identity documentation

#### Requirements Covered

- REQ-001: Describe the repository as Nebius Platform Services.

#### Context Evidence

The root `README.md` is the repository orientation page. Before this change,
its opening sentence called the project Nebius Public Services. The root
`CHANGELOG.md` explicitly owns repository-wide documentation changes.

#### Design Details

Keep the repository-slug H1 and place the accepted Nebius Platform Services
description verbatim in the opening paragraph. Preserve the existing
repository-layout, use-case, policy, automation, and license sections. Record
the branding change in the root `[Unreleased]` changelog.

#### Selected Option

Update only the human-facing root description and retain the existing
`nebius-ps-services` repository slug in the README title and wherever it
identifies a real path, package source, or release location.

#### Alternatives Considered

Renaming the README title, repository identifiers, or historical attribution
would expand the task beyond the requested description correction and risk
confusing the stable repository slug with the human-facing service name.

#### Implementation Boundaries

The implementation owns the root `README.md`, root `CHANGELOG.md`, and this
canonical root specification pair. Project-local documentation, source code,
configuration, package names, Git remotes, and release artifacts are excluded.

#### Test-First Success Criteria

- TDD-001: Before implementation, an exact search shows the root README
  contains the stale `Nebius Public Services` description.
- TDD-002: After implementation, exact searches find the accepted description
  and find no stale Public or Professional Services branding in the root
  README.

#### Validation Plan

Validate the canonical spec pair, run scoped Markdown lint and exact branding
searches, inspect `git diff --check`, and review the final changed-scope diff.

#### Test Plan

Use deterministic text assertions for the root README opening sentence and
stale-brand absence. Confirm the repository-slug title remains unchanged and
the root changelog has one concise Unreleased entry for the change.

#### Evaluation Plan

Review the root README as a visitor-facing document and compare its opening
sentence with the accepted repository identity and description.

#### Rollout And Rollback

Publish the documentation changes through the normal repository review flow.
If the branding decision is reversed, revert the focused root documentation
and matching specification records together.

#### Done Definition

The root README and changelog consistently present the accepted repository
identity, the canonical specs validate with complete traceability, and no
unrelated project behavior or identifiers change.

#### Implementation Evidence

No implementation evidence was recorded before schema migration.

#### Verification Evidence

No independent verification evidence was recorded before schema migration.

<!-- /FEATURE: FEAT-001 -->

<!-- FEATURE: FEAT-002 reqs=REQ-002 status=ready delivery=implemented priority=P1 version=2 -->
### FEAT-002: Shared protected merge automation

#### Requirements Covered

- REQ-002: Shared protected merge automation.

#### Context Evidence

Existing create-pr ends before merge; review-pr permits independent branch mutation; Helm/image helpers commit selected paths and require clean default entry. Existing Dependabot automation uses a personal-token fallback.

#### Design Details

Ordinary skills and deterministic Dependabot automation use the repository GITHUB_TOKEN for protected approval and merge. Local agent review and safe repair remain mandatory for ordinary skills. Agentic SDLC is excluded. Custom Apps and PAT fallback are not supported.

Keep the canonical whole-repository transaction and exact-head/base review evidence. Use a trusted-default metadata-only Required CI aggregate for all PRs, including human/fork review paths. Ordinary broker admission additionally requires a local review attestation; Dependabot uses verified secretless metadata. Separate per-job status, approval/merge, dispatch and Pages permissions. Upload an immutable admission receipt before effects. Use guarded asynchronous direct merge with no bypass; queues and rejected workflow-file merges stop for a maintainer. Shared github-actions identity is not exclusive to this broker.

Completion reacts to broker/CI completion and schedule, validates receipt provenance and authoritative merge state, dispatches applicable CI on trusted default with a frozen result SHA, and verifies exact checkout evidence. Correlate and reconcile ambiguous submissions. Retain legacy Pages publication and request a build explicitly; a successful build must contain the result. Publishers keep local exact-result tag ownership. Activation defaults disabled, with restricted canaries before full enablement. Preserve existing secrets and dependency update scope. Include the user's existing CODEOWNERS edit in the bootstrap PR.

#### Selected Option

Python, Bash, gh and deterministic GitHub Actions using GITHUB_TOKEN. Agentic review/repair remains local; no hosted model runtime or new cloud services.

#### Alternatives Considered

Custom App installation is unavailable. PAT fallback is rejected. Keeping the existing actor restriction requires a maintainer for every merge; the selected policy permits PR-gated Actions merges with explicit unsupported-case handoff.

#### Implementation Boundaries

Selected ordinary skills, direct commit-owner contracts, reusable workflow assets, root merge/Dependabot workflows, documentation and tests. Preserve SDLC and optional existing project helper copies.

#### Test-First Success Criteria

- TDD-001: Unsafe, stale or forged admission never approves or merges.
- TDD-002: Authorized safe repairs re-review the new head before merge.
- TDD-003: Publication tags the verified result even when default advances.

#### Validation Plan

Run focused deterministic regressions, lint/syntax, host skill structure, specification validation and changed-scope align.

#### Test Plan

Cover both admission lanes, missing checks, API errors, requested changes, provenance, concurrency, merge queue and exact-result publication.

#### Evaluation Plan

Update trigger and quality cases; report unavailable native evaluation rather than inferring runtime from metadata.

#### Rollout And Rollback

Bootstrap under current protection with one maintainer review. Keep effects disabled, establish Required CI, apply the reviewed PR-gated policy, then run ordinary, Dependabot and workflow-file canaries. Enable only after declared outcomes pass. Disable new effects and restore captured protection on rollback; continue verifying completed merges and never undo public releases automatically.

#### Done Definition

Code, instructions, templates and tests agree; independent review passes. Report local implementation separately from Actions policy setup, bootstrap approval and live merge proof.

#### Implementation Evidence

Implemented per-job Actions authority, CI-only aggregate, uploaded intent before effects, exact-result CI inputs/checkout receipts, deterministic completion and Pages journals, and disabled/canary/enabled controls. Ordinary skill owners, templates, documentation and publication primitives agree. Agentic SDLC and Dependabot scheduling remain unchanged. Bootstrap and live activation are pending.

#### Verification Evidence

Passed 172 focused tests: 58 merge/recovery, 10 disposable publication, 60 canonical transaction, 5 commit contract and 39 release tests. Also passed workflow actionlint, Python lint/format, shell syntax/ShellCheck, portable/Codex/Claude structure and stateful profiles, and eight-skill npx copy/repeat/isolation checks. Code/security review findings for fork association, rerun invalidation, result concurrency and recovery provenance were fixed with regression coverage. Native trigger/quality runners remain authentication-unavailable; no runtime or live merge claim. Maintainer bootstrap, protection activation and controlled live canaries remain pending.

<!-- /FEATURE: FEAT-002 -->
<!-- maintain-project-specs:design:end -->
<!-- markdownlint-enable MD001 MD024 -->
