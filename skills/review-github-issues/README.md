# Review GitHub Issues

[The shared PR lifecycle](../review-pr/references/pr-lifecycle.md) aligns contribution assessment,
status labels, failed-test repair, one replacement PR and independent issue
completion. Plain review and report-only limits remain binding; issue closure
requires its own verified resolution and authority. In PR-caller mode this
skill remains a read-only adviser, including when the selected PR closes
without merging and another merged PR already resolved the issue on current
default. The outer PR owner applies the authorized completion and verifies each
effect; this adviser creates no redundant triage plan or GitHub writes.

Review GitHub issues by project and shared remediation, apply evidence-backed
triage decisions, and save one implementation plan per accepted batch. The
skill ends triage with an operator approval handoff; implementation is separate.

## Usage

```text
$review-github-issues
$review-github-issues --project vpngw
$review-github-issues --project services/vpngw --issues 123,124
$review-github-issues --project .
$review-github-issues --priority services/vpngw,services/nebius-cxcli
$review-github-issues --issues 123,124
$review-github-issues --repo example-org/alpha,example-org/beta
$review-github-issues --repo example-org/alpha,example-org/beta --issues 12,34
$review-github-issues --repo https://github.com/example-org/alpha --issues 12
$review-github-issues --label being-fixed --issue-batch 10
$review-github-issues --label being-fixed
$review-github-issues --help
```

Use `/review-github-issues` in Claude Code or
`/skills:review-github-issues` through the Claude plugin. Invocation is
explicit-only. This is an agent skill, not a separately installed executable.

| Option | Meaning |
| --- | --- |
| No flags | Review open issues related to the current folder's project; descendants inherit their project root. |
| `--repo` | Comma-separated repository names or URLs; finish repositories in supplied order. |
| `--issues` | Comma-separated positive numbers; check each in every selected repo, intersected with current/explicit project scope. |
| `--project` | Override scope with an exact project root or unique name; `.` means whole repository. |
| `--label` | One exact existing label; select oldest-created open issues matching project scope. |
| `--issue-batch` | Total matching issues after project filtering, default five in label mode; requires `--label` and excludes `--issues`. |
| `--priority` | Comma-separated exact project roots, highest first, applied independently in each repo. |
| `-h`, `--help` | Explain the interface and effects without tools or side effects. |

From `services/vpngw`, bare `$review-github-issues` selects vpngw-owned and
proven-affected issues. Running from its `src` subfolder uses the same project.
At the repository root, or with `--project .`, scope is the whole repository.
An explicit project overrides the folder in every selector mode. Ambiguous
project names require a choice; unknown paths never silently widen selection.
With `--repo` alone, the inferred project-relative root must exist in each target.
Without `--issues` or `--label`, enumerate open candidates completely, then filter
by that scope. A shared-component issue remains owned by its actual project.
Unknown repository identity requires clarification. Empty backlogs end without
effects. Repeated inputs are deduplicated; positional issue numbers/URLs and
unknown flags are rejected. Retired `--issue` is rejected with the `--issues`
correction. There are no implementation flags.
All argument syntax, including project and priority paths, is validated before discovery or
GitHub calls; invalid input cannot partially start a review.

## Review order and batches

1. Resolve current/explicit project scope and collect matching issues, excluding
   PRs; label mode filters by project before its cap and freezes its total
   across repositories before effects, while unbounded mode exhausts each repo.
2. Map issues to primary project owners using discussion, docs and source.
3. Process explicit priority projects first, then other projects by selected
   issue count descending, with alphabetical path ties. Uncertain owners go last.
4. Within each project, identify issues that can share one remediation. Review
   batches by supported severity first, then size, then lowest issue number.
5. Assess each issue individually, save accepted shared plans and publish decisions.
6. Finish that repository before processing the next one.

`--priority` changes order, not selection, severity or labels. Identical folder
names in different repositories remain separate groups. Unmatched priority
paths are reported. Incomplete unbounded/number collection leaves that repo
pending while other repos can continue. In label mode, unresolved earlier
collection leaves the entire bounded selection pending with no partial effects.
The report distinguishes coverage from completed review.

Label mode stops at the total cap or exhaustion, freezes identities before
any effect and never refills skipped/changed slots. Priority orders only that
selected set. Report unvisited repositories and incomplete discovery honestly.
No automatic next batch, hidden cursor or new overall time cap is introduced;
existing limits and user stops remain binding. Reruns select afresh and reuse
unchanged decisions, so still-open oldest matches may recur.

For example, three related BGP issues and two related SSH issues in
`services/vpngw` can form two batches and two plans. Each must share an evidenced
remediation boundary; a BGP keyword alone does not justify combining issues or
closing them as duplicates. Every member retains its own acceptance criteria.

## Decisions and outputs

Ordinary invocation authorizes documented existing-label changes, concise
comments and justified closure on the
selected issues, including a no-flags backlog review. Clearly out-of-scope or
disproven claims can close as not planned; confirmed duplicates and resolved
issues use their appropriate reasons. Needs-information and valid low-priority
issues remain open. Rarity alone does not reduce serious defects' importance.
Explicitly selected closed issues are assessed without automatic reopening.

Accepted batches use `research` and `design` as internal advisers. Each plan is
saved and verified at
`<project>/docs/github-issues/batch-<anchor-number>-<topic-slug>.md` before any
acceptance is posted. The anchor is the lowest initial accepted member number;
the filename stays stable. Example: `docs/github-issues/batch-123-bgp-routes.md`.
Existing matching plans are reused regardless of filename. Overlaps require
reconciliation; maintainer edits are preserved.
Before each acceptance, refresh the target and the sibling/source evidence
supporting its shared plan. Material drift requires reassessment and verified
updated coverage before publication.

**Implementation-plan content never appears in GitHub comments or chat.** Chat
shows repository/project order, counts, batch membership, individual decisions,
local plan links and verified/pending effects. GitHub receives concise decisions
and rationale. A failed save leaves acceptance pending; a partial comment
failure does not undo the shared file or other verified comments.

[Issue label management](references/labels.md) preserves unrelated labels and
uses only existing documented mappings. Missing or ambiguous mappings remain
recommendations; they do not block independent comments or justified closure.
No labels are created. Verify labels, comments and closure separately; observe
state before terminal labeling and read back ambiguous outcomes before retrying.

## Project overview and implementation approval

Complete batch files remain under their primary owners. The selected project
gets `docs/github-issues/project-plan.md`: scope, coverage, individual outcomes,
ordered batch links, owners, exact fingerprints, dependencies and readiness.
This snapshot does not duplicate detailed designs or track live execution.
Existing human edits and concurrent changes are protected.

After triage, the agent offers a specific ready set with links and actual project
targets. Approval can cover all proposed batches or a dependency-complete subset:

- Explicit `$implement` uses that workflow, one existing run per approved batch
  in dependency order and under its actual project.
- "Approve and continue", "implement it" or "proceed" in response to the exact
  proposal starts ordinary local implementation without invoking `$implement`.

Before either route, verify the matching checkout, project root, instructions,
change boundaries, plan bytes and material evidence. The agent resolves these
from source instead of assuming the review folder or asking for known paths.
Changed scope requires renewed agreement; host Plan Mode remains binding.
Implementation approval grants no publication or issue-closure authority.
Natural implementation creates no Implement receipt or automatic commit.

See [operator handoff](references/implementation-handoff.md) and
[project selection](references/project-selection.md).

## Prerequisites and boundaries

- Authenticated GitHub connector capabilities, or `gh` as a disclosed fallback,
  with existing permissions. No authentication repair or permission expansion.
- A matching local checkout before saving each repo's plans. Remote-only
  assessment cannot save into an unrelated repo or silently clone a target.
- Installed `research` and `design` with their dependencies for accepted plans.
  Both are read-only advisers in the current host mode; this skill owns saving
  and publication without a mode switch or user-visible plan tool.
- Host Plan Mode and explicit report-only limits prevent GitHub/file writes;
  report pending artifacts instead of exposing plan content.
- Sensitive reports use private reporting policy. No implementation, canonical
  spec edits, Git/PR actions, label creation, assignments, milestones or transfers.

## Maintained resources and evaluation

[SKILL.md](SKILL.md) owns the workflow. References cover
[grouping](references/batching.md), [triage](references/triage.md),
[GitHub effects](references/github-operations.md) and
[plan files](references/plan-files.md). The
[template](assets/issue-plan.md.template) is only for repository artifacts.

Trigger definitions live in `evals/trigger-prompts.csv`; offline behavior
scenarios and assertions live in `evals/evals.json`. Exercise these using
synthetic repositories and mocked GitHub effects, never live issues. Definitions
and static checks do not prove runtime activation or output quality.
Composition evaluation requires actual `research`/`design` instructions and mock
artifact tools; a single-skill installation cannot establish that success.

## Issue resolution across delivery

The [shared handoff contract](../worktree/references/issue-resolution.md) assigns
review and saved plans to review-github-issues, local implementation and individual
acceptance evidence to Implement, concise comments and verified closing references
to create-pr, and merge/issue-state verification to merge-pr. GitHub performs
native issue closure on default-branch merge when enabled. A partial fix or mere
issue mention never qualifies. Each delivery owner retains its explicit authority;
review does not start implementation, PR creation or merge by itself. Only a
separate direct operator approval activates the root handoff described above.
