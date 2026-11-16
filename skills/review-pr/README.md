# Review PR

[The shared PR lifecycle](references/pr-lifecycle.md) aligns contribution assessment,
status labels, failed-test repair, one replacement PR and independent issue
completion. Plain review and report-only limits remain binding; issue closure
requires its own verified resolution and authority.

An explicit “review and close only PR #N” includes completion of that PR's
independently resolved directly closing-linked issues. “Only” limits PR
selection; “leave issues unchanged” explicitly excludes issue writes. When
merged replacement work resolves an issue on current default, the outer owner
verifies its criteria, posts and reads back the resolving evidence, closes it
as completed and verifies state/reason before returning. Unresolved issues stay
open with concrete gaps. Plain review and audit-only requests remain unchanged.
See [closure continuation](references/pr-lifecycle.md#completion-after-unmerged-pr-closure)
for interruption, human reopening, partial access and enrolled-owner boundaries.

Ordinary review first assesses the contribution against accepted behavior,
safety and supported existing-user workflows. After proven safe repairs, the
root invokes maintain-project-specs for every affected project, then reviews the
complete final diff again. No docs changes in the diff alone are not a finding; changed docs
alone are not proof of alignment. See [contribution assessment](references/contribution-assessment.md).
Normal invocation covers scoped non-destructive fork repairs when GitHub permits;
report-only and active SDLC stay read-only. Standalone review never merges.

`review-pr` reviews GitHub pull requests and safely moves them closer to
merge-readiness when permissions allow.

## Usage

```text
$review-pr
$review-pr 42
$review-pr --pr 42
$review-pr --pr 42 --label being-fixed --pr-batch 10
$review-pr --label being-fixed --pr-batch 10
$review-pr --label being-fixed
$review-pr --help
```

`--pr` requires a PR number, URL or branch and reviews exactly that PR. It
ignores valid `--label` and `--pr-batch` options with one concise notice; no
label-existence lookup or batch discovery occurs. The PR's actual labels and
state guide review, and existing authorized status-label updates remain enabled.
Bare `--pr`, malformed options, repeated flags, or mixing `--pr` with a
positional target fails before discovery. Ambiguous targets require an exact
PR number or URL; no current-branch or batch fallback occurs.

Without `--pr`, `--label` selects one exact existing label across the current repository;
`--pr-batch` is a positive total limit, default five. It requires label mode
and cannot combine with a positional PR selector. No flags preserves the
current-branch PR review. `-h, --help` explains every option without effects.

[Label batches](references/label-batches.md) freeze oldest-created matches before
effects and never refill or start another batch. Active/custom labels select
open PRs including drafts; `merged` and `closed` audit all states without new
repair or merge. `being-fixed` requires full verified merged replacement coverage
before authorized closure, with attribution to the original contributor.
Existing per-item budgets remain; there is no new batch-wide time limit.

Directly closing-linked issues are assessed once per full identity, independently
of PR outcomes and the PR count limit. The issue skill advises read-only; the
outer owner verifies each authorized label, comment and closure separately.
Discover shared issue relationships before the first per-PR handoff and reconcile
each owner's results in the batch context. Terminal audits also bypass enrolled
`process` dispatch; unsupported narrow completion effects remain pending.
Selection labels never grant closure or merge authority.

For `--pr`, actual terminal state or live terminal labels take audit precedence,
then live `being-fixed` triggers coverage assessment; otherwise ordinary review
applies. An ignored filter does not set a label or change this route. See
[shared label handling](references/pr-lifecycle.md#selection-and-live-label-routing).

## What It Does

- Reviews PRs by number, URL, or current branch.
- Inspects base branch, checks, review state, conflicts, and open concerns.
- Fixes safe issues on writable branches.
- Resolves straightforward conflicts when safe.
- For Agentic SDLC PRs, checks requirements, design, validation, tests,
  evaluation, UAT, and commit evidence when available.
- For active Agentic SDLC PRs, uses findings-and-readiness-only mode and never
  mutates the exact promoted PR head.
- When Agentic SDLC local state is available, records readiness and blocker
  summaries in run evidence.
- Reports whether the PR is ready to merge and what blockers remain.

## Architecture

```text
PR target
  |
  v
Fetch GitHub and git state
  |
  v
Review checks, diffs, conflicts, and comments
  |
  +--> apply safe fixes when writable
  `--> report blockers when not writable or unsafe
```

## Workflow

1. Resolve the PR target and base branch.
2. Inspect working tree state and branch ownership.
3. Review checks, comments, diff, and conflicts.
4. For Agentic SDLC PRs, inspect local SDLC specs and evidence summaries.
5. Apply relevant sibling skills for concrete file types.
6. Fix safe issues and rerun focused validation in generic mode. In active
   Agentic SDLC mode, route every required branch change through the
   coordinator instead.
7. For an authorized closure, verify PR closure and complete eligible linked
   issues under the shared lifecycle; report each issue's evidence or exact gap.
8. Report readiness or closure, remaining blockers, and separate issue outcomes.

## Core Concepts

- Findings lead; summaries come after issues.
- Do not rewrite externally owned branches without permission.
- Preserve reviewer concerns as explicit blockers until resolved.
- Prefer non-destructive updates for shared branches.
- Preserve the exact promoted SHA in active Agentic SDLC review mode; branch
  fixes, conflict resolution, and check repairs must re-enter the SDLC gates.

## Files

- `SKILL.md`: PR review and repair workflow.
- `agents/openai.yaml`: UI metadata.
- `references/command-reference.md`: exact GitHub CLI and Git command cookbook
  loaded when inspecting, syncing, or updating a PR branch.

Ordinary review runs locally and delegates safe repair commits, base sync and
pushes to create-pr's private preparation handoff. It revalidates/re-reviews the
final head/base and returns an authenticated COMMENT attestation. Plain review
stops there. An explicit root review-and-merge request delegates once to merge-pr;
its parent-owned review-pr call returns evidence without recursive delegation.
Unsafe findings or unresolved human objections stop. Agentic SDLC remains read-only.

## Fork repair permissions

Review uses a shared read-only preflight before allocating repairs or reporting
a permission blocker. Direct fork write access or upstream write access plus
maintainer edits can permit updates to the original PR branch. Missing evidence
is unknown, not denied; metadata eligibility does not prove a successful push.
Needed repairs with denied/unknown access receive an accurate `blocked`
disposition when status writes are authorized. A PR needing no branch changes
continues review without branch-write access. See [branch access](references/branch-access.md).

## Reviewed dependency updates

Dependabot updates, including Docker, require complete local review, meaningful
validation and fresh evidence after every head/base change. Safe source repairs
use create-pr. Bot metadata and green CI alone cannot authorize merging.
Plain review records readiness and never initiates merging. Every PR review
uses nested read-only code-review for complete frozen diff analysis, supplemented
by matching specialists. Same-operation exact-head/base analysis may be reused,
but GitHub state and objections are always refreshed.

### Private review execution

Executable Git hooks, non-LFS filters, fsmonitor commands and external diff/merge
drivers remain blocked. Unused global LFS configuration is safe to retain:
private Git commands disable its executables. Actual LFS-managed files stop with
a clear unsupported-content diagnostic; full LFS support is deferred.

Every standalone or nested review leaves the caller branch, index, files and
local refs untouched. Metadata-only review needs no checkout. Checkout-dependent
review and safe repairs run in an independent owner-bound private repository.
Repairs may advance the existing remote PR; your local branch stays unchanged
until you update it yourself. Concurrent edits remain your work.

The [private execution contract](../create-pr/references/private-review.md) defines
allocation, original authority, recovery and safe cleanup. Candidate tests still
need restricted execution separate from trusted controls. Private execution helpers add no public flags.

## Enrolled repository automation

Shared Actions authority: enrolled repositories route laptop and hosted requests through one merge/release controller; PR lifetime waivers are durable policy, not labels. See the skill contract and sibling merge-pr automation-authority reference.

## Delivery waiting and optional UAT

Ordinary merge returns after confirmed merge and destination containment.
Use `merge-pr --wait-for-result` to wait for full result CI and Pages. Release
publication instead verifies its exact-commit service/shared publisher profile.
Additional live/cloud/UAT campaigns are opt-in; required CI, review, recorded
failures and cleanup remain binding. SDLC retains its accepted delivery boundary.
