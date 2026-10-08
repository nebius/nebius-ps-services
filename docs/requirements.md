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

Ordinary skills and deterministic Dependabot automation use the repository GITHUB_TOKEN for protected approval and merge. Local agent review and safe repair remain mandatory for ordinary skills. Agentic SDLC is excluded. Custom Apps and PAT fallback are not supported. Agents never execute on GitHub runners. Dependabot keeps existing ecosystem, update-type and file limits; Docker is not eligible. Preserve dependency update schedules and groups.

#### Acceptance Criteria

- AC-001: All ordinary merge effects use the merge-pr deterministic helper and an Actions approval tied to the exact admitted head.
- AC-002: Local review attestations and Dependabot provenance are distinct admission policies; stale evidence, missing expected CI and human objections block merging.
- AC-003: Use least-privilege per-job GITHUB_TOKEN permissions; no agent, PR code, PAT fallback or bypass runs in the privileged workflow.
- AC-004: Same-feature reuse, default-to-feature preparation, canonical whole-repository commits and post-merge destination/CI verification are preserved.
- AC-005: Publishing consumes the actual merge result and cannot tag failed or unverified applicable CI.

- AC-006: PR-gated protection replaces the empty push-actor restriction; one approval, stale dismissal, conversations and strict required CI remain. Shared Actions identity is an explicit trust boundary.
- AC-007: Unsupported queue or workflow-file merges stop for a maintainer without credential substitution.
- AC-008: A trusted receipt precedes merge effects; interrupted completion reconciles exact-result CI and a Pages build containing the result.

#### Negative Criteria

- NC-001: Do not change Agentic SDLC, dependency schedules/ecosystem scope, unrelated user work or existing credentials by revocation.
- NC-002: No direct default-branch pushes, force-pushes, protection bypass, stale approval, duplicate effects or agent runner in GitHub.

#### Validation Method

Run scoped Python, shell, workflow, skill and specification checks plus independent code/security review.

#### Test Method

Use disposable repositories and mocked GitHub APIs for identity, admission, CI, retries, queue/head races, canonical preparation and exact-result tags.

#### Evaluation Method

Separate static, installed, native and live evidence. Live rollout uses a bootstrap maintainer review followed by ordinary and Dependabot PR trials.

<!-- /REQUIREMENT: REQ-002 -->
<!-- maintain-project-specs:requirements:end -->
<!-- markdownlint-enable MD001 MD024 -->
