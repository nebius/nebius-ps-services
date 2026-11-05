# Review GitHub Issues

Review GitHub issues by project and shared remediation, apply evidence-backed
triage decisions, and save one implementation plan per accepted batch. The
skill never implements the plans.

## Usage

```text
$review-github-issues
$review-github-issues --priority services/vpngw,services/nebius-cxcli
$review-github-issues --issue 123,124
$review-github-issues --repo example-org/alpha,example-org/beta
$review-github-issues --repo example-org/alpha,example-org/beta --issue 12,34
$review-github-issues --repo https://github.com/example-org/alpha --issue 12
$review-github-issues --help
```

Use `/review-github-issues` in Claude Code or
`/skills:review-github-issues` through the Claude plugin. Invocation is
explicit-only. This is an agent skill, not a separately installed executable.

| Option | Meaning |
| --- | --- |
| No flags | Review all observed open issues in the current checkout's GitHub repo, across all project folders. |
| `--repo` | Comma-separated repository names or URLs; finish repositories in supplied order. |
| `--issue` | Comma-separated positive numbers; check each in every selected repo and review every existing issue match. |
| `--priority` | Comma-separated exact project roots, highest first, applied independently in each repo. |
| `-h`, `--help` | Explain the interface and effects without tools or side effects. |

Without `--issue`, collect all open issues from each selected repository.
Unknown repository identity requires clarification. Empty backlogs end without
effects. Repeated inputs are deduplicated; positional issue numbers/URLs and
unknown flags are rejected. There are no implementation flags.
All argument syntax, including priority paths, is validated before discovery or
GitHub calls; invalid input cannot partially start a review.

## Review order and batches

1. Finish paginated collection for one repository, excluding pull requests.
2. Map issues to primary project owners using discussion, docs and source.
3. Process explicit priority projects first, then other projects by selected
   issue count descending, with alphabetical path ties. Uncertain owners go last.
4. Within each project, identify issues that can share one remediation. Review
   batches by supported severity first, then size, then lowest issue number.
5. Assess each issue individually, save accepted shared plans and publish decisions.
6. Finish that repository before processing the next one.

`--priority` changes order, not selection, severity or labels. Identical folder
names in different repositories remain separate groups. Unmatched priority
paths are reported. Incomplete collection leaves that repo pending while other
repos can continue; the report distinguishes coverage from completed review.

For example, three related BGP issues and two related SSH issues in
`services/vpngw` can form two batches and two plans. Each must share an evidenced
remediation boundary; a BGP keyword alone does not justify combining issues or
closing them as duplicates. Every member retains its own acceptance criteria.

## Decisions and outputs

Ordinary invocation authorizes concise comments and justified closure on the
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

The maintainer owns implementation, commits, pushes, PR creation and merge.

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
  spec edits, Git/PR actions, labels, assignments, milestones or transfers.

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
review does not start implementation, PR creation or merge.
