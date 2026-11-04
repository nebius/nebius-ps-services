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
- AC-009: PR events, CI events and completion recovery update Required CI but produce no merge candidates. Completion recovery resumes previously authorized operations only. MERGE_OPERATOR_IDS is the only merge-specific repository variable; no rollout toggles or metadata-only admission remain.
- AC-010: Standalone review-pr never merges. Merge-pr invokes review when needed; review-and-merge and complete publication requests authorize the entire sequence without routine reconfirmation. Preparation-only modes remain available.
- AC-011: Global completion discovery failures display a sanitized reason and original nonzero status and publish no targets. Target-specific receipt failures are isolated with blocked diagnostics and aggregate failure, while valid independent targets may proceed. Preserve API deadlines, receipt gates and effect permissions.

- AC-012: Protected merge has no cron. PR and trusted PR CI start/completion events target only affected current heads; ignore push and result-CI for PR aggregation. One hourly completion recovery at minute 17 and manual recovery also repair Required CI for all open default-base PRs.
- AC-013: Completion events inspect only the producer run. Operator-dispatched merge intents and validated early result-CI routing receipts can route recovery, including failed CI. Routing identity never proves CI success. Preserve latest-attempt exact-result evidence. Target-specific malformed receipts block that target while independent valid targets proceed; authentication, permissions and incomplete global listings stop all effects.
- AC-014: Register CI only for maintained repository projects and shared automation. Preserve the registered workflow identities and five result-dispatch inputs, trusted checkout ordering and success artifacts. Keep policy, listeners, path filters and publication-test inventory synchronized with the maintained projects, and check their parity in baseline CI.
- AC-015: Existing releases require a trusted immutable pre-publication manifest binding repository, tag object/commit, workflow run/attempt and every asset basename, size and SHA-256. Verify downloaded bytes. Missing evidence, transport/auth failures, partial or conflicting state block reconciliation; never treat errors as absence or overwrite published artifacts.
- AC-016: Image SemVer tags are tag-triggered only (X.Y.Z and X.Y.Z-gSHA); manual builds use sha-SHA and optional main-only latest, with no release_version input. Serialize the destination image across preflight, push, digest verification and manifest using noncanceling queue:max. Verify all immutable aliases against trusted prior evidence before reuse and every remote digest after publication.
- AC-017: Preserve service-specific release qualification and Helm catalog, metadata, package, anonymous-pull and digest checks. Share deterministic publication utilities without a privileged generic command workflow. github-workflows is the sole release/image YAML template owner; publisher skills delegate setup to it.
- AC-018: Reviewer routing adds configured nonauthor reviewers for same-repository nondraft PRs using trusted base policy, and preserves manually requested users and teams. It never executes PR code or removes reviewers.
- AC-019: Retire github-report and its release workflow while preserving strict Required CI during the trusted-policy transition. Keep the existing CI workflow identity temporarily as a read-only retirement validator; remove that validator in a follow-up PR after the policy no longer requires it on the default branch. Do not restore the retired product or weaken branch protection.

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
<!-- REQUIREMENT: REQ-003 status=active priority=P1 type=feature -->
### REQ-003: Restrict privileged PR events to reviewed automation

#### User Story

Repository administrators need explicit, auditable Actions event policies that
preserve trusted reviewer routing and CI status updates while preventing other
workflows from starting through `pull_request_target`.

#### Acceptance Criteria

- AC-001: Three active repository policies partition workflow paths. Reviewer
  routing permits only `pull_request_target`; protected merge permits
  `pull_request_target`, `workflow_run`, and `workflow_dispatch`; all other
  paths permit the frozen documented event inventory except `pull_request_target`.
- AC-002: Exceptions match exact workflow paths and use existing trusted code
  with least-privilege jobs. Policy permission never authorizes contributor code
  execution or automatic approval/merge.
- AC-003: Independently read back policy IDs, path conditions, enforcement and
  events. Preserve branch protection, token permissions, actor access and fork
  approval settings byte-for-byte in normalized configuration snapshots.
- AC-004: Document administrative ownership, exact policy configuration,
  maintenance, concurrent-change checks and fail-closed rollback. Distinguish
  local checks, configuration readback and subsequent live-event evidence.

#### Negative Criteria

- NC-001: Do not change workflow code, deploy new credentials, relax inherited
  restrictions, approve fork runs, or dispatch approval/merge to test policies.
- NC-002: Do not silently allow new event types or broaden path exceptions.
  Do not claim live acceptance from historical workflow runs.

#### Validation Method

Compare independently fetched policy definitions and unchanged adjacent settings
against the frozen intended configuration; validate the canonical spec pair and
the focused documentation diff.

#### Test Method

Run existing merge-admission and workflow-contract tests, inspect exact checkout
and job permissions, and exhaustively check the policy partition against the
frozen event inventory, current paths and hypothetical new paths.

#### Evaluation Method

Observe ordinary and fork PR/CI runs created after activation and their policy
insights when available. Keep live verification pending until representative
fresh runs exist; settings readback alone proves configuration only.

<!-- /REQUIREMENT: REQ-003 -->

<!-- maintain-project-specs:requirements:end -->
<!-- markdownlint-enable MD001 MD024 -->
