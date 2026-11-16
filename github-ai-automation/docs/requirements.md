<!-- markdownlint-disable MD001 MD024 -->
<!-- markdownlint-disable-next-line MD013 -->
<!-- maintain-project-specs:requirements:start schema=maintain-project-specs/requirements-v2 -->
# Project Requirements

<!-- REQUIREMENT: REQ-001 status=active priority=P0 type=feature -->
### REQ-001: Trusted hosted execution

#### User Story

Run the PR lifecycle on GitHub-hosted runners using pinned trusted controller code, Codex and provider-neutral skills. No App, PAT fallback or self-hosted runner.

#### Acceptance Criteria

- AC-001: Run the PR lifecycle on GitHub-hosted runners using pinned trusted controller code, Codex and provider-neutral skills. No App, PAT fallback or self-hosted runner.

#### Negative Criteria

- NC-001: No unauthorized effect or unsupported success claim.

#### Validation Method

Focused contracts and integration tests, then explicitly configured end-to-end qualification.

#### Test Method

Positive, rejection, concurrency and recovery cases for this boundary.

#### Evaluation Method

Retained source checks and independently observed runtime evidence.

<!-- /REQUIREMENT: REQ-001 -->

<!-- REQUIREMENT: REQ-002 status=active priority=P0 type=feature -->
### REQ-002: Coordinated PR effects

#### User Story

Laptop and Actions requests share one PR operation and merge authority. Exact head/base review, CI, live evidence and GitHub protection must authorize every merge.

#### Acceptance Criteria

- AC-002: Laptop and Actions requests share one PR operation and merge authority. Exact head/base review, CI, live evidence and GitHub protection must authorize every merge.

#### Negative Criteria

- NC-002: No unauthorized effect or unsupported success claim.

#### Validation Method

Focused contracts and integration tests, then explicitly configured end-to-end qualification.

#### Test Method

Positive, rejection, concurrency and recovery cases for this boundary.

#### Evaluation Method

Retained source checks and independently observed runtime evidence.

<!-- /REQUIREMENT: REQ-002 -->

<!-- REQUIREMENT: REQ-003 status=active priority=P0 type=feature -->
### REQ-003: Specific live tests

#### User Story

Generate a minimal meaningful live scenario for each PR, freeze assertions, verify product behavior independently and clean owned resources. Failed or unavailable live tests leave the PR open.

#### Acceptance Criteria

- AC-003: Generate a minimal meaningful live scenario for each PR, freeze assertions, verify product behavior independently and clean owned resources. Failed or unavailable live tests leave the PR open.

#### Negative Criteria

- NC-003: No unauthorized effect or unsupported success claim.

#### Validation Method

Focused contracts and integration tests, then explicitly configured end-to-end qualification.

#### Test Method

Positive, rejection, concurrency and recovery cases for this boundary.

#### Evaluation Method

Retained source checks and independently observed runtime evidence.

<!-- /REQUIREMENT: REQ-003 -->

<!-- REQUIREMENT: REQ-004 status=active priority=P0 type=feature -->
### REQ-004: Lifetime PR waiver

#### User Story

An authorized creator can waive eligible missing or inapplicable live validation for one immutable PR identity, across updates and reopenings, until explicit revocation. Failed/intervened evidence and known defects remain blocking.

#### Acceptance Criteria

- AC-004: An authorized creator can waive eligible missing or inapplicable live validation for one immutable PR identity, across updates and reopenings, until explicit revocation. Failed/intervened evidence and known defects remain blocking.

#### Negative Criteria

- NC-004: No unauthorized effect or unsupported success claim.

#### Validation Method

Focused contracts and integration tests, then explicitly configured end-to-end qualification.

#### Test Method

Positive, rejection, concurrency and recovery cases for this boundary.

#### Evaluation Method

Retained source checks and independently observed runtime evidence.

<!-- /REQUIREMENT: REQ-004 -->

<!-- REQUIREMENT: REQ-005 status=active priority=P0 type=feature -->
### REQ-005: Automatic package releases

#### User Story

Package-affecting merges trigger project releases using per-change semver fragments. Documentation and test-only changes do not trigger releases.

#### Acceptance Criteria

- AC-005: Package-affecting merges trigger project releases using per-change semver fragments. Documentation and test-only changes do not trigger releases.

#### Negative Criteria

- NC-005: No unauthorized effect or unsupported success claim.

#### Validation Method

Focused contracts and integration tests, then explicitly configured end-to-end qualification.

#### Test Method

Positive, rejection, concurrency and recovery cases for this boundary.

#### Evaluation Method

Retained source checks and independently observed runtime evidence.

<!-- /REQUIREMENT: REQ-005 -->

<!-- REQUIREMENT: REQ-006 status=active priority=P0 type=feature -->
### REQ-006: Credential and input isolation

#### User Story

Keep secrets and private target identifiers out of public artifacts, logs, prompts and repository files. Use the github-ai-automation environment on the trusted default branch.

#### Acceptance Criteria

- AC-006: Keep secrets and private target identifiers out of public artifacts, logs, prompts and repository files. Use the github-ai-automation environment on the trusted default branch.

#### Negative Criteria

- NC-006: No unauthorized effect or unsupported success claim.

#### Validation Method

Focused contracts and integration tests, then explicitly configured end-to-end qualification.

#### Test Method

Positive, rejection, concurrency and recovery cases for this boundary.

#### Evaluation Method

Retained source checks and independently observed runtime evidence.

<!-- /REQUIREMENT: REQ-006 -->

<!-- REQUIREMENT: REQ-007 status=active priority=P0 type=feature -->
### REQ-007: Durable evidence and recovery

#### User Story

Interrupted operations recover without duplicate merges, resources, tags or publications. Preserve original budgets and independent proof boundaries.

#### Acceptance Criteria

- AC-007: Interrupted operations recover without duplicate merges, resources, tags or publications. Preserve original budgets and independent proof boundaries.

#### Negative Criteria

- NC-007: No unauthorized effect or unsupported success claim.

#### Validation Method

Focused contracts and integration tests, then explicitly configured end-to-end qualification.

#### Test Method

Positive, rejection, concurrency and recovery cases for this boundary.

#### Evaluation Method

Retained source checks and independently observed runtime evidence.

<!-- /REQUIREMENT: REQ-007 -->

<!-- REQUIREMENT: REQ-008 status=active priority=P0 type=feature -->
### REQ-008: Qualification and rollout

#### User Story

Ship disabled, qualify real provider and lifecycle behavior, then enable report, repair and merge stages. Control PR automation with repository Actions variable `AI_AUTOMATION_PR_REVIEW` (`disabled`, `report`, `repair`, `merge`), release publishing with `AI_AUTOMATION_RELEASE_PUBLISH` (`false`, `true`), and fork merge eligibility with `AI_AUTOMATION_MERGE_FORK_CONTRIBUTION` (`false`, `true`). Defaults are `disabled`, `false`, and `false`. Update skills and canonical documentation with measured evidence.

#### Acceptance Criteria

- AC-008: Ship disabled, qualify real provider and lifecycle behavior, then enable report, repair and merge stages. Control PR automation with repository Actions variable `AI_AUTOMATION_PR_REVIEW` (`disabled`, `report`, `repair`, `merge`), release publishing with `AI_AUTOMATION_RELEASE_PUBLISH` (`false`, `true`), and fork merge eligibility with `AI_AUTOMATION_MERGE_FORK_CONTRIBUTION` (`false`, `true`). Defaults are `disabled`, `false`, and `false`. Update skills and canonical documentation with measured evidence.

- AC-009: Source may be merged before activation. Keep ordinary operator-driven
  protected merge and publish-release available while the controller is dormant.
  Store enrollment, effect workflows and reusable release builders as prepared
  installation templates until a separately authorized activation.

#### Negative Criteria

- NC-008: No unauthorized effect or unsupported success claim. No retired control-name aliases or enabling values committed as workflow defaults. Keep one repository-level value for each control, without environment shadows.

- NC-009: Shipping disabled source must not install mandatory controller authority
  or replace the working ordinary release publisher.

#### Validation Method

Verify missing and explicit disabled controls prevent PR/release effects, and each supported PR mode preserves its authority. Check controller, workflows and enrolled templates together, then explicitly configured end-to-end qualification.

#### Test Method

Positive, rejection, concurrency and recovery cases for this boundary.

#### Evaluation Method

Retained source checks and independently observed runtime evidence.

<!-- /REQUIREMENT: REQ-008 -->

<!-- REQUIREMENT: REQ-009 status=satisfied priority=P2 type=feature -->
### REQ-009: Portable workflow guide

#### User Story

Readers can browse the PR and release workflows in one portable HTML file
under docs, with readable diagrams and explicit operating limits.

#### Acceptance Criteria

- AC-009: Open directly from disk, offline and after relocation, without
  external assets. Provide navigation, diagram zoom, text equivalents,
  narrow-screen reflow and complete printing, including without JavaScript.
- AC-010: Distinguish review-only, repair and merge authority; lifetime waivers;
  failed gates; release outcomes; and disabled, unqualified rollout status.

#### Negative Criteria

- NC-009: No automatic network requests, runtime behavior changes or claims of
  deployed qualification from documentation checks.

#### Validation Method

Inspect the delivered HTML with browser networking disabled and direct file URLs.

#### Test Method

Exercise navigation, zoom, keyboard controls, relocation, no-script reading,
320-pixel reflow, enlarged text, and actual print output.

#### Evaluation Method

Compare diagram branches with the controller, policy and publication owners;
record actual browser coverage and remaining gaps.

<!-- /REQUIREMENT: REQ-009 -->
<!-- REQUIREMENT: REQ-010 status=satisfied priority=P1 type=constraint -->
### REQ-010: One maintainer release entry point

#### User Story

Maintainers use publish-release as the sole supported GitHub Release entry point;
project READMEs focus on product use and do not offer competing release procedures.

#### Acceptance Criteria

- AC-001: Remove project-local GitHub Release scripts from vpngw, nebius-cxcli and
  nebius-acc-management without replacement wrappers or compatibility aliases.
- AC-002: Remove maintainer GitHub Release procedures from project READMEs while
  preserving release consumption, version information and local build guidance.
- AC-003: Skill requests and automatic post-merge releases retain the same shared
  Actions publisher, reservations and evidence gates. Private helpers remain internal.
- AC-004: Remove obsolete callers and keep meaningful release-dispatch regression
  coverage in the skill's test lane. Setup must not recreate local publishers in
  this enrolled repository.

#### Negative Criteria

- NC-001: Do not change image/Helm publishing, add account-management enrollment,
  activate automation, publish a release, or alter historical changelog records.

#### Validation Method

Scoped source inventory, documentation/CI alignment and shared release regressions.

#### Test Method

Exercise direct shared-client dispatch and invalid requests without real GitHub writes.

#### Evaluation Method

Review the changed scope and distinguish local cleanup evidence from live publication.

<!-- /REQUIREMENT: REQ-010 -->

<!-- REQUIREMENT: REQ-011 status=active priority=P0 type=feature -->
### REQ-011: Explicit PR requests and terminal lifecycle

#### User Story

Automatically enroll only newly opened PRs in the enabled admission epoch. New drafts wait for readiness. Existing or terminal PRs require a fresh authenticated manual request. Recovery resumes registered active requests only, preserves review intent, three-hour deadline and two-repair allowance, and never enrolls the backlog.

#### Acceptance Criteria

- AC-001: Automatically enroll only newly opened PRs in the enabled admission epoch. New drafts wait for readiness. Existing or terminal PRs require a fresh authenticated manual request. Recovery resumes registered active requests only, preserves review intent, three-hour deadline and two-repair allowance, and never enrolls the backlog.

#### Negative Criteria

- NC-001: No unrequested processing, authority escalation or unsupported success claim.

#### Validation Method

Focused source and workflow contracts; isolated acceptance fixtures; later hosted qualification.

#### Test Method

Exercise positive, rejection, concurrency, duplicate delivery and recovery cases.

#### Evaluation Method

Retain exact-identity local evidence separately from real provider and hosted trials.

<!-- /REQUIREMENT: REQ-011 -->

<!-- REQUIREMENT: REQ-012 status=active priority=P0 type=feature -->
### REQ-012: Purposeful contribution gate

#### User Story

Assess concrete usefulness before repairs, candidate CI or live tests. Useful small code, documentation, test, accessibility and maintenance changes qualify. Judge contribution evidence rather than author motives, diff size or missing rationale.

#### Acceptance Criteria

- AC-001: Assess concrete usefulness before repairs, candidate CI or live tests. Useful small code, documentation, test, accessibility and maintenance changes qualify. Judge contribution evidence rather than author motives, diff size or missing rationale.

#### Negative Criteria

- NC-001: No unrequested processing, authority escalation or unsupported success claim.

#### Validation Method

Focused source and workflow contracts; isolated acceptance fixtures; later hosted qualification.

#### Test Method

Exercise positive, rejection, concurrency, duplicate delivery and recovery cases.

#### Evaluation Method

Retain exact-identity local evidence separately from real provider and hosted trials.

<!-- /REQUIREMENT: REQ-012 -->

<!-- REQUIREMENT: REQ-013 status=active priority=P0 type=feature -->
### REQ-013: Verified ordered review and repair

#### User Story

Require ordered value, safety, bounded conflict repair, code review, alignment, exact-candidate CI, live qualification and cleanup before protected merging. Do not equate bundled skills or a model claim with validation.

#### Acceptance Criteria

- AC-001: Require ordered value, safety, bounded conflict repair, code review, alignment, exact-candidate CI, live qualification and cleanup before protected merging. Do not equate bundled skills or a model claim with validation.

#### Negative Criteria

- NC-001: No unrequested processing, authority escalation or unsupported success claim.

#### Validation Method

Focused source and workflow contracts; isolated acceptance fixtures; later hosted qualification.

#### Test Method

Exercise positive, rejection, concurrency, duplicate delivery and recovery cases.

#### Evaluation Method

Retain exact-identity local evidence separately from real provider and hosted trials.

<!-- /REQUIREMENT: REQ-013 -->

<!-- REQUIREMENT: REQ-014 status=active priority=P0 type=feature -->
### REQ-014: Controlled fork merge eligibility

#### User Story

Use the repository variable AI_AUTOMATION_MERGE_FORK_CONTRIBUTION, default false. True allows already qualified fork PRs to merge through the protected broker; it grants no fork-write authority and bypasses no gate.

#### Acceptance Criteria

- AC-001: Use the repository variable AI_AUTOMATION_MERGE_FORK_CONTRIBUTION, default false. True allows already qualified fork PRs to merge through the protected broker; it grants no fork-write authority and bypasses no gate.

#### Negative Criteria

- NC-001: No unrequested processing, authority escalation or unsupported success claim.

#### Validation Method

Focused source and workflow contracts; isolated acceptance fixtures; later hosted qualification.

#### Test Method

Exercise positive, rejection, concurrency, duplicate delivery and recovery cases.

#### Evaluation Method

Retain exact-identity local evidence separately from real provider and hosted trials.

<!-- /REQUIREMENT: REQ-014 -->

<!-- REQUIREMENT: REQ-015 status=active priority=P0 type=feature -->
### REQ-015: Clear PR outcomes and fork origin

#### User Story

Provide idempotent actionable comments, source:fork labels and persistent origin notices for admitted PRs. Select explain/open, explain/close or protected merge from completed evidence and request authority.

#### Acceptance Criteria

- AC-001: Provide idempotent actionable comments, source:fork labels and persistent origin notices for admitted PRs. Select explain/open, explain/close or protected merge from completed evidence and request authority.

#### Negative Criteria

- NC-001: No unrequested processing, authority escalation or unsupported success claim.

#### Validation Method

Focused source and workflow contracts; isolated acceptance fixtures; later hosted qualification.

#### Test Method

Exercise positive, rejection, concurrency, duplicate delivery and recovery cases.

#### Evaluation Method

Retain exact-identity local evidence separately from real provider and hosted trials.

<!-- /REQUIREMENT: REQ-015 -->

<!-- REQUIREMENT: REQ-016 status=active priority=P0 type=feature -->
### REQ-016: Hosted managed recovery and two evaluation slots

#### User Story

Run on GitHub-hosted runners with two concurrent evaluation workers and durable
managed recovery. GitHub scheduling delays and outages have no promised recovery SLA.

#### Acceptance Criteria

- AC-001: Enforce two deployment-wide slots, counting starting, running, draining and unknown executions.
- AC-002: Deduplicate dispatches and reject stale generations; confirm worker termination before slot reuse.
- AC-003: Separate coordinator, evaluator, live executor and protected effect credentials; serialize cloud trials.

#### Negative Criteria

- NC-001: No permanent Nebius runner fleet or regional-outage availability claim.
- NC-002: Lease expiry alone must never prove a worker stopped.

#### Validation Method

Source contracts, focused tests and independently observed hosted qualification.

#### Test Method

Three concurrent requests, duplicate dispatch, supervisor loss, uncertain cancellation,
CI waits, stale results and recovery while admission is disabled.

#### Evaluation Method

Record local, hosted, cloud and protected-effect evidence separately.

<!-- /REQUIREMENT: REQ-016 -->

<!-- REQUIREMENT: REQ-017 status=active priority=P0 type=feature -->
### REQ-017: Qualified public images and configurable Codex updates

#### User Story

Publish repository-linked public GHCR images for anonymous digest pulls. Resolve
AI_AUTOMATION_CODEX_VERSION=latest by default, or an exact operator-selected version.

#### Acceptance Criteria

- AC-001: Hourly, manual and trusted source triggers build only when resolved version or build inputs change.
- AC-002: Qualify exact image bytes using real inference and sandbox tests before publication and manual promotion.
- AC-003: Keep AI_AUTOMATION_IMAGE_PUBLISH=false by default; automatic builds/inference stay off until enabled, while explicit manual qualification is available. Reject unqualified runtime digests.

#### Negative Criteria

- NC-001: No version-bump PRs, per-PR latest installs or automatic digest promotion.
- NC-002: No secrets, private identifiers or runtime job data in images or public artifacts.

#### Validation Method

Source contracts, focused tests and independently observed hosted qualification.

#### Test Method

Latest changes, exact pins, duplicate checks, failed qualification, anonymous pull,
forged qualification and rollback to the prior qualified digest.

#### Evaluation Method

Record local, hosted, cloud and protected-effect evidence separately.

<!-- /REQUIREMENT: REQ-017 -->

<!-- REQUIREMENT: REQ-018 status=active priority=P0 type=feature -->
### REQ-018: In-repository qualification and scoped real canaries

#### User Story

Keep tests with this monorepo, rehearse without GitHub writes, and qualify real
protected effects using useful PRs and legitimate releases rather than a separate repo.

#### Acceptance Criteria

- AC-001: Use a shared evaluator with read-only GitHub transport, isolated state and distinct rehearsal artifacts.
- AC-002: Trusted rollout state defaults to qualification and admits only exact expiring operation permits.
- AC-003: Recheck generation, candidate and permitted effects at intake, callbacks, recovery and each write.
- AC-004: Map the complete candidate diff to all affected projects and shared dependencies.

#### Negative Criteria

- NC-001: Report mode is not a no-write rehearsal; never consume shadow artifacts as production admissions.
- NC-002: No global disabled-mode bypass, non-default-base exception or unrelated PR effects.
- NC-003: Do not fabricate a contribution, release or fork-origin qualification.

#### Validation Method

Source contracts, focused tests and independently observed hosted qualification.

#### Test Method

No-write shadow, non-canary rejection, stale permit callbacks, candidate drift,
monorepo dependencies, exact real merge and release postconditions.

#### Evaluation Method

Record local, hosted, cloud and protected-effect evidence separately.

<!-- /REQUIREMENT: REQ-018 -->

<!-- REQUIREMENT: REQ-019 status=active priority=P0 type=feature -->
### REQ-019: Scoped native Nebius live qualification

#### User Story

Qualify frozen cxcli and vpngw candidates, including vpngw HA, using disposable
resources in the dedicated live-test project and independent cleanup evidence.

#### Acceptance Criteria

- AC-001: Use reviewed test-live-on-nebius adapters, exact trial identity and trusted ownership journals.
- AC-002: Provide explicit preconfigured credential modes for both products with read-only prerequisite verification.
- AC-003: Enforce provider expiry, denied refresh/delegation and effective resource ceilings before deployment.
- AC-004: Serialize trials; retain cleanup authority until independent absence verification.

#### Negative Criteria

- NC-001: No candidate IAM bootstrap, impersonation, credential rotation or privilege fallback.
- NC-002: Fixture setup, recovery and observation cannot masquerade as product-owned behavior.
- NC-003: Never publish supplied storage or test target identifiers.

#### Validation Method

Source contracts, focused tests and independently observed hosted qualification.

#### Test Method

Native apply/destroy, ordinary VPN Static/BGP, HA fencing/failover/replacement,
negative controls, interrupted cleanup, forbidden IAM and residual resource limits.

#### Evaluation Method

Record local, hosted, cloud and protected-effect evidence separately.

<!-- /REQUIREMENT: REQ-019 -->

<!-- REQUIREMENT: REQ-020 status=active priority=P0 type=feature -->
### REQ-020: Explicit historical release baseline adoption

#### User Story

Adopt the verified existing cxcli v0.1.9 release as an accountable baseline
without changing its tag/assets or inventing historical publication evidence.

#### Acceptance Criteria

- AC-001: Inspect and hash a descriptor binding release, tag/source, original run/artifact and verified assets.
- AC-002: Adoption revalidates that exact descriptor and initializes retained state with explicit maintainer_adoption provenance.
- AC-003: Use one canonical verifier for retained adopted and newly published baselines.

#### Negative Criteria

- NC-001: No tag moves, asset replacement, new release or fabricated original hash attestation.
- NC-002: Reject drift, concurrent publication, newer baselines and uncertain writes.

#### Validation Method

Source contracts, focused tests and independently observed hosted qualification.

#### Test Method

Idempotent adoption, interruption, drift, changed asset IDs, invalid wheel identity,
concurrent publisher and first subsequent legitimate release.

#### Evaluation Method

Record local, hosted, cloud and protected-effect evidence separately.

<!-- /REQUIREMENT: REQ-020 -->

<!-- REQUIREMENT: REQ-021 status=active priority=P0 type=feature -->
### REQ-021: Unified PR lifecycle and verified issue completion

#### User Story

Maintainers need one evidence-backed PR lifecycle across reusable skills and
hosted automation, with meaningful status and independently justified issue closure.

#### Acceptance Criteria

- AC-001: Plain review assesses, safely repairs and labels; full lifecycle effects
  require explicit user intent or enrolled policy. Labels never grant authority.
- AC-002: Use under-review, testing, being-fixed, manual-merge, blocked,
  needs-information, closed and merged as reconciled lifecycle statuses.
- AC-003: Preserve early usefulness assessment and independent closure proof.
  Route live failures through causal diagnosis, owner repair and fresh trials;
  known defects, intervention and pending cleanup cannot be waived.
- AC-004: Adopt large conflicts in one replacement PR with complete contribution
  coverage, individual feature commits and inherited origin, authority and budgets.
  Preserve the original until merged-result coverage is independently verified.
- AC-005: Share the original three-hour and two-attempt request limits; accepted
  repair/adoption publications count once. Terminal work needs manual continuation.
- AC-006: Assess each linked issue independently. Prefer native closure and permit
  explicit verified closure under lifecycle authority when still open. Preserve
  later human reopening, scope changes and objections; verify every effect.
- AC-007: Reconcile terminal labels, comments and issues through narrow completion
  records without restarting implementation, admitting backlog work or remerging.

#### Negative Criteria

- NC-001: No merges from labels, contributor prose, failed evidence or partial
  adoption; no authority or budget reset through replacement PRs.
- NC-002: No credential expansion, protection bypass, implicit fork writes,
  report-only effects, hidden uncertain writes or false live qualification.

#### Validation Method

Focused controller and skill contracts, independent review, lint, canonical
validation, changed-scope alignment and disposable installation parity.

#### Test Method

Exercise success, failed live remediation, labels, adoption coverage, origin
restrictions, deadlines, retries, issue drift and later human reopening.

#### Evaluation Method

Separate local deterministic evidence from installed and hosted qualification.

<!-- /REQUIREMENT: REQ-021 -->

<!-- maintain-project-specs:requirements:end -->
<!-- markdownlint-enable MD001 MD024 -->
