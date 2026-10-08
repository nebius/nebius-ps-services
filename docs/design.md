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

<!-- FEATURE: FEAT-002 reqs=REQ-002 status=ready delivery=implemented priority=P1 version=3 -->
### FEAT-002: Shared protected merge automation

#### Requirements Covered

- REQ-002: Shared protected merge automation.

#### Context Evidence

The deployed broker has separate ordinary-review and Dependabot metadata admission plus rollout controls. The accepted redesign replaces both paths with universal local review while preserving the existing completion and publication safeguards.

#### Design Details

All ordinary PRs and configured Dependabot updates, including Docker, use local review-pr, safe repairs through create-pr, fresh exact-head/base review and CI, then merge-pr. Standalone review records evidence without starting a merge; a complete review-and-merge request authorizes the sequence. Route Python, Actions and Docker changes to the matching specialists and stop for unsafe findings, unavailable meaningful validation or human objections. Every changed head or base invalidates earlier review.

Only an explicit trusted numeric operator workflow_dispatch can initiate a merge. Independently fetch and validate the positive GitHub COMMENT review ID, author, repository/PR/head/base and validation before intent and again before effects. Bot identity, metadata, labels and CI cannot replace review. Remove metadata admission and rollout toggles without compatibility paths; MERGE_OPERATOR_IDS is the only merge-specific variable. Preserve same-feature reuse, canonical repository-wide commits and preparation-only publication modes.

PR, CI and scheduled events maintain the CI-only Required CI aggregate for ordinary and fork PRs and return no merge candidates. Upload an immutable admission receipt before approval. Keep per-job least privilege, protected asynchronous merge with bypass_rules false, exact-head approval, repeated CI/objection checks and maintainer handoff for queues or workflow-file permission denial. The shared Actions identity is a trust boundary.

Completion schedules recover only already-authorized operator-dispatched positive-review intents. Preserve duplicate suppression, uncertain-effect journals, authoritative result ancestry, explicit result CI and Pages dispatch. Each CI execution job loads its checkout validator from the trusted workflow revision and validates receipt and authoritative result before setup/cache restoration; fetch credentials remain process-local. Release publishers tag only the verified actual merge result. Agentic SDLC, existing credentials and Dependabot scheduling/configuration stay unchanged.

#### Selected Option

Python, Bash, gh and deterministic GitHub Actions using GITHUB_TOKEN. Agentic review/repair remains local; no hosted model runtime or new cloud services.

#### Alternatives Considered

Unattended metadata-only merging violates universal review. Manual merges for every PR defeat the requested completion flow. Retain the existing local agent and Python/Bash/Actions stack; no scheduled local agent, new model runtime, custom App or credential fallback.

#### Implementation Boundaries

Ordinary review/merge/create and publication owners, reusable workflows, broker/completion/CI checkout helpers, root merge workflows, paired specifications, documentation and evaluations. Delete the separate Dependabot producer workflow and template. Preserve Agentic SDLC and all configured Dependabot updates.

#### Test-First Success Criteria

- TDD-001: Ordinary, Python, Actions and Docker PRs use identical positive-review dispatch admission. Missing, zero, malformed, forged or stale evidence blocks all effects.
- TDD-002: Safe Dependabot source repairs invalidate old review; head/base changes, objections and missing/failed CI stop the operation.
- TDD-003: Standalone review and automatic events cannot initiate merging; ordinary/fork Required CI still reports.
- TDD-004: Interrupted completion, exact checkout, duplicate suppression, Pages and verified-result tags retain their safeguards.

#### Validation Plan

Run focused deterministic regressions, lint/syntax, host skill structure, specification validation and changed-scope align.

#### Test Plan

Run merge, completion, publication and template suites, covering dispatch identity, reviewed dependency repairs, missing checks, objections, artifact provenance, recovery, checkout, queue/workflow permission handoff and exact-result publication.

#### Evaluation Plan

Update trigger and quality cases; report unavailable native evaluation rather than inferring runtime from metadata.

#### Rollout And Rollback

Capture current repository settings and reconcile old broker effects before retiring the producer. Verify old runs are quiescent. Deploy through the existing feature PR and protection, using maintainer bootstrap if workflow permission requires it. Configure the numeric operator list, read-only default token, Actions approvals and strict Required CI bound to the verified built-in publisher. Remove only the agreed empty push restriction; retain all other protection and repository merge methods. No Environment or new secret. Run ordinary and real Dependabot acceptance PRs without a permanent trial mode. Rollback stops new broker runs, reconciles in-flight effects and restores captured settings when necessary; retain completion recovery and never undo merges/releases automatically.

#### Done Definition

Code, instructions, templates and tests agree; independent review passes. Report local implementation separately from Actions policy setup, bootstrap approval and live merge proof.

#### Implementation Evidence

Implemented universal positive COMMENT review admission and explicit trusted operator dispatch. Deleted dependency metadata admission, its producer/template, policy key and obsolete check exclusion. Removed rollout controls with no replacement or compatibility path. Completion selection and CI checkout require operator-dispatched positive-review intents; immutable receipts, repeated CI/review gates, exact-result checkout, journals and publication ownership remain. Dependabot configuration is unchanged. Updated instructions, reusable assets, evaluations, READMEs and Unreleased changelogs; installed the four changed skill payloads from their verified source owners.

#### Verification Evidence

Historical foundation evidence: 179 focused tests and initial skill/workflow checks passed; the maintainer bootstrap PR passed 41 checks with 13 expected skips, and cxcli passed 8,278 tests. The bootstrap merged on 2026-10-08 and legacy Pages built that result. A later alignment passed 77 merge/completion/publication/template tests and 21 service checks with source/installed parity. These results do not verify this redesign or token-driven completion. Current source, installed, CI and live evidence must be recorded separately after implementation; delivery remains unverified until acceptance is observed.

Current redesign evidence: 122 focused merge, completion, publication, release and template tests pass, including reviewed Python/Actions/Docker changes and safe source repairs, automatic-event non-initiation, malformed/stale review rejection and completion checkout provenance. Workflow actionlint, Python lint/format, publication shell checks, changed Markdown and paired spec validation pass. Independent code/security review found no blocking source issue. Eight skills pass portable/Codex/Claude structure checks and actual npx copy/repeat/isolation tests; all eight source/installed payloads and executable modes match. Native trigger and comparative quality probes are authentication-unavailable, not passing runtime evidence.

Live preflight confirmed the authorized numeric account and administration access, the built-in publisher App identity, read-only default workflow token and enabled Actions approvals. Old merge workflows were quiescent and the merge/effect receipt inventory was empty. Existing protection was captured and the narrow target configuration reviewed. Deployment, settings application/readback and live ordinary/real Dependabot acceptance remain pending; no eligible dependency PR was open at preflight. Source tests and bot fixtures do not establish live protected merge, exact-result CI or Pages completion.

<!-- /FEATURE: FEAT-002 -->
<!-- maintain-project-specs:design:end -->
<!-- markdownlint-enable MD001 MD024 -->
