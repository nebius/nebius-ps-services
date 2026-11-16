<!-- markdownlint-disable MD001 MD024 -->
<!-- markdownlint-disable-next-line MD013 -->
<!-- maintain-project-specs:design:start schema=maintain-project-specs/design-v2 -->
# Project Design

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-001 reqs=REQ-001 status=ready delivery=implemented priority=P0 version=2 -->
### FEAT-001: Trusted hosted execution

#### Requirements Covered

- REQ-001: Run the PR lifecycle on GitHub-hosted runners using pinned trusted controller code, Codex and provider-neutral skills. No App, PAT fallback or self-hosted runner.

#### Context Evidence

Approved design; existing protected merge and publication helpers under skills; project workflows at repository root.

#### Design Details

Use a Python CLI with typed contracts, provider adapter and separate effect jobs. Never execute PR instructions in privileged jobs. Pin runtime/model/skills/policy identities. Load the qualified runtime by immutable OCI digest to preserve stage reuse across continuations. Real provider qualification is required.

#### Selected Option

Deterministic controller authority with bounded AI proposals and fail-closed evidence validation.

#### Alternatives Considered

Reject direct agent merge authority, competing laptop publishers and unverified provider compatibility.

#### Implementation Boundaries

This component owns runtime orchestration; existing skills retain task ownership. Preserve unrelated dirty work.

#### Test-First Success Criteria

- TDD-001: Contract tests reject missing, stale, forged and conflicting evidence.

#### Validation Plan

uv run --locked pytest; uv run --locked ruff check .; owner spec validation.

#### Test Plan

Unit policy tests, storage concurrency tests, workflow contracts and existing merge/publication regressions.

#### Evaluation Plan

Real target and provider checks remain separately reported until observed.

#### Rollout And Rollback

Default disabled. Enable report, repair and merge after qualification. Disabling stops newly admitted jobs while preserving owned cleanup and reconciliation. Already running jobs retain their environment snapshot and must be cancelled for an immediate stop.

#### Done Definition

Implementation, local checks and configured end-to-end qualification pass with retained evidence.

#### Implementation Evidence

Implemented typed runtime contracts, source export, restricted Responses relay, Docker sandbox and explicit skill bundle. The local image runs pinned Codex as the restricted user.

#### Verification Evidence

Local Docker image build and non-root/read-only/no-network/no-Docker-socket smoke passed; Codex CLI 0.167.0 observed. Authenticated Responses/tool/schema loop not run.

<!-- /FEATURE: FEAT-001 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-002 reqs=REQ-002 status=ready delivery=implemented priority=P0 version=3 -->
### FEAT-002: Coordinated PR effects

#### Requirements Covered

- REQ-002: Laptop and Actions requests share one PR operation and merge authority. Exact head/base review, CI, live evidence and GitHub protection must authorize every merge.

#### Context Evidence

Approved design; existing protected merge and publication helpers under skills; project workflows at repository root.

#### Design Details

Use conditional private storage, leases, fencing and persisted effect intents. Reconcile unknown outcomes. Read-only review never merges. Repairs use non-forced compare-and-set updates with a two-attempt budget. Forks and control-plane changes remain report-only. Human objections remain gating.

#### Selected Option

Deterministic controller authority with bounded AI proposals and fail-closed evidence validation.

#### Alternatives Considered

Reject direct agent merge authority, competing laptop publishers and unverified provider compatibility.

#### Implementation Boundaries

This component owns runtime orchestration; existing skills retain task ownership. Preserve unrelated dirty work.

#### Test-First Success Criteria

- TDD-001: Contract tests reject missing, stale, forged and conflicting evidence.

#### Validation Plan

uv run --locked pytest; uv run --locked ruff check .; owner spec validation.

#### Test Plan

Unit policy tests, storage concurrency tests, workflow contracts and existing merge/publication regressions.

#### Evaluation Plan

Real target and provider checks remain separately reported until observed.

#### Rollout And Rollback

Default disabled. Enable report, repair and merge after qualification. Disabling stops newly admitted jobs while preserving owned cleanup and reconciliation. Already running jobs retain their environment snapshot and must be cancelled for an immediate stop.

#### Done Definition

Implementation, local checks and configured end-to-end qualification pass with retained evidence.

#### Implementation Evidence

Implemented PR leases/fencing, structured review, bounded fixes/conflict repair, candidate CI recovery and the sole protected merge broker. Laptop clients use the same hosted owner.

#### Verification Evidence

Local authority/recovery and protected merge regressions passed. Added rejection coverage for renaming protected files, publishing prepared repairs in report mode, and invalid policy before closure or base synchronization. Real GitHub merge, protection, concurrent runner and bot-authored PR acceptance not run.

<!-- /FEATURE: FEAT-002 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-003 reqs=REQ-003 status=ready delivery=unassessed priority=P0 version=2 -->
### FEAT-003: Specific live tests

#### Requirements Covered

- REQ-003: Generate a minimal meaningful live scenario for each PR, freeze assertions, verify product behavior independently and clean owned resources. Failed or unavailable live tests leave the PR open.

#### Context Evidence

Approved design; existing protected merge and publication helpers under skills; project workflows at repository root.

#### Design Details

Separate fixtures, product actions and observers. Trusted capability adapters alone hold cloud credentials. Unsupported privileged behavior is unavailable. Test destination defaults belong to this deployment only; explicit configured profiles override. Quota wait is bounded at 120 seconds; unsupported quota requests fail closed. Stage and live timeouts are 30 and 60 minutes.

#### Selected Option

Deterministic controller authority with bounded AI proposals and fail-closed evidence validation.

#### Alternatives Considered

Reject direct agent merge authority, competing laptop publishers and unverified provider compatibility.

#### Implementation Boundaries

This component owns runtime orchestration; existing skills retain task ownership. Preserve unrelated dirty work.

#### Test-First Success Criteria

- TDD-001: Contract tests reject missing, stale, forged and conflicting evidence.

#### Validation Plan

uv run --locked pytest; uv run --locked ruff check .; owner spec validation.

#### Test Plan

Unit policy tests, storage concurrency tests, workflow contracts and existing merge/publication regressions.

#### Evaluation Plan

Real target and provider checks remain separately reported until observed.

#### Rollout And Rollback

Default disabled. Enable report, repair and merge after qualification. Disabling stops newly admitted jobs while preserving owned cleanup and reconciliation. Already running jobs retain their environment snapshot and must be cancelled for an immediate stop.

#### Done Definition

Implementation, local checks and configured end-to-end qualification pass with retained evidence.

#### Implementation Evidence

Implemented scenario contracts, pinned-driver admission, deadlines and durable cleanup recovery. No hosted Nebius capability is qualified or enabled; actual product test execution remains unavailable.

#### Verification Evidence

Local policy tests reject missing/failed/intervened live evidence and pending cleanup. No Nebius provisioning or product acceptance was performed.

<!-- /FEATURE: FEAT-003 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-004 reqs=REQ-004 status=ready delivery=implemented priority=P0 version=2 -->
### FEAT-004: Lifetime PR waiver

#### Requirements Covered

- REQ-004: An authorized creator can waive eligible missing or inapplicable live validation for one immutable PR identity, across updates and reopenings, until explicit revocation. Failed/intervened evidence and known defects remain blocking.

#### Context Evidence

Approved design; existing protected merge and publication helpers under skills; project workflows at repository root.

#### Design Details

Create draft, register waiver, then ready. Store actor and reason privately with a sanitized projection. Labels never authorize. Waiver does not waive failed/intervened trials, known defects, CI, review, protections or cleanup. Serialize waiver changes and merge effects.

#### Selected Option

Deterministic controller authority with bounded AI proposals and fail-closed evidence validation.

#### Alternatives Considered

Reject direct agent merge authority, competing laptop publishers and unverified provider compatibility.

#### Implementation Boundaries

This component owns runtime orchestration; existing skills retain task ownership. Preserve unrelated dirty work.

#### Test-First Success Criteria

- TDD-001: Contract tests reject missing, stale, forged and conflicting evidence.

#### Validation Plan

uv run --locked pytest; uv run --locked ruff check .; owner spec validation.

#### Test Plan

Unit policy tests, storage concurrency tests, workflow contracts and existing merge/publication regressions.

#### Evaluation Plan

Real target and provider checks remain separately reported until observed.

#### Rollout And Rollback

Default disabled. Enable report, repair and merge after qualification. Disabling stops newly admitted jobs while preserving owned cleanup and reconciliation. Already running jobs retain their environment snapshot and must be cancelled for an immediate stop.

#### Done Definition

Implementation, local checks and configured end-to-end qualification pass with retained evidence.

#### Implementation Evidence

Implemented authorized human dispatch, immutable PR identity, durable waiver revision and revocation. Draft creation waits for registration; cleanup and other gates cannot be waived.

#### Verification Evidence

Local tests cover lifetime identity, cross-PR rejection, actor authority and revocation/merge serialization. Hosted draft-to-waiver-to-ready flow not run.

<!-- /FEATURE: FEAT-004 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-005 reqs=REQ-005 status=ready delivery=implemented priority=P0 version=4 -->
### FEAT-005: Automatic package releases

#### Requirements Covered

- REQ-005: Package-affecting merges trigger project releases using per-change semver fragments. Documentation and test-only changes do not trigger releases.

#### Context Evidence

Approved design; existing protected merge and publication helpers under skills; project workflows at repository root.

#### Design Details

Registry initially enrolls vpngw and nebius-cxcli. Highest pending fragment bump selects next version from the last completed release. One coordinator owns version allocation, annotated tags, builds and publication for manual and automatic requests. Retain project-specific wheel and cxcli manifest validation. No second release PR.

#### Selected Option

Deterministic controller authority with bounded AI proposals and fail-closed evidence validation.

#### Alternatives Considered

Reject direct agent merge authority, competing laptop publishers and unverified provider compatibility.

#### Implementation Boundaries

This component owns runtime orchestration; existing skills retain task ownership. Preserve unrelated dirty work.

#### Test-First Success Criteria

- TDD-001: Contract tests reject missing, stale, forged and conflicting evidence.

#### Validation Plan

uv run --locked pytest; uv run --locked ruff check .; owner spec validation.

#### Test Plan

Unit policy tests, storage concurrency tests, workflow contracts and existing merge/publication regressions.

#### Evaluation Plan

Real target and provider checks remain separately reported until observed.

#### Rollout And Rollback

Default disabled. Enable report, repair and merge after qualification. Disabling stops newly admitted jobs while preserving owned cleanup and reconciliation. Already running jobs retain their environment snapshot and must be cancelled for an immediate stop.

#### Done Definition

Implementation, local checks and configured end-to-end qualification pass with retained evidence.

#### Implementation Evidence

Implemented package registry, immutable semver fragments, exact-result completion admission, one release reservation, build-only service workflows and original-asset verification. Local tag paths are retired for enrolled projects.

#### Verification Evidence

Local tests cover semver, reservations, tag recovery, lease-loss protection and immutable assets. Added regressions for renamed fragments, all cxcli wheel inputs, invalid tag assertions and pending-source persistence. Release-skill shared-client tests cover explicit repository selection outside the checkout with an inherited repository override. Real build/publish/recovery qualification not run.

<!-- /FEATURE: FEAT-005 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-006 reqs=REQ-006 status=ready delivery=implemented priority=P0 version=3 -->
### FEAT-006: Credential and input isolation

#### Requirements Covered

- REQ-006: Keep secrets and private target identifiers out of public artifacts, logs, prompts and repository files. Use the github-ai-automation environment on the trusted default branch.

#### Context Evidence

Approved design; existing protected merge and publication helpers under skills; project workflows at repository root.

#### Design Details

Separate model forwarding, candidate tools, trusted cloud executor and publisher. Build without write credentials. Validate uploaded evidence against producer identity and artifact hashes. Candidate processes have no GitHub/cloud credentials or metadata access. Missing credentials block safely.

#### Selected Option

Deterministic controller authority with bounded AI proposals and fail-closed evidence validation.

#### Alternatives Considered

Reject direct agent merge authority, competing laptop publishers and unverified provider compatibility.

#### Implementation Boundaries

This component owns runtime orchestration; existing skills retain task ownership. Preserve unrelated dirty work.

#### Test-First Success Criteria

- TDD-001: Contract tests reject missing, stale, forged and conflicting evidence.

#### Validation Plan

uv run --locked pytest; uv run --locked ruff check .; owner spec validation.

#### Test Plan

Unit policy tests, storage concurrency tests, workflow contracts and existing merge/publication regressions.

#### Evaluation Plan

Real target and provider checks remain separately reported until observed.

#### Rollout And Rollback

Default disabled. Enable report, repair and merge after qualification. Disabling stops newly admitted jobs while preserving owned cleanup and reconciliation. Already running jobs retain their environment snapshot and must be cancelled for an immediate stop.

#### Done Definition

Implementation, local checks and configured end-to-end qualification pass with retained evidence.

#### Implementation Evidence

Implemented credential-separated jobs/processes, environment-only target defaults, model inference ticket, read-only candidate tokens, protected source and sanitized public outcomes.

#### Verification Evidence

Actionlint and scoped source/security review passed locally. Targeted sensitive-value scans found no matching token, private-key or private target-ID patterns in the inspected changes; this is not exhaustive proof. Adversarial hosted isolation and egress qualification still required.

<!-- /FEATURE: FEAT-006 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-007 reqs=REQ-007 status=ready delivery=implemented priority=P0 version=3 -->
### FEAT-007: Durable evidence and recovery

#### Requirements Covered

- REQ-007: Interrupted operations recover without duplicate merges, resources, tags or publications. Preserve original budgets and independent proof boundaries.

#### Context Evidence

Approved design; existing protected merge and publication helpers under skills; project workflows at repository root.

#### Design Details

Persist intents before effects in private regular Object Storage using conditional writes. Bind receipts to repository, PR, head/base, producer run/attempt, source/build and policy. Do not infer candidate identity from default-ref workflow SHA. Preserve release assets and reject conflicting bytes.

#### Selected Option

Deterministic controller authority with bounded AI proposals and fail-closed evidence validation.

#### Alternatives Considered

Reject direct agent merge authority, competing laptop publishers and unverified provider compatibility.

#### Implementation Boundaries

This component owns runtime orchestration; existing skills retain task ownership. Preserve unrelated dirty work.

#### Test-First Success Criteria

- TDD-001: Contract tests reject missing, stale, forged and conflicting evidence.

#### Validation Plan

uv run --locked pytest; uv run --locked ruff check .; owner spec validation.

#### Test Plan

Unit policy tests, storage concurrency tests, workflow contracts and existing merge/publication regressions.

#### Evaluation Plan

Real target and provider checks remain separately reported until observed.

#### Rollout And Rollback

Default disabled. Enable report, repair and merge after qualification. Disabling stops newly admitted jobs while preserving owned cleanup and reconciliation. Already running jobs retain their environment snapshot and must be cancelled for an immediate stop.

#### Done Definition

Implementation, local checks and configured end-to-end qualification pass with retained evidence.

#### Implementation Evidence

Implemented conditional durable state, lost-CI callback reconstruction, lease-loss rejection, pre-submission versus uncertain merge recovery, release tag/asset reconciliation and cleanup recovery.

#### Verification Evidence

Regression tests cover lost dispatch, lost CI callback, CAS fencing, uncertain effects and pre-submit retries. Explicit invalid tag assertions cannot poison durable release recovery; requests without tag assertions remain queued while another source is pending. Live Object Storage contention and cloud cleanup interruption not run.

<!-- /FEATURE: FEAT-007 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-008 reqs=REQ-008 status=ready delivery=unassessed priority=P0 version=5 -->
### FEAT-008: Qualification and rollout

#### Requirements Covered

- REQ-008: Ship disabled, qualify real provider and lifecycle behavior, then enable report, repair and merge stages. Control PR automation with repository Actions variable `AI_AUTOMATION_PR_REVIEW` (`disabled`, `report`, `repair`, `merge`), release publishing with `AI_AUTOMATION_RELEASE_PUBLISH` (`false`, `true`), and fork merge eligibility with `AI_AUTOMATION_MERGE_FORK_CONTRIBUTION` (`false`, `true`). Defaults are `disabled`, `false`, and `false`. Update skills and canonical documentation with measured evidence.

#### Context Evidence

Approved design; existing protected merge and publication helpers under skills; project workflows at repository root.

#### Design Details

Use the repository-level controls named in REQ-008. Workflows pass `vars.AI_AUTOMATION_PR_REVIEW` with a `disabled` fallback and `vars.AI_AUTOMATION_RELEASE_PUBLISH` with a `false` fallback; controller readers use only those canonical names. Fork merging remains default-false and subordinate to PR mode and every qualification gate. These are independent PR and release controls, not one application-wide switch. Before explicit activation, keep the ordinary protected broker, completion workflow and tag-driven release publishers installed. Store controller effect workflows, enrolled policy, enrollment metadata and reusable builders under `deployment/` as installation templates. Source CI remains active. A separately authorized rollout installs the prepared authority only after qualification; after installation the repository variables control subsequent runs. Keep existing request budgets and activation epochs intact.

Scaffold through scaffold-project and Python owner. Bundle reviewed transitive skills without arbitrary personal hooks. Use apply-security and align. Explicit dispatch handles token-event suppression. No claim of live qualification from static tests. Actions-authored PR approval limitations remain visible.

#### Selected Option

Deterministic controller authority with bounded AI proposals and fail-closed evidence validation.

#### Alternatives Considered

Reject direct agent merge authority, competing laptop publishers and unverified provider compatibility.

#### Implementation Boundaries

This component owns runtime orchestration; existing skills retain task ownership. Preserve unrelated dirty work.

#### Test-First Success Criteria

- TDD-001: Contract tests reject missing, stale, forged and conflicting evidence.
- TDD-002: Dormant deployment has no active enrollment, mandatory controller policy
  or installed automation effect workflow; ordinary publication remains wired.

#### Validation Plan

uv run --locked pytest; uv run --locked ruff check .; owner spec validation.

#### Test Plan

Unit policy tests, storage concurrency tests, workflow contracts and existing merge/publication regressions.

#### Evaluation Plan

Real target and provider checks remain separately reported until observed.

#### Rollout And Rollback

Set the three repository controls to `disabled`, `false`, and `false` before deployment. Remove retired PR/release variable names and environment shadows after verifying the replacement off values. Prior workflow revisions also remain off because their missing controls default off. The rename does not enable automation or reset active request budgets.

Default disabled. Enable report, repair and merge after qualification. Disabling stops newly admitted jobs while preserving owned cleanup and reconciliation. Already running jobs retain their environment snapshot and must be cancelled for an immediate stop.

#### Done Definition

Implementation, local checks and configured end-to-end qualification pass with retained evidence.

#### Implementation Evidence

Renamed PR/release controls across runtime readers, intake conditions, hosted workflows, enrolled templates and test fixtures. Repository Actions values are explicitly disabled/false/false, with retired environment control names removed and no same-name shadows. No compatibility alias, admission-epoch change or request-state migration. Source changes remain local; real provider/cloud/hosted lifecycle qualification remains pending.

#### Verification Evidence

New-name tests failed against the previous readers, then passed after the rename. All 120 project tests, 78 shared merge/workflow tests and 138 subtests passed; Ruff, Actionlint on eight workflows and Markdown structural checks passed. Independent scoped review found no blocker. GitHub API read-back verified the three repository off values, absence of retired environment names and no environment/organization overrides. Strict spec tracking validation remains advisory while project files are untracked. These checks do not qualify hosted PR or release effects; provider/state/cloud credentials and qualified live capabilities remain prerequisites.

<!-- /FEATURE: FEAT-008 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-009 reqs=REQ-009 status=ready delivery=verified priority=P2 version=2 -->
### FEAT-009: Portable workflow guide

#### Requirements Covered

- REQ-009: Portable, readable workflow documentation under docs.

#### Context Evidence

README, architecture, controller.process, policy.qualification and publication
release intake/preparation/publication define the existing behavior. The supplied
release chart incorrectly shares the no-release outcome with successful release;
the guide gives these paths separate outcomes and exposes mode restrictions.

#### Design Details

Use the general-document profile: warm neutral page, navigation, four inline SVG
charts for intake, repair, qualification and release, plus semantic text steps.
Embed all CSS and small deterministic JavaScript in docs/workflow-guide.html.
Keep navigation as ordinary fragments and zoom state in memory. Print every
section, with diagrams and text, independent of the currently browsed section.
Use A3 portrait at 255 mm diagram width to preserve long-chart label readability.
Use a local font stack and no dependencies, fetches, storage or runtime AI.

#### Selected Option

One progressively enhanced HTML document with static SVG supports offline use,
embedding and readable no-script content. The fixed native web stack is sufficient.
AI, provider and application-stack redesign are outside this documentation scope.
Review local accessibility, portability and content boundaries; a platform design
review is unnecessary for this reversible artifact.

#### Alternatives Considered

Raw Mermaid preserves editable source but depends on a renderer. A static image
is simpler but lacks searchable text and useful zoom/navigation. A hosted site
adds deployment and network dependencies that the requested artifact does not need.

#### Implementation Boundaries

Only the HTML guide, its README link, changelog and this canonical pair change.
No runtime, workflow, cloud, credential or publication behavior changes.

#### Test-First Success Criteria

- TDD-009: Direct-file offline reading preserves every branch and operating limit.
- TDD-010: Printed and no-script views include all four workflows and explanations.

#### Validation Plan

Use Playwright Chromium for offline file and relocated-copy checks, no automatic
request attempts, interactions, keyboard, 320/1440-pixel layouts, enlarged text,
no-script content and rendered print pages. Record other-browser gaps explicitly.

#### Test Plan

Perform bounded artifact acceptance checks and focused HTML/script syntax,
Markdown, link, content and source reviews; no application runtime regression
suite is needed for this isolated documentation addition.

#### Evaluation Plan

Independently compare branch meaning with the source and inspect screenshots
and printed pages for label overlap, clipping and complete evidence.

#### Rollout And Rollback

Open or copy the HTML; optionally embed it in a local iframe. Remove the file
and documentation link to roll back. No service rollout or external publication.

#### Done Definition

The artifact and README entry exist; scoped acceptance checks and alignment pass;
browser limitations and runtime qualification boundaries remain explicit.

#### Implementation Evidence

Delivered docs/workflow-guide.html with four static SVG flowcharts, semantic
text equivalents, fragment navigation and bounded in-memory zoom. README links
the guide and describes offline, iframe and print use; CHANGELOG records it.
The guide is approximately 53 KB and embeds all required assets.

#### Verification Evidence

Playwright with local Chrome 157.0.8103.39 passed offline direct-file opening,
renamed isolated-copy opening, no-JavaScript reading, all zoom controls and
bounds, navigation, deep links, history, keyboard focus/activation, 320/768/1440
pixel reflow and enlarged rendering. No page errors or automatic network
requests were observed. Normal iframe navigation/zoom and sandboxed static
fallback passed. Both scripted and no-script printouts contain eight A3 pages;
rendered pages were inspected for complete diagrams, readable labels and text.
Static HTML, fragment links and JavaScript syntax passed. Changed Markdown
introduces no new lint findings; 118 pre-existing line-length findings remain.
A read-only source review confirmed branch semantics and identified a recovery
arrow crossing and small print labels; both were corrected and rechecked.
Other browsers and deployed automation behavior were not tested.

<!-- /FEATURE: FEAT-009 -->
<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-010 reqs=REQ-010 status=ready delivery=verified priority=P1 version=2 -->
### FEAT-010: Retire project-local release publishers

#### Requirements Covered

- REQ-010: One maintainer release entry point.

#### Context Evidence

Before cleanup, two project wrappers delegated to the shared client while the
account-management script independently committed, tagged and published. Their
README procedures and wrapper-specific tests exposed competing entry points.

#### Design Details

Delete all three scripts. Keep publish-release as the supported maintainer workflow
and the shared Actions coordinator as the enrolled execution backend. Remove
README publishing commands, stale tree/TOC entries and active developer instructions.
Preserve product consumption, historical release notes and image/Helm guidance.
Consolidate dispatch/rejection tests under publish-release; remove the obsolete
vpngw wrapper-test CI step. Setup guidance must not regenerate these scripts.
Account-management enrollment is deferred; missing enrollment remains a blocker.

#### Selected Option

One public skill workflow with existing private implementation components.

#### Alternatives Considered

Keeping wrappers retains competing interfaces. Removing the shared publisher would
break automatic releases. Enrolling account management would exceed this cleanup.

#### Implementation Boundaries

Project scripts, affected documentation, scoped skill guidance, regression tests
and the obsolete CI caller only. No new runtime, cloud operation or publication.

#### Test-First Success Criteria

- TDD-001: Shared-client tests cover both enrolled projects and reject unsupported
  projects or incomplete identities without a publication effect.
- TDD-002: No active caller or README procedure depends on a removed script.

#### Validation Plan

Run focused release/client/workflow tests, scoped Python/Markdown lint, Actionlint
and align. Preserve unrelated dirty files and consumer documentation.

#### Test Plan

Check exact workflow inputs, explicit repository selection, invalid requests and
absence of local tag/commit effects using fake GitHub responses.

#### Evaluation Plan

Local interface/documentation qualification only; no live release claim.

#### Rollout And Rollback

Ship the removals together with caller/docs/test updates. Refresh installed skills
through their existing rollout separately. No enabled flags or remote state change.

#### Done Definition

Removed scripts have no active consumers; shared publication behavior and focused
checks remain intact. Applicable docs and changelogs reflect the single entry point.

#### Implementation Evidence

Removed the three project-local scripts, their wrapper-only tests and the stale
vpngw CI caller. Consolidated shared-client coverage under the release skill and
updated setup guidance, READMEs, developer/design references and changelogs.
Existing Actions publication, package enrollment and image/Helm paths are unchanged.

#### Verification Evidence

The 64 release-skill tests, service workflow/documentation checks and five shared
workflow-contract tests passed. Scoped Markdown, Python, Actionlint, TOC and caller
checks passed; read-only review found no serious issue. These are local cleanup
checks, not installed-skill activation or live publication qualification.

<!-- /FEATURE: FEAT-010 -->
<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-011 reqs=REQ-011 status=ready delivery=implemented priority=P0 version=2 -->
### FEAT-011: Explicit PR requests and terminal lifecycle

#### Requirements Covered

- REQ-011: Automatically enroll only newly opened PRs in the enabled admission epoch. New drafts wait for readiness. Existing or terminal PRs require a fresh authenticated manual request. Recovery resumes registered active requests only, preserves review intent, three-hour deadline and two-repair allowance, and never enrolls the backlog.

#### Context Evidence

Approved request-scoped automation plan and inspected controller, recovery, CI and broker paths.

#### Design Details

One CAS-owned request per immutable PR identity carries origin, capabilities, candidate, stage checkpoints and terminal disposition. Run reruns preserve identity; distinct manual dispatches create successors only after pending effects settle. Head/base drift invalidates evidence without resetting budgets. Cleanup remains recoverable.

#### Selected Option

Deterministic request and effect owners with bounded isolated agent assessments.

#### Alternatives Considered

Reject backlog sweeps, direct model effect authority, fork branch writes and compatibility shims.

#### Implementation Boundaries

Automation controller, enrolled workflow/helper contracts and shared protected broker. Preserve unrelated edits.

#### Test-First Success Criteria

- TDD-001: New acceptance fixtures reproduce unsupported admission and authority paths before repair.

#### Validation Plan

Focused pytest, workflow contracts, Ruff, schema checks and changed-scope alignment.

#### Test Plan

Request lifecycle, meaningfulness, fork capability, exact evidence, effect races and recovery fixtures.

#### Evaluation Plan

Hosted provider, adversarial isolation, semantic false-closure and live qualification remain separate.

#### Rollout And Rollback

Keep disabled and fork merging false. Quiesce prior writers, reconcile old effects, retain history and require manual requests for old operations. Refresh admission epoch on enablement.

#### Done Definition

Source implementation and focused checks pass; hosted qualification is reported independently.

#### Implementation Evidence

`requests.py`, `recovery.py`, intake/controller workflows and CLI implement epoch-based enrollment, draft readiness, stable budgets, terminal state and explicit legacy retirement.

#### Verification Evidence

`tests/unit/test_requests.py` covers old PRs/events, draft epochs, terminal successors, reruns, authority ceilings, uncertain effects and interrupted history archival. The original backlog-recovery regression failed before the change and passes now.

<!-- /FEATURE: FEAT-011 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-012 reqs=REQ-012 status=ready delivery=implemented priority=P0 version=2 -->
### FEAT-012: Purposeful contribution gate

#### Requirements Covered

- REQ-012: Assess concrete usefulness before repairs, candidate CI or live tests. Useful small code, documentation, test, accessibility and maintenance changes qualify. Judge contribution evidence rather than author motives, diff size or missing rationale.

#### Context Evidence

Approved request-scoped automation plan and inspected controller, recovery, CI and broker paths.

#### Design Details

Use typed meaningful/no_benefit/uncertain assessments covering the complete contribution diff and accepted baseline. A no-benefit closure requires two separate fresh assessments with evidence and counterevidence, frozen PR rationale/human discussion and no conflicting human review; uncertainty stays open. Preserve deterministic redundancy proofs.

#### Selected Option

Deterministic request and effect owners with bounded isolated agent assessments.

#### Alternatives Considered

Reject backlog sweeps, direct model effect authority, fork branch writes and compatibility shims.

#### Implementation Boundaries

Automation controller, enrolled workflow/helper contracts and shared protected broker. Preserve unrelated edits.

#### Test-First Success Criteria

- TDD-001: New acceptance fixtures reproduce unsupported admission and authority paths before repair.

#### Validation Plan

Focused pytest, workflow contracts, Ruff, schema checks and changed-scope alignment.

#### Test Plan

Request lifecycle, meaningfulness, fork capability, exact evidence, effect races and recovery fixtures.

#### Evaluation Plan

Hosted provider, adversarial isolation, semantic false-closure and live qualification remain separate.

#### Rollout And Rollback

Keep disabled and fork merging false. Quiesce prior writers, reconcile old effects, retain history and require manual requests for old operations. Refresh admission epoch on enablement.

#### Done Definition

Source implementation and focused checks pass; hosted qualification is reported independently.

#### Implementation Evidence

`stages.py` and `controller.py` assess the full three-dot diff, accepted baseline, PR rationale and human discussion before repair/CI/live. `closure.py` requires blind confirmation and fresh semantic proof for no-benefit closure.

#### Verification Evidence

`test_stages.py` verifies gate order, blind confirmation and uncertainty stops. `test_effect_boundaries.py` rejects changed rationale and tests attributable compensation versus human closure. Real model false-closure evaluation remains pending.

<!-- /FEATURE: FEAT-012 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-013 reqs=REQ-013 status=ready delivery=implemented priority=P0 version=2 -->
### FEAT-013: Verified ordered review and repair

#### Requirements Covered

- REQ-013: Require ordered value, safety, bounded conflict repair, code review, alignment, exact-candidate CI, live qualification and cleanup before protected merging. Do not equate bundled skills or a model claim with validation.

#### Context Evidence

Approved request-scoped automation plan and inspected controller, recovery, CI and broker paths.

#### Design Details

Persist candidate-bound stage receipts and independent validation. Conflicts preserve both parent intents; independently verify before publication. Limit agent conflict edits to five files and 200 added/deleted non-marker lines excluding clean base changes. Requalify after repairs. CI precedes live execution; callbacks resume missing stages.

#### Selected Option

Deterministic request and effect owners with bounded isolated agent assessments.

#### Alternatives Considered

Reject backlog sweeps, direct model effect authority, fork branch writes and compatibility shims.

#### Implementation Boundaries

Automation controller, enrolled workflow/helper contracts and shared protected broker. Preserve unrelated edits.

#### Test-First Success Criteria

- TDD-001: New acceptance fixtures reproduce unsupported admission and authority paths before repair.

#### Validation Plan

Focused pytest, workflow contracts, Ruff, schema checks and changed-scope alignment.

#### Test Plan

Request lifecycle, meaningfulness, fork capability, exact evidence, effect races and recovery fixtures.

#### Evaluation Plan

Hosted provider, adversarial isolation, semantic false-closure and live qualification remain separate.

#### Rollout And Rollback

Keep disabled and fork merging false. Quiesce prior writers, reconcile old effects, retain history and require manual requests for old operations. Refresh admission epoch on enablement.

#### Done Definition

Source implementation and focused checks pass; hosted qualification is reported independently.

#### Implementation Evidence

`stages.py`, `repairs.py`, `sandbox.py` and source/controller/effect owners separate independent repair assessment, bounded conflict delta, actual credentialless checks and publication. Cached stages retain complete producer/context identity; candidate CI precedes live execution.

#### Verification Evidence

`test_stages.py` verifies cache reuse across CI callbacks and rejects model-only success. Real Git conflict/diff fixtures and isolated validation-command assertions pass. Focused profile and live registries remain empty/unqualified; no hosted repair or cloud qualification is claimed.

<!-- /FEATURE: FEAT-013 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-014 reqs=REQ-014 status=ready delivery=implemented priority=P0 version=2 -->
### FEAT-014: Controlled fork merge eligibility

#### Requirements Covered

- REQ-014: Use the repository variable AI_AUTOMATION_MERGE_FORK_CONTRIBUTION, default false. True allows already qualified fork PRs to merge through the protected broker; it grants no fork-write authority and bypasses no gate.

#### Context Evidence

Approved request-scoped automation plan and inspected controller, recovery, CI and broker paths.

#### Design Details

Bind source and destination repository identities throughout source export, CI, admission and broker effects. Changes to the original fork branch require contributor action. An explicitly admitted repository-owned replacement can receive bounded repairs under inherited adoption authority, without acquiring fork branch write access. Strict boolean parsing; request capability ceiling cannot increase. New privileged runs re-evaluate restrictions; cancel in-flight jobs for immediate stop.

#### Selected Option

Deterministic request and effect owners with bounded isolated agent assessments.

#### Alternatives Considered

Reject backlog sweeps, direct model effect authority, fork branch writes and compatibility shims.

#### Implementation Boundaries

Automation controller, enrolled workflow/helper contracts and shared protected broker. Preserve unrelated edits.

#### Test-First Success Criteria

- TDD-001: New acceptance fixtures reproduce unsupported admission and authority paths before repair.

#### Validation Plan

Focused pytest, workflow contracts, Ruff, schema checks and changed-scope alignment.

#### Test Plan

Request lifecycle, meaningfulness, fork capability, exact evidence, effect races and recovery fixtures.

#### Evaluation Plan

Hosted provider, adversarial isolation, semantic false-closure and live qualification remain separate.

#### Rollout And Rollback

Keep disabled and fork merging false. Quiesce prior writers, reconcile old effects, retain history and require manual requests for old operations. Refresh admission epoch on enablement.

#### Done Definition

Source implementation and focused checks pass; hosted qualification is reported independently.

#### Implementation Evidence

Canonical repository variable `AI_AUTOMATION_MERGE_FORK_CONTRIBUTION` defaults false across privileged workflows and templates. Candidate CI v2 and review-v2 bind both repositories, head ref, request/head/base/tree. Branch writers reject forks; broker rechecks the active ceiling and current restriction.

#### Verification Evidence

`test_requests.py`, `test_candidate_ci.py`, `test_effect_boundaries.py` and protected merge helper tests cover strict flags, no elevation, revocation, fork-main write denial, source identity drift and v2 status publication. No real GitHub merge/protection or fork-token trial was run.

<!-- /FEATURE: FEAT-014 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-015 reqs=REQ-015 status=ready delivery=implemented priority=P0 version=2 -->
### FEAT-015: Clear PR outcomes and fork origin

#### Requirements Covered

- REQ-015: Provide idempotent actionable comments, source:fork labels and persistent origin notices for admitted PRs. Select explain/open, explain/close or protected merge from completed evidence and request authority.

#### Context Evidence

Approved request-scoped automation plan and inspected controller, recovery, CI and broker paths.

#### Design Details

One updated bot comment retains origin and outcome. Review-only requests never close or merge. Fork process requests may close proven no-benefit or redundant changes even when fork merging is disabled. Revalidate identity and verify effect ownership before closure compensation; never undo a human closure.

#### Selected Option

Deterministic request and effect owners with bounded isolated agent assessments.

#### Alternatives Considered

Reject backlog sweeps, direct model effect authority, fork branch writes and compatibility shims.

#### Implementation Boundaries

Automation controller, enrolled workflow/helper contracts and shared protected broker. Preserve unrelated edits.

#### Test-First Success Criteria

- TDD-001: New acceptance fixtures reproduce unsupported admission and authority paths before repair.

#### Validation Plan

Focused pytest, workflow contracts, Ruff, schema checks and changed-scope alignment.

#### Test Plan

Request lifecycle, meaningfulness, fork capability, exact evidence, effect races and recovery fixtures.

#### Evaluation Plan

Hosted provider, adversarial isolation, semantic false-closure and live qualification remain separate.

#### Rollout And Rollback

Keep disabled and fork merging false. Quiesce prior writers, reconcile old effects, retain history and require manual requests for old operations. Refresh admission epoch on enablement.

#### Done Definition

Source implementation and focused checks pass; hosted qualification is reported independently.

#### Implementation Evidence

`github.py`, `events.py` and `closure.py` provide idempotent fork labels/notices, actionable terminal comments, narrow evidence-backed closure and verified compensation. README, architecture and portable guide describe the ordered flow and UI-only manual request path.

#### Verification Evidence

`test_effect_boundaries.py` verifies idempotent fork origin reporting and that compensation never reopens a human-closed PR. Playwright rendered the supplied HTML at desktop/mobile dimensions without document overflow or clipped node text. Direct local-file/loopback browser navigation was unavailable; this turn does not requalify that transport.

<!-- /FEATURE: FEAT-015 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-016 reqs=REQ-016 status=ready delivery=implemented priority=P0 version=2 -->
### FEAT-016: Hosted managed recovery and two evaluation slots

#### Requirements Covered

- REQ-016: Hosted managed recovery and two evaluation slots.

#### Context Evidence

Reviewed current controller, requests, stage persistence, state, publication,
workflow permissions and the accepted hosted implementation plan.

#### Design Details

One CAS scheduler record holds exactly two evaluator slots. Persist dispatch intent
and nonce before dispatch. A trusted supervisor renews ownership and cancels exact
workers on loss; recovery never reassigns until terminal run evidence. Five-minute
scheduled recovery supplements event-driven and manual recovery, without an SLA.
Workers receive local input/cache and return bounded typed data; only trusted owners
persist authoritative state. Candidate CI and live waits do not occupy evaluator slots.

#### Selected Option

Extend the existing Python, GitHub Actions, Codex, Token Factory and private S3
implementation. Deterministic owners control admission, state and effects;
bounded agents supply assessment and patch proposals only.

#### Alternatives Considered

A separate test repository and non-default-branch merge exceptions add ownership
and security surfaces. Keep production target guards and qualify through isolated
rehearsals followed by legitimate, explicitly selected production canaries.

#### Implementation Boundaries

Automation package, trusted workflows, focused tests and documentation.
Native product credential changes also reconcile the affected product contracts.

#### Test-First Success Criteria

- TDD-016: Reject unauthorized, stale and cross-scope operations before effects.

#### Validation Plan

Focused unit/integration tests, workflow validation, apply-security and align.

#### Test Plan

Three concurrent requests, duplicate dispatch, supervisor loss, uncertain cancellation,
CI waits, stale results and recovery while admission is disabled.

#### Evaluation Plan

Bind every result to its source, candidate, runtime, workflow and operation.
Simulated effects do not prove actual GitHub or cloud behavior.

#### Rollout And Rollback

Ship PR review disabled and release, fork merging and image publishing false.
Maintainers select exact canaries and qualified image digests. Revoke new permits,
cancel and reconcile owned writers; retain cleanup authority and rollback images.

#### Done Definition

Implemented source and independent evidence cover the declared behavior.
Unavailable credentials, hosted deployment or real canaries remain explicit gaps.

#### Implementation Evidence

Recovery rechecks the reservation under both leases before any cleanup driver call.

The hosted workflow now uses execution.py prepare/evaluate/supervise/accept/live/finalize, scheduler.py two-slot CAS admission, and live_slot.py durable live reservations. Trusted ingestion owns durable evidence and final admission.

#### Verification Evidence

Local negative controls now prove recovery refuses a changed or already-clean reservation before any driver call; an unchanged terminal owner still cleans successfully.

Local execution/scheduler tests cover concurrency, duplicate dispatch, uncertain cancellation, patch-budget interruption, admission interruption and invalid oldest queue entries. Actual hosted interruption and cloud recovery remain unrun.

<!-- /FEATURE: FEAT-016 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-017 reqs=REQ-017 status=ready delivery=implemented priority=P0 version=2 -->
### FEAT-017: Qualified public images and configurable Codex updates

#### Requirements Covered

- REQ-017: Qualified public images and configurable Codex updates.

#### Context Evidence

Reviewed current controller, requests, stage persistence, state, publication,
workflow permissions and the accepted hosted implementation plan.

#### Design Details

Resolve the npm package version and integrity once; build Linux amd64 from trusted
inputs. Qualify actual Responses/tool/error loops, publish the same bytes in a
separate packages-write job and independently verify anonymous pulls. Retain a
manifest binding image, source/build inputs, Codex integrity, workflow and results.
AI_AUTOMATION_AGENT_IMAGE_REF remains a manually promoted qualified immutable digest.

#### Selected Option

Extend the existing Python, GitHub Actions, Codex, Token Factory and private S3
implementation. Deterministic owners control admission, state and effects;
bounded agents supply assessment and patch proposals only.

#### Alternatives Considered

A separate test repository and non-default-branch merge exceptions add ownership
and security surfaces. Keep production target guards and qualify through isolated
rehearsals followed by legitimate, explicitly selected production canaries.

#### Implementation Boundaries

Automation package, trusted workflows, focused tests and documentation.
Native product credential changes also reconcile the affected product contracts.

#### Test-First Success Criteria

- TDD-017: Reject unauthorized, stale and cross-scope operations before effects.

#### Validation Plan

Focused unit/integration tests, workflow validation, apply-security and align.

#### Test Plan

Latest changes, exact pins, duplicate checks, failed qualification, anonymous pull,
forged qualification and rollback to the prior qualified digest.

#### Evaluation Plan

Bind every result to its source, candidate, runtime, workflow and operation.
Simulated effects do not prove actual GitHub or cloud behavior.

#### Rollout And Rollback

Ship PR review disabled and release, fork merging and image publishing false.
Maintainers select exact canaries and qualified image digests. Revoke new permits,
cancel and reconcile owned writers; retain cleanup authority and rollback images.

#### Done Definition

Implemented source and independent evidence cover the declared behavior.
Unavailable credentials, hosted deployment or real canaries remain explicit gaps.

#### Implementation Evidence

images.py, image_cli.py, Dockerfile and github-ai-image.yml implement exact npm resolution/integrity, OCI SBOM/provenance checks, real model/tool probes, separate publisher, anonymous verification and retained receipts. Automatic builds stay off with image publication disabled; explicit manual qualification remains available. Promotion stays manual.

#### Verification Evidence

Local image tests cover selection, changed OCI bytes/platform, forged receipts, receipt expiry retention, concurrent selection, hourly no-op reuse and disabled automatic runs. An amd64 OCI image was built and Codex version-smoked on an ARM host; this is not native hosted or actual Token Factory qualification. No registry publication or promotion has occurred.

<!-- /FEATURE: FEAT-017 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-018 reqs=REQ-018 status=ready delivery=implemented priority=P0 version=2 -->
### FEAT-018: In-repository qualification and scoped real canaries

#### Requirements Covered

- REQ-018: In-repository qualification and scoped real canaries.

#### Context Evidence

Reviewed current controller, requests, stage persistence, state, publication,
workflow permissions and the accepted hosted implementation plan.

#### Design Details

Extract one shared assessment sequence and local stage backend. Shadow uses GET-only
GitHub reads, qualification-specific artifacts and no production continuation.
Trusted rollout state contains a generation and exact PR/candidate/effect permits.
Missing state blocks production admission. Global controls remain additional ceilings.
Changed head/base requires renewed canary admission. Release preparation is a real
tag write: admit exact project/source/version first, then bind publication to the
built asset manifest. A maintainer explicitly enables general admission after
qualification. Bootstrap the harness through ordinary maintainer review; use an
eligible product PR, not a protected automation change, for autonomous merge proof.

#### Selected Option

Extend the existing Python, GitHub Actions, Codex, Token Factory and private S3
implementation. Deterministic owners control admission, state and effects;
bounded agents supply assessment and patch proposals only.

#### Alternatives Considered

A separate test repository and non-default-branch merge exceptions add ownership
and security surfaces. Keep production target guards and qualify through isolated
rehearsals followed by legitimate, explicitly selected production canaries.

#### Implementation Boundaries

Automation package, trusted workflows, focused tests and documentation.
Native product credential changes also reconcile the affected product contracts.

#### Test-First Success Criteria

- TDD-018: Reject unauthorized, stale and cross-scope operations before effects.

#### Validation Plan

Focused unit/integration tests, workflow validation, apply-security and align.

#### Test Plan

No-write shadow, non-canary rejection, stale permit callbacks, candidate drift,
monorepo dependencies, exact real merge and release postconditions.

#### Evaluation Plan

Bind every result to its source, candidate, runtime, workflow and operation.
Simulated effects do not prove actual GitHub or cloud behavior.

#### Rollout And Rollback

Ship PR review disabled and release, fork merging and image publishing false.
Maintainers select exact canaries and qualified image digests. Revoke new permits,
cancel and reconcile owned writers; retain cleanup authority and rollback images.

#### Done Definition

Implemented source and independent evidence cover the declared behavior.
Unavailable credentials, hosted deployment or real canaries remain explicit gaps.

#### Implementation Evidence

The shared GitHub permission adapter preserves built-in Maintain separately from legacy Write.

rollout.py provides exact generation/expiry/capability permits; qualification.py uses shared assessment through a GET-only GitHub adapter and separate state-only image guard. Production ingestion rejects shadow evidence. docs/hosted-rollout.md documents same-repository qualification and real canaries.

#### Verification Evidence

Realistic GitHub permission responses test Maintain/Admin admission and rejection of ordinary Write, custom Write, Read and None for rollout control.

Local rollout/shadow tests cover denied writes, disabled ceilings, generation/candidate drift and artifact identity. No real PR approval/merge or legitimate release canary has been executed. General activation remains disabled.

<!-- /FEATURE: FEAT-018 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-019 reqs=REQ-019 status=ready delivery=unassessed priority=P0 version=2 -->
### FEAT-019: Scoped native Nebius live qualification

#### Requirements Covered

- REQ-019: Scoped native Nebius live qualification.

#### Context Evidence

Reviewed current controller, requests, stage persistence, state, publication,
workflow permissions and the accepted hosted implementation plan.

#### Design Details

Keep state in private regular versioned S3 buckets, separate production and
qualification credentials, and one isolated backend per cloud trial. Candidate
executors receive provider-expiring bearer tokens without signing/refresh authority.
Trial backend keys have independent expiration or trusted revocation. Freeze
scenario, source and adapter; independent observers verify deployed identity and
traffic. Use runner-/32 SSH and authenticated host keys. The serialized replacement
envelope is five VMs, 16 vCPU, 64 GiB RAM, 380 GiB SSD and five public IPs, including
residual resources. Verify effective quota and isolation; request only verified
shortfalls through the authorized human account. No automatic merge for uncovered paths.

#### Selected Option

Extend the existing Python, GitHub Actions, Codex, Token Factory and private S3
implementation. Deterministic owners control admission, state and effects;
bounded agents supply assessment and patch proposals only.

#### Alternatives Considered

A separate test repository and non-default-branch merge exceptions add ownership
and security surfaces. Keep production target guards and qualify through isolated
rehearsals followed by legitimate, explicitly selected production canaries.

#### Implementation Boundaries

Automation package, trusted workflows, focused tests and documentation.
Native product credential changes also reconcile the affected product contracts.

#### Test-First Success Criteria

- TDD-019: Reject unauthorized, stale and cross-scope operations before effects.

#### Validation Plan

Focused unit/integration tests, workflow validation, apply-security and align.

#### Test Plan

Native apply/destroy, ordinary VPN Static/BGP, HA fencing/failover/replacement,
negative controls, interrupted cleanup, forbidden IAM and residual resource limits.

#### Evaluation Plan

Bind every result to its source, candidate, runtime, workflow and operation.
Simulated effects do not prove actual GitHub or cloud behavior.

#### Rollout And Rollback

Ship PR review disabled and release, fork merging and image publishing false.
Maintainers select exact canaries and qualified image digests. Revoke new permits,
cancel and reconcile owned writers; retain cleanup authority and rollback images.

#### Done Definition

Implemented source and independent evidence cover the declared behavior.
Unavailable credentials, hosted deployment or real canaries remain explicit gaps.

#### Implementation Evidence

The native cxcli guard reads the real bindings field and rejects FIFOs before blocking on a read.

The distributed live reservation and trusted driver boundary are implemented. state_qualification.py and its manual workflow provide isolated S3 CAS/fencing/version-history trials, a same-run production-sentinel read guard, exact cleanup ownership and bounded clients. Separate private versioned production/qualification buckets, bucket-scoped service accounts and five default-branch-only GitHub environments have been provisioned; activation controls remain off. cxcli preconfigured-static CPU VM authentication has been added with local tests. Concrete reviewed cloud drivers and focused validation profiles remain absent. VPNGW BGP/HA external credential enrollment, scoped trial provisioning and independent product observers remain incomplete; no capability is marked qualified.

#### Verification Evidence

Local auth regressions reproduce and reject native component bindings and non-regular FIFO credential sources; the latter has a bounded subprocess proof. These fixes do not establish provider or VM qualification.

Local cxcli authentication/backend/render tests pass, including managed-key, GPU-cluster and alternate-identity rejection. A real S3 trial from the local execution host passed conditional writes, contention, stale-owner fencing, injected accepted-response loss, known-sentinel and anonymous read denial, version history and exact version cleanup. Storage quotas and private bucket policy/versioning were independently read back. Earlier failures and incomplete proof remain recorded; the provider HTTP412 error-name defect and harness ownership/probe defects were repaired before the final trial. Hosted S3 execution, credential expiry, cross-bucket write/delete denial, native VM trials and VM cleanup proof remain unrun. The disposable VM target must be explicitly authorized independently from storage.

<!-- /FEATURE: FEAT-019 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-020 reqs=REQ-020 status=ready delivery=implemented priority=P0 version=2 -->
### FEAT-020: Explicit historical release baseline adoption

#### Requirements Covered

- REQ-020: Explicit historical release baseline adoption.

#### Context Evidence

Reviewed current controller, requests, stage persistence, state, publication,
workflow permissions and the accepted hosted implementation plan.

#### Design Details

Add release-baseline-inspect and release-baseline-adopt in a trusted manual
workflow with GitHub read-only access and protected state credentials. Keep
release publishing disabled during adoption. Preserve original evidence privately;
validate current asset bytes and explicitly distinguish adoption from historical
producer attestation. CAS initialize the canonical completed baseline under the
existing per-project release lock, without fabricating PR completion receipts.

#### Selected Option

Extend the existing Python, GitHub Actions, Codex, Token Factory and private S3
implementation. Deterministic owners control admission, state and effects;
bounded agents supply assessment and patch proposals only.

#### Alternatives Considered

A separate test repository and non-default-branch merge exceptions add ownership
and security surfaces. Keep production target guards and qualify through isolated
rehearsals followed by legitimate, explicitly selected production canaries.

#### Implementation Boundaries

Automation package, trusted workflows, focused tests and documentation.
Native product credential changes also reconcile the affected product contracts.

#### Test-First Success Criteria

- TDD-020: Reject unauthorized, stale and cross-scope operations before effects.

#### Validation Plan

Focused unit/integration tests, workflow validation, apply-security and align.

#### Test Plan

Idempotent adoption, interruption, drift, changed asset IDs, invalid wheel identity,
concurrent publisher and first subsequent legitimate release.

#### Evaluation Plan

Bind every result to its source, candidate, runtime, workflow and operation.
Simulated effects do not prove actual GitHub or cloud behavior.

#### Rollout And Rollback

Ship PR review disabled and release, fork merging and image publishing false.
Maintainers select exact canaries and qualified image digests. Revoke new permits,
cancel and reconcile owned writers; retain cleanup authority and rollback images.

#### Done Definition

Implemented source and independent evidence cover the declared behavior.
Unavailable credentials, hosted deployment or real canaries remain explicit gaps.

#### Implementation Evidence

Baseline adoption consumes the corrected shared Maintain-role mapping without expanding ordinary Write authority.

baseline.py and github-ai-baseline.yml provide read-only inspection and human exact-digest/revision adoption under the release owner. Original producer evidence and independently checked current asset identities/hashes are retained as maintainer_adoption. publication.py uses the canonical release verifier.

#### Verification Evidence

The actual GitHub adapter is exercised with Maintain/Admin and denied Write/custom-role API responses before baseline state writes.

Local baseline tests verify original evidence, replaced asset rejection and exact review/CAS. A read-only inspection of existing cxcli 0.1.9 verified its original producer and two current assets. No baseline was adopted, tag changed, asset replaced or new release published.

<!-- /FEATURE: FEAT-020 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-021 reqs=REQ-021 status=ready delivery=implemented priority=P0 version=4 -->
### FEAT-021: Unified PR lifecycle and verified issue completion

#### Requirements Covered

- REQ-021: Unified PR lifecycle and verified issue completion.

#### Context Evidence

Existing usefulness, isolated review/repair, candidate CI, live lifecycle and
protected merge owners are present. Dispositions, adoption, live remediation
and hosted linked-issue reconciliation need explicit connected contracts.

#### Design Details

Keep deterministic admission, budgets, publication and completion owners. Agents
return typed assessments and patches without credentials. Reuse the state store,
effect journal, trusted publisher and broker; add no service or model dependency.

A shared status publisher reconciles the named lifecycle label family and concise
comments. Terminal completion records carry only frozen reporting and issue
obligations, preserve successful merge truth and cannot resume candidate execution.

Live applicability and sanitized failure evidence bind the candidate and scenario.
Diagnose before repair; design proven contract-changing remedies; preserve failed
trials and quiesce/clean before fresh testing. A waiver never overrides a known
failure or contamination. Unavailable testing can yield manual-merge only when
review/CI have no known correctness blockers.

Large conflicts use one attributed replacement PR with an independently reviewed
full-diff feature ledger, separate feature commits and one verified complete
publication. Each adopted feature maps to independently reviewed live assertions. Preserve original
origin/authority and one budget owner. Replacement creation cannot automatically
admit another request. Transfer remaining execution once; explicit manual renewal
reuses the same replacement and journals both records. Preserve current fork and
report-only restrictions at every effect. Finish only after full merged-result coverage; material
source drift or uncovered features keeps the original open.

Issue assessment owns complete identities, scope/discussion and state-event
watermarks, criterion evidence and closure authority. Native closure is preferred;
explicit fallback requires fresh resolution proof and verified rationale. Later
human reopening or objection stops closure. Cross-repository access is never
assumed and the merge broker receives no extra issue permissions.

#### Selected Option

Extend existing owners with typed bounded handoffs and one status vocabulary.

#### Alternatives Considered

Prose-only alignment leaves hosted behavior inconsistent. Closing every linked
issue loses unresolved work. Unbounded adaptation and additional credential
providers expand authority and cost without need.

#### Implementation Boundaries

PR/issue/live skill contracts, hosted stages and effects, shared resolution
helpers, trusted bundle and workflow wiring, tests and operational guidance.

#### Test-First Success Criteria

- TDD-001: No label, replacement or waiver can broaden admitted authority.
- TDD-002: Repeated and interrupted effects converge without duplicate work.
- TDD-003: Unresolved defects, omitted features and human reopening block closure.

#### Validation Plan

Run focused pytest and issue handoff tests, then affected full local suites,
Ruff, workflow contracts, canonical validation, explicit align and install parity.

#### Test Plan

Cover the complete status matrix, sanitized failure transport, repair/retest,
adoption inheritance and coverage, terminal reconciliation, issue drift and races.

#### Evaluation Plan

Local fixtures establish source behavior only. Qualify exact hosted source,
policy, skills and runtime in controlled trials before enabling new effects.

#### Rollout And Rollback

Keep disabled defaults. Quiesce old writers and reconcile pending effects before
coordinated schema cutover; preserve historical evidence and require fresh
admission when incompatible. Do not add compatibility shims or reset active budgets.

#### Done Definition

Scoped implementation, regression tests and alignment pass; installed and live
qualification are reported separately, with remaining access limitations explicit.

#### Implementation Evidence

Controller contracts/stages now bind applicability, causal diagnosis, feature
inventory/live coverage and independent issue decisions. Adoption publishes one
replacement with verified feature/aggregate trees, inherited ceilings and budgets,
and explicit manual renewal. Completion uses the canonical lock and heartbeat,
typed authority, current-policy guards, actual merged-tree evidence and separate
issue comment/closure journals. Later human reopening and objections block old
effects. Evaluation wire v2 has no compatibility adapter; the continuation owns
completion writes and the merge broker gains issue-read access only.
Completion effects intersect retained/current capabilities with current mode and
exact rollout permits, including expiry. Known non-submissions remain retryable;
lost successful issue-close responses settle through observation without another
close request. Execution expiry alone does not revive or erase these obligations.

#### Verification Evidence

Local full controller suite: 247 tests passed, including loss/replay, status,
issue scope/reopening/merged-tree, fork adoption, manual renewal, failed-live waiver,
failure retention before finalization, inconsistent-pass rejection,
feature live coverage, current-policy downgrade and lease-loss regressions.
Alignment also covers expired/restricted canary permits, removed request
capabilities, policy changes before submission, permission restoration, lost
issue-close response reconciliation and completion after execution expiry.
Existing issue handoff: 20 tests passed. Protected merge owner: 73 tests passed.
Ruff and scoped Markdown pass. Continuation/protected-merge Actionlint passes;
local Actionlint does not recognize pre-existing concurrency.queue in the main
automation workflow (the unchanged baseline reproduces the same two errors).
The canonical publisher validates both pairs; the standalone tracked-file check
for automation reports SPEC_REQUIRED because that pre-existing project is
untracked. The skills tracked-file validation passes.
Five skill structures pass with existing host-extension/test-directory warnings.
Eight disposable Codex/Claude installs preserve source skill/lifecycle bytes.
Playwright local guide check found no page overflow or broken internal anchors.
These are source/disposable-install proofs, not hosted, native-trigger or Nebius
live qualification. No remote PR/issue/cloud changes or actual user-skill
installation occurred. Disabled deployment defaults are preserved.

<!-- /FEATURE: FEAT-021 -->

<!-- maintain-project-specs:design:end -->
<!-- markdownlint-enable MD001 MD024 -->
