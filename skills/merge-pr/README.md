# Merge PR

Eligible fork PRs use the same protected broker, with independently bound
numeric base/head repository identities. Parent-owned review-pr assesses
contribution safety/compatibility, repairs defects and reconciles each project's
canonical specs before final attestation. Review/intent v2 requires those
assessments; old evidence fails closed. Follow the coordinated cutover in the
[Actions protocol](references/actions-protocol.md) before live activation.
Independently verified terminal completion receipts keep finished operations
settled when older intent artifacts remain after the cutover. Unfinished or
unverified operations still fail closed.

`merge-pr` verifies and merges a GitHub pull request outside the Agentic SDLC
workflow. It is the general-purpose merge primitive used by release publishing
skills after `create-pr` opens or reuses a release-prep PR. It reports merge,
destination verification, and post-merge CI as separate outcomes.

## Usage

```text
$merge-pr [<PR-number|PR-URL|current-branch>] [--merge-method squash|merge|rebase] [--delete-branch]
$merge-pr --help
```

Invoke only with explicit merge intent. The default method is squash for
ordinary branches; keep the head branch unless deletion is explicitly requested.
`-h` also shows help. No additional public flags.

## What It Does

- Resolves a PR from a number, URL, or current branch.
- Waits for PR checks when they are pending.
- Verifies review state, draft state, mergeability, and head SHA.
- Invokes parent-owned review-pr for every unmerged PR, refreshing GitHub state
  even with passing evidence. Same-operation exact-head/base analysis may be
  reused; the review child never re-delegates merge. Requires safe repairs before dispatching a
  trusted default-branch GitHub Actions workflow. The async API guards the exact
  reviewed head. Required merge queues stop for a maintainer.
- Refuses admin bypasses and branch-protection overrides.
- Records GitHub's actual resulting commit and verifies its ancestry in the
  remote target branch, allowing the target to have advanced.
- Uploads intent before effects and recovers exact-result CI/Pages after merge.
- Observes applicable CI on the exact resulting commit for up to 24 hours.
- Observes externally queued PRs read-only within the merge deadline; it never
  enqueues or treats a pending queue entry as a completed merge.

## Architecture

```text
Pull request
  |
  v
Readiness checks
  |
  v
Head SHA guarded merge
  |
  v
Authoritative outcome
  |                  |
  v                  v
External queue       Merged result SHA
  |                  |
  v                  v
Read-only wait       Remote destination ancestry
                     |
                     v
                     Result-commit CI (up to 24 hours)
```

## Core Concepts

- The user or calling skill must have explicit merge intent.
- `squash` is the default merge method for ordinary protected branches.
- GITHUB_TOKEN cannot enqueue merge queues; there is no credential fallback.
- Explicit combined create/review-and-merge and complete publish-* requests
  delegate merge authority. Plain create-pr and review-pr do not.
- Already-merged PRs use observation and exact-result verification only.
- The operator starts local review for ordinary and Dependabot PRs, including
  major updates; no unattended agent initiates work.
- Actions approves after local exact-head/base review. Agents run locally;
  Actions runs deterministic scripts. See [setup/protocol](references/actions-protocol.md).
- Required reviews, branch protection, and environment rules are blockers, not
  conditions to bypass.
- This skill does not write Agentic SDLC state; `sdlc-merge-pr` owns that path.
- The guarded PR head and resulting merge SHA can differ, especially for squash
  and rebase. Verification uses GitHub's GraphQL `mergeCommit.oid`, bound to the
  merged PR identity. REST `2026-03-10` has no result SHA field; missing result
  evidence stops completion without dispatching CI or Pages.
- Completion candidate failures display the helper's sanitized reason and retain
  its nonzero exit status without publishing targets. An already verified merge
  remains distinct from a later recovery scan failure.
- Ordinary readiness, merge settlement and result verification each use a fixed
  86,400-second (24-hour) deadline and a 30-second polling interval. An explicit
  user continuation can start a new observation attempt with the same transaction.
  Caller-supplied checkpoint deadlines remain authoritative, even when shorter;
  delayed checks, repairs and observed reruns do not reset deadlines. Result settlement is
  bounded to 60 seconds. Local verification is read-only; a separate trusted
  completion workflow dispatches exact-result CI and configured Pages builds.
- CI reports passed, failed, pending, not configured, or unverified. Neutral and
  skipped checks are disclosed; accepted check policy is not proof tests ran.
  Unknown configuration or access errors cannot become "not configured."
- `merged; destination verified; CI failed` preserves both outcomes. Queued
  requests have no post-merge CI result and defer requested branch deletion.
- Verification requires readable PR, reference, check/status, Actions, and
  applicable provider/configuration evidence. Missing visibility is reported;
  the skill does not expand permissions or replace credentials.

See [completion verification](references/completion-verification.md) for API
recipes, queue races, conclusion rules, cleanup conditions, and official sources.

## Evaluation

The canonical trigger CSV retains invocation-selection coverage. Quality cases
replay synthetic local API observations, including rewritten commit identities,
destination advancement, queue removal, and CI failures, without contacting
GitHub. Fixture timestamps simulate deadlines without real waits. Compare with
the pre-edit skill using the catalog's isolated quality runner when authenticated.
Static definitions, copied installation, native triggering, and output quality
are separate evidence lanes; none proves a live merge occurred.

## Files

- `SKILL.md`: Merge workflow, guardrails, and output contract.
- `agents/openai.yaml`: UI metadata and default prompt.
- `references/completion-verification.md`: Required completion evidence rules.
- `references/actions-protocol.md`: Authority, installation and recovery contract.
- `scripts/merge_gate.py`: CI aggregate, review admission and protected merge.
- `scripts/merge_completion.py`: Result CI, Pages and durable reconciliation.
- `evals/trigger-prompts.csv`: Invocation selection cases.
- `evals/evals.json` and `evals/fixtures/`: Completion behavior cases and inputs.

## Reviewed dependency updates

Dependabot updates, including Docker, require complete local review, meaningful
validation and fresh evidence after every head/base change. Safe source repairs
use create-pr. Bot metadata and green CI alone cannot authorize merging.
Only trusted explicit dispatch initiates a merge; scheduled events observe CI
and recover already-authorized completion.

### Local checkout completion

The canonical commit transaction owns safe target checkout and outer completion.
See [the completion contract](../create-pr/references/checkout-completion.md) for
review switch-back, create-only behavior, verified-result fast-forwards, resumed
work and publication-preserving policy. Merge, CI/Pages, local refs and deletion
are reported independently. No public skill flags are added.

## Targeted events and hourly recovery

Protected merge has no schedule. PR events and trusted PR CI `in_progress` and
`completed` events update only affected current heads. Starts must remain wired
so a same-SHA rerun cannot leave stale green Required CI. Push and result-CI
runs do not enter that aggregate path.

Merge completion has the sole hourly recovery schedule (`17 * * * *`) and manual
recovery. A separate default-branch-only status-write job repairs Required CI
for all open default-base PRs. Completion event discovery reads the producer
run's artifacts; ordinary CI and CI-only broker runs are filtered before runner
allocation. GitHub may still display skipped workflow-run rows.

Validated result inputs produce an immutable `merge-result-route/v1` receipt
before tests. It binds repository, workflow/run/attempt, broker/PR/result and
correlation; a failed CI run can route recovery but cannot prove successful CI.
Latest-attempt exact-result success evidence remains mandatory. Recovery retains
bounded journal scans for uncertain effects. Target-specific malformed receipts
produce blocked matrix entries and failing run status while valid independent
targets proceed. Global API, authentication and incomplete-listing errors stop
all candidate effects and preserve sanitized diagnostics.
