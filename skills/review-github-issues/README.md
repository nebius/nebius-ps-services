# Review GitHub Issues

Review selected GitHub issues, apply evidence-backed triage decisions, and save
implementation plans for accepted work. The skill never implements the plans.

## Usage

```text
$review-github-issues
$review-github-issues 123 124
$review-github-issues https://github.com/example-org/example-repo/issues/123
$review-github-issues 123 --repo example-org/example-repo
$review-github-issues --help
```

Use `/review-github-issues` in Claude Code or
`/skills:review-github-issues` through the Claude plugin. Invocation is
explicit-only. `--repo` selects the repository; `-h` and `--help` explain the
interface without tools or side effects. There are no implementation flags.

The current repository is the default when unambiguous. Unknown or conflicting
repositories require clarification. With no selectors, one open issue is
selected, zero ends the review, and multiple issues require numbers or links.
The skill never silently reviews the backlog.

## Decisions and outputs

Ordinary invocation authorizes concise issue comments and justified closure.
Clearly out-of-scope or disproven claims can close as not planned; confirmed
duplicates and already-resolved issues use their appropriate reasons.
Insufficient information and valid low-priority issues remain open.
Rarity alone does not reduce a serious defect's importance.

Accepted work uses `research` and `design`, saves a complete plan at
`<project>/docs/github-issues/<number>-<title-slug>.md`, verifies the file, then
posts acceptance. Example: `docs/github-issues/123-fix-connection-timeout.md`.
Existing plans are matched by repository and issue identity and retain
maintainer edits, including when an issue title changes.

**Plan content never appears in GitHub comments or chat.** Chat returns only
the disposition, brief rationale, local file path and action status. GitHub
receives the decision and rationale. A failed save leaves acceptance pending.
The maintainer owns implementation, commit/push, PR creation and merge.

## Prerequisites and boundaries

- Available authenticated GitHub connector capabilities, or `gh` as a disclosed
  fallback, with existing permissions for the selected effects. No auth repair.
- A matching local checkout/project path before saving a plan; remote-only
  assessment cannot write into an unrelated current repository.
- Installed `research` and `design` skills and their required dependencies for
  accepted planning. Installing this folder alone does not supply those skills.
  Both are read-only advisers within the current execution mode; this skill
  owns saving and public output, without a mode switch or user-visible plan tool.
- Host Plan Mode and explicit report-only limits prevent external/file writes;
  the skill reports the pending artifact instead of printing the plan.
- Sensitive security reports use the repository's private reporting process.
  No public disclosure, code changes, canonical-spec changes, Git/PR actions,
  labels, assignments, milestones or transfers are part of ordinary review.

## Maintained resources and evaluation

[SKILL.md](SKILL.md) owns workflow and authority. Supporting references cover
[triage](references/triage.md), [GitHub effects](references/github-operations.md)
and [plan files](references/plan-files.md). The reusable
[template](assets/issue-plan.md.template) is used only for repository artifacts.

Canonical trigger cases are in `evals/trigger-prompts.csv`; offline behavior
scenarios and assertions are in `evals/evals.json`. They are definitions, not
proof of runtime activation or output quality. Exercise them with synthetic
repositories and mocked GitHub effects; never mutate live issues for testing.
The composition scenario additionally requires the actual `research` and
`design` instructions in the isolated harness. A single-skill installation
without these dependencies cannot establish that scenario's success.
