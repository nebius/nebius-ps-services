<!-- markdownlint-disable MD001 MD024 -->
<!-- maintain-project-specs:requirements:start schema=maintain-project-specs/requirements-v2 -->
# Project Requirements

<!-- REQUIREMENT: REQ-001 status=active priority=P0 type=documentation -->
### REQ-001: Describe the repository as Nebius Platform Services

#### User Story

Repository visitors need the root README to describe the project as Nebius
Platform Services in the same terms as the canonical repository description.

#### Acceptance Criteria

- AC-001: The root README opening description is `Nebius Platform Services:
  reusable AI/ML deployment building blocks for Nebius AI Cloud`.
- AC-002: The root README continues to orient visitors to the monorepo layout,
  common use cases, cross-project policy, and project-local documentation.
- AC-003: The root changelog records the repository-wide branding update in
  its `[Unreleased]` section.

#### Negative Criteria

- NC-001: The branding update must not rename repository paths, packages,
  commands, release artifacts, or project-local historical attribution.
- NC-002: The root README must not describe the repository as Nebius Public
  Services or Nebius Professional Services.

#### Validation Method

Inspect the root README and changelog, search the affected root documents for
stale branding, and review the focused Git diff.

#### Test Method

Run exact text searches for the existing repository-slug title and new
description, reject stale Public or Professional Services branding in the root
README, and lint the changed Markdown files.

#### Evaluation Method

Compare the rendered root README opening sentence with the accepted repository
description and confirm that its existing repository-slug title and unrelated
project content are unchanged.

<!-- /REQUIREMENT: REQ-001 -->

<!-- REQUIREMENT: REQ-002 status=active priority=P1 type=feature -->
### REQ-002: Shared protected merge automation

#### User Story

Ordinary PRs and all configured Dependabot updates, including Docker, use one local review, safe repair, fresh review and CI, then protected Actions merge workflow. Dependabot waits for an explicit local operation. Agentic SDLC is excluded. Keep the Python/Bash/GitHub Actions stack and local review agent; no new model runtime, cloud service, custom App or credential. Preserve dependency schedules, groups, labels, limits, target branch and ecosystems.

#### Acceptance Criteria

- AC-001: All ordinary merge effects use the merge-pr deterministic helper and an Actions approval tied to the exact admitted head.
- AC-002: Every new merge requires trusted operator dispatch and a positive actual GitHub COMMENT review ID bound to the exact head/base. Review the complete diff, breaking changes, dependency/security information and meaningful validation for ordinary, Python, GitHub Actions and Docker PRs alike. Safe source repairs require fresh review and CI; unsafe findings, stale/forged/malformed evidence, missing CI and human objections block effects.
- AC-003: Use least-privilege per-job GITHUB_TOKEN permissions; no agent, PR code, PAT fallback or bypass runs in the privileged workflow.
- AC-004: Same-feature reuse, default-to-feature preparation, canonical whole-repository commits and post-merge destination/CI verification are preserved.
- AC-005: Publishing consumes the actual merge result and cannot tag failed or unverified applicable CI.

- AC-006: PR-gated protection replaces the empty push-actor restriction; one approval, stale dismissal, conversations and strict required CI remain. Shared Actions identity is an explicit trust boundary.
- AC-007: Unsupported queue or workflow-file merges stop for a maintainer without credential substitution.
- AC-008: A trusted receipt precedes merge effects; completion and CI checkout accept only trusted operator-dispatched positive-review intents. Preserve immutable receipts, duplicate suppression, exact-result CI, Pages and publication guards.
- AC-009: PR events, CI events and schedules update Required CI but produce no merge candidates. Completion schedules recover previously authorized operations only. MERGE_OPERATOR_IDS is the only merge-specific repository variable; no rollout toggles or metadata-only admission remain.
- AC-010: Standalone review-pr never merges. Merge-pr invokes review when needed; review-and-merge and complete publication requests authorize the entire sequence without routine reconfirmation. Preparation-only modes remain available.

#### Negative Criteria

- NC-001: Do not change Agentic SDLC, dependency schedules/ecosystem scope, unrelated user work or existing credentials by revocation.
- NC-002: No direct default-branch pushes, force-pushes, protection bypass, stale approval, duplicate effects or agent runner in GitHub.

#### Validation Method

Run scoped Python, shell, workflow, skill and specification checks plus independent code/security review.

#### Test Method

Use disposable repositories and mocked GitHub APIs for identity, admission, CI, retries, queue/head races, canonical preparation and exact-result tags.

#### Evaluation Method

Separate static, installed, native and live evidence. Live acceptance uses a protected bootstrap followed by ordinary and real Dependabot PRs, including Docker when an eligible update exists. Fixtures do not prove live Dependabot results. Verify workflow-file success or the permission handoff separately.

<!-- /REQUIREMENT: REQ-002 -->
<!-- maintain-project-specs:requirements:end -->
<!-- markdownlint-enable MD001 MD024 -->
