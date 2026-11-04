# Review PR

Ordinary review first assesses the contribution against accepted behavior,
safety and supported existing-user workflows. After proven safe repairs, the
root invokes maintain-project-specs for every affected project, then reviews the
complete final diff again. No docs changes in the diff alone are not a finding; changed docs
alone are not proof of alignment. See [contribution assessment](references/contribution-assessment.md).
Normal invocation covers scoped non-destructive fork repairs when GitHub permits;
report-only and active SDLC stay read-only. Standalone review never merges.

`review-pr` reviews GitHub pull requests and safely moves them closer to
merge-readiness when permissions allow.

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
7. Report readiness, remaining blockers, and merge guidance.

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

## Reviewed dependency updates

Dependabot updates, including Docker, require complete local review, meaningful
validation and fresh evidence after every head/base change. Safe source repairs
use create-pr. Bot metadata and green CI alone cannot authorize merging.
Plain review records readiness and never initiates merging. Every PR review
uses nested read-only code-review for complete frozen diff analysis, supplemented
by matching specialists. Same-operation exact-head/base analysis may be reused,
but GitHub state and objections are always refreshed.

### Local checkout completion

The canonical commit transaction owns safe target checkout and outer completion.
See [the completion contract](../create-pr/references/checkout-completion.md) for
review switch-back, create-only behavior, verified-result fast-forwards, resumed
work and publication-preserving policy. Merge, CI/Pages, local refs and deletion
are reported independently. No public skill flags are added.
