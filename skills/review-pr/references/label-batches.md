# Label-filtered PR batches

Read for `--label` without `--pr` before repository discovery. With `--pr`, use
[single-PR resolution](command-reference.md#single-pr-resolution) instead;
valid label/batch options are ignored. This is bounded selection for
the existing review workflow, not another lifecycle engine or grant of authority.

## Validate and select

- Accept one nonempty literal label and optional positive decimal `--pr-batch`,
  default five. Reject repeated flags, missing values, unknown flags, a batch
  without a label, and a label combined with a positional PR number/URL/branch.
  A label is not CSV, a glob or search syntax; preserve its literal spelling.
- Resolve the current Git root and unambiguous GitHub repository using trusted
  metadata. Do not restrict to the working subdirectory or silently select an
  upstream/fork. A batch selector overrides incidental earlier PR context.
- Verify the label exists; distinguish missing label, inaccessible repository
  and a valid label with no matches. Do not create a missing selection label.
- Prefer a capable GitHub connector; when insufficient, disclose use of the
  existing authenticated GitHub CLI API fallback. Do not install tools or
  repair authentication. Prefer a repository-scoped connection with structured literal label filters,
  creation-time ascending order and pagination. Include drafts. Active/custom
  labels select open PRs; `merged` and `closed` select all states for audit.
  Never use semantic search, substring matches or interpolate labels into shell
  or search expressions. If the transport cannot preserve exact filtering,
  ordering and complete bounded pagination, report the capability gap.
- Deduplicate full host/repository/PR/API identities, break creation-time ties
  by number, and freeze at most N matches before any effect. A cutoff tie must
  be resolved before selection. Stop pagination at N or proven exhaustion; do
  not fetch every matching PR or claim the remaining backlog is fully counted.
  Incomplete discovery leaves the batch pending rather than using a partial set.

## Process the frozen batch

Before any per-PR delegation or enrolled dispatch, collect all direct closing
links across the frozen PR set and establish the shared issue registry described
below. Every shared issue must be associated with all selected referring PRs
before one can produce dependent effects. Missing supporting discovery leaves
those effects pending; never merge first and discover linked scope afterward.

Run existing per-PR owners in selection order. Preserve private checkout
isolation, full-diff review, enrolled controller routing, SDLC/read-only limits
and each original request's consumed budget. Route terminal-label selections
to audit-only completion first, even with combined merge wording. For other
selections, a root combined merge request
delegates once per selected PR to merge-pr; its nested review never reselects
the batch or recursively delegates. Labels and batch flags authorize no merge,
rejection, adoption publication, new live testing or PR closure.

Terminal audits never dispatch enrolled `process`; observe read-only or use an
existing authorized completion-only capability. If none supports the requested
effect, report it pending without invoking a broader path or local fallback.
Keep the shared issue context in the batch coordinator, pass only supported
context to per-PR owners, and reconcile their observed results before the next
dispatch. Do not invent API fields or send unsupported context to a hosted
schema. If an owner cannot preserve required shared-issue coordination, leave
dependent processing pending. No new persistent registry or runtime is added.

Before each item, refresh its identity, labels, head/base and discussion. If it
lost the selection label, changed identity or crossed the selected state scope,
report it skipped/pending; do not refill. Changes caused by this invocation do
not remove an item already being processed. Reassess material source/discussion
drift before further effects. Independent items can continue after a blocker;
global restrictions, cancellation and unavailable shared access still apply.

Finish the selected batch without a new overall time cap. Existing per-item
deadlines/attempt limits remain binding and never reset because of batching.
No automatic next batch, hidden cursor, scheduler or guaranteed crash resume.
Another invocation selects afresh and may select still-open items again; reuse
equivalent decisions instead of duplicating comments.

## Label-dependent behavior

Use [shared lifecycle routing](pr-lifecycle.md#selection-and-live-label-routing)
for terminal audits, `being-fixed` coverage assessment and ordinary reviews.
Batch selection labels retain their meanings; they never grant authority.

## Linked issues and report

Collect directly closing-linked issues across the selected PRs, deduplicating by
full identity. Load the actual sibling issue skill as a read-only adviser using
the shared lifecycle contract. Keep a single coordinated assessment per issue
with all selected-PR evidence; later source/scope changes invalidate dependent
findings. Do not run standalone issue triage, recurse into a backlog or save new
acceptance plans. Read related evidence without expanding mutation scope.

The PR cap does not cap directly linked issues. Report their count separately.
The outer lifecycle owner applies only already-authorized effects after fresh
criterion evidence, discussion and state checks. No PR closure proves issue
resolution. Never close a shared issue merely because one selected PR passed.
Explicit PR-closure requests include the shared
[linked-issue continuation](pr-lifecycle.md#completion-after-unmerged-pr-closure)
unless issue changes are excluded. Complete each independently resolved issue
once; unresolved issues retain a concrete criterion gap. Labels and batch caps
never supply this authority, and terminal audits cannot broaden processing.

Report selector, limit, frozen identities, discovery coverage, per-PR outcomes
and one per-issue assessment with separate label/comment/closure results. Name
skipped, pending and unreviewed items; do not claim all repository PRs reviewed.
Enrolled dispatch without observed completion remains pending, not completed.

## Vendor basis

Checked 2026-11-16: [GitHub repository connections](https://docs.github.com/en/graphql/reference/repos)
and [closing links](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue).
Discover the actual connector schema; documented API capability does not prove
the selected connector exposes it.
