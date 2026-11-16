---
name: review-pr
description: "Use only when explicitly asked to review a GitHub PR by number, URL, branch, or a bounded exact-label batch: coordinate code-review, safe repairs, checks and linked-issue assessment. Plain review never merges; combined review-and-merge delegates to merge-pr. In Agentic SDLC, report only."
disable-model-invocation: true
---

# Review PR

## Help

For `$review-pr --help` or `$review-pr -h` (including native Claude forms), return concise help and stop before
any workflow step. State the purpose and invocation policy. Show exact usage
for every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

## Public Usage

```text
$review-pr [<PR-number-or-URL-or-branch>]
$review-pr --label <exact-label> [--pr-batch <positive-integer>]
$review-pr --help
$review-pr -h
```

- A number, URL or branch selects that PR; no selector uses current-branch PR
  resolution. Natural-language review intent remains supported.
- `--label` selects one repository-wide batch with an exact existing label.
- `--pr-batch` limits total selected PRs for this invocation; default five in
  label mode. Requires `--label` and cannot combine with an explicit PR selector.
- `-h, --help` explains the interface without tools or effects.

Validate complete argument syntax before repository discovery or GitHub calls.
For label mode, read [label batches](references/label-batches.md) before selection.
Reject invalid/repeated flags or missing values; labels are literal, not CSV or
search expressions. Batch flags do not widen lifecycle authority.

## Agent Compatibility

Use `$review-pr` in Codex, `/review-pr` in Claude Code, or
`/skills:review-pr` in the Claude plugin. Dollar-prefixed skill examples
refer to the same named skill on either host; use the native invocation syntax.
Preserve the declared invocation policy, approvals and workflow ownership.
Use available native tools; an unavailable required capability is a blocker,
never permission to bypass a guard or claim unobserved behavior.

## Purpose

Use this skill for GitHub-backed pull request review work when the goal is to
inspect any provided PR, identify real issues, fix safe problems when branch
permissions allow, and leave it closer to merge-ready.

Plain review stops at findings and readiness. Terminal-label audits remain
audit-only even with combined merge wording. For other selections, a root request
to review **and merge** delegates once per selected PR to merge-pr. Its parent-owned
review-pr call returns evidence to merge-pr without delegating again, even
when the original prompt includes merge intent. This is calling context, not
a public mode flag. Help, report-only and active SDLC limits take precedence.

## Use This Skill For

- Reviewing an open PR by number, URL, or current branch against its base
  branch, usually `main`.
- Reviewing PRs opened from the user's branch, another same-repository branch,
  or a fork.
- Checking whether the branch is mergeable and whether CI or review blockers
  remain.
- Fixing safe code, test, workflow, or documentation issues directly on the PR
  branch when the branch can be safely updated.
- Resolving straightforward conflicts with `main` or the PR base branch when
  the correct resolution is clear and push permissions allow it.
- Reporting review findings and exact blockers when the branch cannot be
  updated safely.
- In an Agentic SDLC run, checking the PR against requirements, design, local
  validation, tests, evaluation, UAT, and commit evidence when that evidence is
  available.

## Requirements

- A Git repository with the selected base origin and PR objects available through
  the trusted create-pr owner intake, for same-repository or fork PRs.
- GitHub CLI (`gh`) authenticated for the target repository.

## Active Agentic SDLC Review Mode

When the PR maps to a matching active Agentic SDLC run, this mode overrides the
generic review-and-fix behavior below. Review is findings-and-readiness-only:

- Reload the active run, current checkpoint, execution coordinator, commit
  evidence, UAT evidence, recorded PR evidence, and current GitHub PR state.
- Require the local clean `HEAD`, recorded `promoted_head`, and PR head SHA to
  be identical before calling the handoff reviewable. Disagreement maps to
  `PR_HEAD_DRIFT`.
- Read the base/head diff, checks, review decision, comments, conflicts, specs,
  and SDLC evidence. Record a concise local readiness result when private run
  state is writable.
- Do not check out or switch branches, edit files, run mutating formatters,
  commit, amend, merge, rebase, update the branch, resolve conflicts, push, or
  otherwise change the PR head.

Any finding that requires a branch change is a coordinator input, not a repair
inside `review-pr`. Classify it with `sdlc-classify-failure`, route it through
`sdlc-start`, and invalidate every affected downstream gate. The workflow must
validate, test, evaluate, update documentation, align, seal/promote, re-evaluate optional UAT obligations,
and republish a new exact SHA before review resumes. `sdlc-merge-pr` remains the
only Agentic SDLC merge path and requires explicit merge authority, including an already accepted run-level
merge completion boundary. Do not ask again for that existing authority.

## Sibling Skill Routing

`review-pr` should stay the coordinator skill for PR review, but it should pull
in the smallest relevant set of sibling skills based on the actual PR surface.
Do not load unrelated skills just because they exist.

Route selectively like this:

- `code-review`: always owns nested read-only analysis of the frozen complete
  base/head diff and affected callers/tests. Reuse analysis only from the same
  root operation at unchanged exact head/base; review-pr always refreshes
  GitHub identity, checks, comments, objections, threads and mergeability.
- `maintain-project-specs`: root-owned reconciliation for every affected project
  after contribution assessment and validated repairs. Follow
  [contribution assessment](references/contribution-assessment.md), including
  baseline acceptance, missing-spec bootstrap and justified unchanged outcomes.
- `align`: when the PR spans multiple surfaces and needs end-to-end alignment
  across implementation, tests, docs, help output, CI, or examples.
- `github-workflows`: when the PR changes `.github/workflows/**`, reusable
  workflow behavior, release automation, or merge/publish gates.
- `helmchart`: when the PR changes Helm charts, including `Chart.yaml`,
  `values.yaml`, templates, schema, or chart publication contracts.
- `python-project`: when the PR centers on Python packaging, `pyproject.toml`,
  `src/`, CLI structure, pytest, Ruff, or general Python project hygiene.
- `container`: when the PR changes Dockerfiles, base images, image build inputs
  or container dependency updates, including Dependabot Docker PRs.
- `shell-scripting`: when the PR changes `.sh` files, shell helpers, or Bash
  CLI flows.
- `linter`: when the PR needs shell, Markdown, or Python lint cleanup as part
  of making the branch merge-ready.
- `nebius`: when the PR depends on live Nebius IAM, VPC, quota, MK8s, or SDK
  behavior.
- `terraform`: when the PR is mainly about Terraform module or environment
  structure, interfaces, validation, or security posture.
- `publish-release`, `publish-image`, or `publish-helm`: when the PR changes a
  release helper plus its matching publication workflow and changelog contract.

Alongside code-review, select only the matching domain skills. A broad PR may
need `align` plus the one or two specialists for its surfaces. All child analysis
is report-only; review-pr owns repair decisions and final readiness.

## Enrolled automation repositories

Before GitHub effects, read the sibling merge-pr
`merge-pr/references/automation-authority.md` contract. In an enrolled
repository, first apply terminal-label audit precedence: never dispatch
`process` for a terminal audit, even with full-lifecycle wording or a stale label
on an open PR. Use read-only observation or an already-authorized narrow
completion capability; unavailable completion effects stay pending without a
local bypass. For other selections, a read-only request dispatches `review`; an explicitly authorized full
lifecycle dispatches `process`. Never publish a local merge attestation or call
the broker separately. In the isolated hosted agent, return findings and
contribution/spec assessments through the supplied schema only. The controller
owns patch publication, closure and delegation to merge-pr.

## Workflow

Read [the shared PR lifecycle](references/pr-lifecycle.md) and
[contribution assessment](references/contribution-assessment.md) before
ordinary review. Review accepted behavior, safety and existing-user compatibility
first; repair proven defects, then reconcile canonical specs before final review.

1. Before any per-PR dispatch, freeze the selected set under the batch reference
   when in label mode. Bind exact host/repository/PR, head/base branches and SHAs,
   author, ownership, draft state and live default. Resolve actual PRs, not an
   unrelated current branch. Collect all direct closing links across the frozen
   set and establish one shared issue assessment/effect registry by full identity
   before delegating any selected PR. The actual issue skill advises read-only;
   incomplete supporting discovery keeps dependent effects pending.
   Route terminal-label selections to audit-only completion before local or
   enrolled dispatch, never new repair or merge admission. Active Agentic SDLC
   retains its read-only mode. For other ordinary root combined review-and-merge
   requests, delegate once per PR to merge-pr with the original intent/receipt
   and supported shared-issue context. Collect each outcome into the same batch
   registry before continuing; return the aggregate after the selected batch.
   A single-PR caller returns its outcome. A parent-owned call continues here
   without rediscovery or re-delegation. Keep the registry in coordinator context,
   not a new service or invented owner API; unsupported coordination stays pending.
2. For ordinary work, read
   the ordinary completion reference from the installed create-pr skill and
   the commit continuation reference from that same skill.
   Read create-pr's private-review reference for mandatory checkout isolation,
   including parent-owned calls; retain the original transaction authority.
   Honor report-only restrictions and current GitHub write permissions. Pin
   trusted helpers before contributor checkout and isolate candidate execution.
3. Delegate complete frozen base-to-head diff analysis to nested read-only
   code-review (or reuse same-operation exact-head/base analysis). Inspect specs,
   fresh review comments, unresolved threads, checks and conflicts. For dependency
   updates, including major versions and Docker, review
   breaking changes and dependency/security information with meaningful validation.
   Select the matching Python, Actions or container specialist. Bot identity,
   metadata and green CI never replace review. Safe repairs may include source
   changes and invalidate earlier head/base review. Review locally;
   use only the enrolled isolated hosted agent contract when Actions owns execution.
4. Keep the caller checkout, index, files and local refs untouched throughout
   every review. Metadata/diff-only review allocates nothing. Before checkout-dependent
   inspection, validation or editing, use the canonical owner's private review
   workspace: standalone `begin-review`, or parent-owned `open-review`.
   Use only the returned execution root and exact PR head/base. Never copy caller
   dirt or local-only commits, switch the caller, or fall back to it if allocation
   fails. Candidate tests still require restricted execution separate from trusted
   owner state. Then classify findings and implement safe, supported repairs
   when authorized and writable. Stop for semantic ambiguity, unsafe changes, unresolved human
   objections or unavailable validation. Do not guess a product decision.
5. Delegate private-workspace checkout, commits, base synchronization and pushes
   to create-pr's private preparation handoff, using the execution manifest bound
   to the active original grant and receipt. Never create a second root receipt.
   Safe repairs update the existing remote PR without importing them into the caller. It cannot call review-pr or merge-pr again.
   Never use raw commits, rebase, force push or GitHub update-branch here.
6. After repair proof, invoke maintain-project-specs at the root for every
   affected project and publish any required paired update before the owner
   commits/publishes the final candidate. Record aligned, unchanged, disabled
   or blocked outcomes with evidence. Run relevant checks, then re-review the complete final diff and fresh remote
   head/base after each repair. No earlier review survives a changed head or base.
   A newly discovered unsafe finding stops the loop and returns a blocker.
7. For an ordinary completion caller, require no unresolved findings, passing
   validation/CI and no outstanding human requested changes or threads. Follow
   the Actions protocol reference from the installed merge-pr skill to post a
   structured COMMENT review on the exact head. Record its review ID, head/base
   and validation evidence. This attestation does not approve the author's own PR.
   Standalone review also returns this evidence when writable and eligible;
   report-only/non-default reviews return findings without merge admission.
   Eligible fork reviews use the same bound attestation and protected broker.
8. Publish/read back the selected PR disposition from the shared lifecycle,
   preserving report-only/SDLC restrictions; then return findings/fixes, validation,
   final identity and attestation or blocker.
   Independently assess linked issues under the shared lifecycle; report each
   label/comment/closure effect or pending recommendation separately. Plain
   review does not gain issue-closure authority from batch selection.
   The review coordinator never issues merge effects. The calling
   create-pr/publication/merge-pr owner continues only
   within its existing completion authorization. Standalone review closes its
   repair transaction after publication and verification through its original owner.
   No switch-back or local branch refresh belongs to review. At a resumable pause,
   keep the original grant and registered execution workspace; report the resume
   context. Cleanup only after terminal closure and proof that no unpublished or
   unresolved work remains. Parent-owned review
   preserves the original receipt, grant, targets and readiness deadline; only
   the completion owner closes at the selected final boundary.

## Command Reference

Read the `private-review` reference from the installed create-pr skill before
any checkout-dependent review, including nested review and recovery.

Read `references/command-reference.md` when exact GitHub CLI or Git commands
are needed for PR metadata, checkout, local base sync, conflict detection, or
branch updates.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Guardrails

- Every invocation preserves the caller checkout throughout. Parent workflows
  retain separately authorized effects; nested review never requires them to
  import its repair commits. Return canonical execution evidence to the parent.
- A private Git repository is not a security sandbox for contributor programs.
  Preserve the restricted test boundary in contribution assessment.
- Default behavior is review-and-fix, not report-only, unless the user asks for
  audit-only output or active Agentic SDLC review mode applies.
- In active Agentic SDLC review mode, never mutate the exact promoted PR head;
  findings that require changes must return to `sdlc-classify-failure`.
- A PR link or number is authoritative. Do not ignore it and review the current
  branch instead.
- Use sibling skills selectively by changed surface; do not turn every PR review
  into a full-repo multi-skill pass.
- Do not claim a PR is ready if checks are failing, conflicts remain, or review
  blockers are unresolved.
- Do not claim conflicts are resolved unless the conflict-resolution commit was
  pushed to the PR branch or GitHub confirms the branch is mergeable after the
  update.
- Do not clear or ignore unresolved reviewer concerns without evidence that the
  branch now addresses them.
- Resolve conflicts automatically only when the correct merge is obvious from
  local context. Stop and explain when conflicts are semantic or risky.
- Never rewrite the default branch.
- Ordinary review/merge intent covers scoped, non-destructive repairs to the
  selected contributor branch when GitHub permits them. Preserve explicit
  report-only restrictions; permission denial is a blocker, not another PR.
- Replacement publication requires explicit full-lifecycle adoption intent or
  enrolled adoption policy and the shared lifecycle coverage/lineage gates.
  Plain review never grants that authority.
- Do not rebase or force-push in the ordinary review repair path.
- Do not issue approval or merge effects. The Actions broker approves only after
  local attestation; merge-pr owns all ordinary merge effects.
- Do not let a sibling skill override `review-pr`'s ownership of readiness,
  branch safety, or final review judgment.
- Keep the PR branch aligned across code, tests, docs, and workflows before
  calling it ready.

## Output Contract

When using this skill:

1. Review the PR against the base branch, or perform the selected terminal audit.
2. Identify whether the PR branch is same-repository, forked, writable, and
   safe to update.
3. Apply the smallest relevant set of sibling skills for the changed surface.
4. Fix safe issues directly on the PR branch when appropriate and writable.
5. Resolve straightforward conflicts when safe and push permissions allow it.
6. Run focused validation and report what actually ran.
7. Report the private execution context, final remote head, caller preservation,
   and cleanup or retention outcome. Never imply local checkout adoption.
8. State clearly whether the PR is ready to merge and what, if anything, still
   blocks it.
9. For batches, report the limit, frozen selection and coverage; distinguish
   reviewed, skipped, pending and unreviewed PRs. Report deduplicated linked
   issues separately, with independently verified label/comment/closure outcomes.
