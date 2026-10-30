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

<!-- FEATURE: FEAT-002 reqs=REQ-002 status=ready delivery=implemented priority=P1 version=8 -->
### FEAT-002: Shared protected merge automation

#### Requirements Covered

- REQ-002: Shared protected merge automation.

#### Context Evidence

The earlier broker had separate ordinary-review and Dependabot metadata admission plus rollout controls. The accepted redesign replaces both paths with universal local review while preserving the existing completion and publication safeguards.

#### Design Details

All ordinary PRs and configured Dependabot updates, including Docker, use local review-pr, safe repairs through create-pr, fresh exact-head/base review and CI, then merge-pr. Standalone review records evidence without starting a merge; a complete review-and-merge request authorizes the sequence. Route Python, Actions and Docker changes to the matching specialists and stop for unsafe findings, unavailable meaningful validation or human objections. Every changed head or base invalidates earlier review.

Only an explicit trusted numeric operator workflow_dispatch can initiate a merge. Independently fetch and validate the positive GitHub COMMENT review ID, author, repository/PR/head/base and validation before intent and again before effects. Bot identity, metadata, labels and CI cannot replace review. Remove metadata admission and rollout toggles without compatibility paths; MERGE_OPERATOR_IDS is the only merge-specific variable. Preserve same-feature reuse, canonical repository-wide commits and preparation-only publication modes.

PR, CI and completion recovery events maintain the CI-only Required CI aggregate for ordinary and fork PRs and return no merge candidates. Upload an immutable admission receipt before approval. Keep per-job least privilege, protected asynchronous merge with bypass_rules false, exact-head approval, repeated CI/objection checks and maintainer handoff for queues or workflow-file permission denial. The shared Actions identity is a trust boundary.

Completion schedules recover only already-authorized operator-dispatched positive-review intents. Resolve the authoritative result through GraphQL mergeCommit.oid after independently binding merged state, PR number, reviewed head and base repository/ref; the pinned REST version has no result field. Missing or inconsistent result evidence stops before completion effects. Preserve duplicate suppression, uncertain-effect journals, authoritative result ancestry, explicit result CI and Pages dispatch. Each CI execution job loads its checkout validator from the trusted workflow revision and validates receipt and authoritative result before setup/cache restoration; fetch credentials remain process-local. Release publishers tag only the verified actual merge result. Agentic SDLC, existing credentials and Dependabot scheduling/configuration stay unchanged.

Keep separate CI, recovery and publication workflow boundaries for maintained repository projects and shared automation. Protected merge drops its cron and routes PR and CI in_progress/completed events to affected current open PR heads only. Completion owns hourly recovery at minute 17 and manual recovery; a separate least-privilege job repairs all-open-PR Required CI. Event jobs are filtered before runner allocation and use producer-run artifact discovery. Keep bounded effect-journal reconciliation where needed. Immutable early result-CI routing receipts bind repository/workflow/run/attempt, original broker/PR/result and correlation. Failed test runs may route recovery; only independently validated latest-attempt success evidence can complete it. Target receipt errors yield blocked target diagnostics and a failing summary while valid targets proceed. Global transport/authentication/permission/listing failures stop discovery before effects.

Preserve registered CI workflow names, dispatch fields, setup ordering and successful result artifacts. The CI inventory in `.github/merge-policy.json` covers maintained repository projects and shared automation; synchronize workflow listeners, path filters and publication-test inventory with those projects. Baseline CI checks that these definitions agree. Shared pure publication helpers own validation, ancestry, notes, manifests and verification without a generic privileged shell workflow. Release publishers upload immutable pre-effect manifests with exact tag object/commit, run provenance and asset SHA-256/size before publishing. Existing releases download and compare assets against trusted original evidence. Classify absent, verified-existing, conflict and unverifiable explicitly; missing/expired legacy evidence blocks automated repair, never manufactures expected hashes from remote assets.

Image tag builds exclusively own X.Y.Z and X.Y.Z-gSHA; manual builds own sha-SHA and optional main-only latest. Delete release_version without an alias. One destination image lock uses cancel-in-progress:false and queue:max across preflight, publication, verification and manifest. Inspect all immutable aliases, block partial/conflicting/unverifiable states, and verify every remote digest equals the build digest. Preserve existing remote tags, registry targets/credentials, Helm exact-package/anonymous-pull/digest checks and service-specific release qualification. The github-workflows release template is the sole YAML owner; publish-release delegates setup, and release observation uses the existing 10800-second phase budgets.

Reviewer routing uses trusted base policy to add configured nonauthor reviewers to same-repository nondraft PRs, preserving manually requested users and teams. No PR checkout or removal effect. Validate local source, template parity and installed payloads separately from GitHub deployment or live publication; this change does not authorize those effects.

The approved github-report retirement keeps `github-report-ci.yml` temporarily under its existing workflow name as a read-only validator. It checks that the project and release workflow are absent and that the candidate policy no longer requires its CI producer. This lets the previous trusted policy validate the retirement commit. After that policy reaches the default branch, a follow-up removes the validator and its focused transition test. The final state contains no github-report project, CI or release workflow; no protection is bypassed. The validator and focused executable regression are implemented. Local proof passes for the retired state and rejects a restored project, restored release workflow or retained policy entry. Live exact-head Required CI and subsequent validator removal remain pending.

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
- TDD-005: Execute the actual candidate shell block with offline helper stubs: empty/populated success arrays, blocked/pending/arbitrary nonzero statuses, empty failure output, unchanged output files on failure, one invocation and no environment-secret disclosure.

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

Current inventory alignment: the CI policy, both workflow listeners and release-test inventory cover the maintained project set. The specifications use that inventory instead of fixed workflow counts.

Implemented workflow rationalization locally: one hourly recovery, targeted PR CI start/completion aggregation, run-scoped completion discovery, early routing receipts and target-isolated blocked diagnostics. Preserved exact-result evidence and effect journals. Retained the then-current workflow boundaries and CI identities, added inventory/filter/listener contracts and add-only reviewer routing. Shared publication helpers enforce original asset hashes, tag-object identity, digest equality and fail-closed retries. Image release/manual aliases are disjoint under one destination queue; manual builds freeze the helper from the workflow revision. An explicit main-only latest request reuses a verified existing manual digest under the same lock, then rechecks every tag; it never rebuilds immutable aliases. Helm retains package/anonymous-pull/digest qualification with structured duplicate-repository observation. Release/image YAML templates have one source owner. Updated docs and installed the five affected skills from source.

Implemented candidate diagnostic visibility in the deployed completion workflow and its reusable template. Both capture the helper status, print a generic exit-status message plus nonempty sanitized output on failure, preserve the status, and publish targets only on success. The helper, transport-error suppression, API deadline, receipt gates and permissions are unchanged.

Implemented universal positive COMMENT review admission and explicit trusted operator dispatch. Deleted dependency metadata admission, its producer/template, policy key and obsolete check exclusion. Removed rollout controls with no replacement or compatibility path. Completion selection and CI checkout require operator-dispatched positive-review intents; immutable receipts, repeated CI/review gates, exact-result checkout, journals and publication ownership remain. Dependabot configuration is unchanged. Updated instructions, reusable assets, evaluations, READMEs and Unreleased changelogs; installed the four changed skill payloads from their verified source owners.

#### Verification Evidence

Current inventory alignment verification: all five workflow contract tests, scoped actionlint, Markdown lint, paired-spec validation and whitespace checks pass. Independent read-only review found no blocking correctness or security issues. These checks establish local source consistency only; deployed GitHub settings and live workflow runs were not exercised.

Local workflow rationalization verification: 58 merge-gate, 37 completion, 10 publication primitive, 18 publication evidence, 5 workflow contract, 2 template ownership, 57 release-session/doer, 39 cxcli publication/workflow/catalog and 11 VPN release tests pass (237 tests plus 56 release subtests). The VPN frozen-binary test initially lacked PyInstaller; it passed with the declared build dependency supplied through an isolated uv environment. Python lint/format, Markdown, paired-spec validation, core/Codex/Claude skill structure and workflow/template actionlint pass. Actionlint 1.7.12 does not recognize the currently documented concurrency.queue key: only that exact syntax diagnostic was excluded, with queue:max/noncanceling locks checked separately by contract tests. Independent read-only code/security review findings were repaired and covered by regressions. These are local source, fixture and installed-payload results; no GitHub deployment, live merge, release, registry push or native skill-trigger behavior is certified.

Candidate diagnostic visibility: the new executable shell regressions failed against the original workflow for all four failure scenarios, then passed after the repair. All 32 completion tests and 56 merge-gate tests pass, including deployed/template parity. Independent read-only code/security review found no blocking issue and separately passed the three new regression tests. Actionlint, Bash syntax, ShellCheck, Ruff lint/format, scoped Markdown and paired-spec validation pass. This proves the local logging repair only; the original API failure remains unconfirmed, and the repaired workflow has not been published or executed on GitHub.

Historical foundation evidence: 179 focused tests and initial skill/workflow checks passed; the maintainer bootstrap PR passed 41 checks with 13 expected skips, and cxcli passed 8,278 tests. The bootstrap merged on 2026-10-08 and legacy Pages built that result. A later alignment passed 77 merge/completion/publication/template tests and 21 service checks with source/installed parity. These results do not verify this redesign or token-driven completion. Current source, installed, CI and live evidence must be recorded separately after implementation; delivery remains unverified until acceptance is observed.

Current redesign evidence: 127 focused merge, completion, publication, release and template tests pass, including reviewed Python/Actions/Docker changes and safe source repairs, automatic-event non-initiation, malformed/stale review rejection and completion checkout provenance. Workflow actionlint, Python lint/format, publication shell checks, changed Markdown and paired spec validation pass. Independent code/security review found no blocking source issue. Eight skills pass portable/Codex/Claude structure checks and actual npx copy/repeat/isolation tests; all eight source/installed payloads and executable modes match. Native trigger and comparative quality probes are authentication-unavailable, not passing runtime evidence.

Live deployment evidence: maintainer bootstrap PR #216 landed on 2026-10-08. Its exact result passed skills CI, merge-contract CI, CodeQL and legacy main/root Pages publication. Authorized settings were applied and read back: the numeric operator list, strict Required CI bound to App ID 15368 and removal of the empty push restriction. Independent protection fields, read-only default token, Actions approvals and repository merge methods were retained. GitHub clears the dependent block_creations flag when restrictions are absent, even if explicitly requested true. No credentials or Environment were created. Old writers were quiescent and the earlier effect inventory was empty.

The first ordinary acceptance PR #217 recorded a trusted COMMENT review and immutable intent, then received exact-head Actions approval and a protected squash merge. Its completion stopped before CI/Pages effects because REST 2026-03-10 removed merge_commit_sha. The source repair uses one identity-bound GraphQL mergeCommit.oid lookup for merged observation, recovery, completion receipts and CI checkout; no old API or field fallback exists. Updated REST fixtures reproduce the failure, and result-identity/error regressions cover the repair. Read-only live source validation resolves the authoritative result and containment. The retained intent is the known-good checkpoint for deployed recovery; record its live outcome on the acceptance PR without treating source validation as deployed proof.

Source, installed, bootstrap CI and live merge evidence are distinct. Real Dependabot acceptance remains unverified because no eligible PR was open; Docker admission is covered offline. The workflow-changing bootstrap used a maintainer; built-in-token workflow-file permission behavior is not established by that result. Native trigger and comparative quality evaluation remain authentication-unavailable. Full delivery remains implemented rather than verified.

<!-- /FEATURE: FEAT-002 -->
<!-- FEATURE: FEAT-003 reqs=REQ-003 status=ready delivery=implemented priority=P1 version=1 -->
### FEAT-003: Scoped Actions event policies

#### Requirements Covered

- REQ-003: Restrict privileged PR events to reviewed automation.

#### Context Evidence

The deployed default branch has two `pull_request_target` workflows:
`.github/workflows/pr-reviewer-trimmer.yml` and
`.github/workflows/skills-merge-pr.yml`. Their trusted metadata operations need
PR-write or status-write permissions; contributor tests run separately.
GitHub documents path-scoped event restrictions in its Actions policy API.

#### Design Details

Use three active repository policies, each containing one
`restrict_action_events` rule. Reviewer routing events includes only the exact
reviewer-routing path and allows `pull_request_target`. Protected merge events
includes only the exact broker path and allows `pull_request_target`,
`workflow_run`, and `workflow_dispatch`. Other workflow events includes `~ALL`,
excludes those two paths and allows the frozen official event inventory except
`pull_request_target`. The sets do not overlap; add no actor restriction.

Capture existing settings before effects. Create the baseline policy before
its exceptions, persist returned IDs, then independently GET every policy and
adjacent settings. Reconcile later changes by ID and exact observed body;
concurrent drift or an ambiguous write outcome stops further effects.

#### Selected Option

Explicit path-based allowances with an explicit remaining-workflows rule retain
the current trusted workflow architecture without relying on future defaults.

#### Alternatives Considered

Replacing the event with `pull_request` changes checkout trust and breaks the
broker's default-ref gate. Broad permission for all workflows expands the
exception. Relying on an implicit default leaves its interaction with future
custom policies outside the repository's explicit configuration.

#### Implementation Boundaries

Own repository Actions event policies, root specifications and documentation.
Keep workflow source, helpers, tokens, secrets, actor restrictions, branch
protection, contributor approval and application interfaces unchanged. Deliver
local documentation without committing, pushing or merging.

#### Test-First Success Criteria

- TDD-001: Initial policy readback has no explicit policies; each desired path
  and event mapping becomes independently observable after creation.
- TDD-002: Existing automatic-event merge rejection remains green. Static policy
  checks admit the two exceptions and reject the event for any other path.

#### Validation Plan

Read back all affected and preserved settings independently, validate policy
partition and current trigger coverage, lint Markdown, validate specs and run
changed-scope align with nested code and security review.

#### Test Plan

Run the focused merge-admission and workflow-contract cases. Inspect every
trusted checkout, exact job permissions and reviewer fork/draft conditions
where existing tests do not provide coverage. Exercise no merge effects.

#### Evaluation Plan

Observe subsequent natural ordinary and fork PR/CI events, with exact workflow
revision, event and run IDs. Keep missing live evidence explicitly pending.
Policy matching checks are not proof of live GitHub negative enforcement.

#### Rollout And Rollback

Create the remaining-workflows policy, then the two exceptions, using existing
administrative authentication. Verify inherited policies before each change.
To roll back an exception safely, remove its path from the baseline exclusions
and verify that readback before disabling the exception by ID. This blocks its
PR event while retaining other declared triggers. Never reset unrelated policy.

#### Done Definition

Exact policy readbacks match the intended configuration, adjacent protections
are unchanged, local checks pass and operational ownership is documented.
Full live verification additionally requires representative post-activation
runs; otherwise delivery remains implemented with live acceptance pending.

#### Implementation Evidence

Created three active repository policies on 2026-10-30: Other workflow events
(ID 10050), Reviewer routing events (ID 10051) and Protected merge events
(ID 10052). Exact path scopes and the frozen event inventory match this design.
Updated the operations guide, root README and Unreleased changelog locally.
No workflow source, branch, commit, push, merge or fork-approval effect occurred.

#### Verification Evidence

Independent API readbacks matched all three intended policy bodies. Branch
protection, rulesets, Actions permissions, workflow-token/PR permissions and
fork-contributor approval configuration matched captured baselines. Seven
existing focused tests passed against trusted default commit
`644b5a17515d30786a226a069c68d2eba9e0ee40`; read-only review covered the trusted
checkout, job permissions and reviewer same-repository/draft boundaries.
All 16 deployed workflow paths retained their declared triggers. The 684
path/event partition cases passed, and unknown event types were rejected by
the allowlists. No fresh post-activation workflow run was available during
implementation: live-event and GitHub negative-enforcement evidence remain
pending, so the requirement is active and delivery is implemented, not verified.

<!-- /FEATURE: FEAT-003 -->

<!-- maintain-project-specs:design:end -->
<!-- markdownlint-enable MD001 MD024 -->
