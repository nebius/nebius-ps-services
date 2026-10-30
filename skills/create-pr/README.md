# Create PR

Ordinary publication uses the transaction owner's
[bound PR intake and publication](references/pr-publication.md). Existing PRs
bind separate base/head repository identities and refs. Fork repairs use unique
local aliases and preserve the original PR/history. Every push is journaled,
non-force, protected against advertised-head races and independently read back.
The private task/checkpoint/closure v3 rollout requires draining old transactions.

`create-pr` turns local work or named branches into reviewable GitHub pull
requests. It is designed for branch-safe PR creation without hand-driving Git
and GitHub steps.

## What It Does

- Rejects publication from every state-classified managed child, integration
  candidate, nested worker, or inconsistent ownership claim and routes it to
  its local owner; only an unmanaged final source branch may become a PR head.
- Creates or reuses feature branches.
- Reuses the current non-default branch as the normal path, staging existing
  work only after formatting, whitespace, lint, and test gates complete,
  validating the staged diff, committing it, pushing it, and opening or reusing
  a PR without creating another branch.
- Runs safe pre-test hygiene before local tests, waits for local tests to
  finish, and commits only after selected checks pass or a real blocker is
  reported.
- Merges the latest `origin/<base>` into the PR branch before PR creation so
  the branch is current without rewriting history.
- Keeps PR branches conflict-free against the base branch when possible.
- Repairs safe branch-owned validation, build, lint, test, or GitHub check
  failures before presenting PR creation as handled.
- Waits for available GitHub PR checks to reach a terminal state before
  reporting a PR as ready.
- Opens or reuses GitHub pull requests.
- Preserves explicit user-supplied PR titles and bodies.
- When invoked from Agentic SDLC, checks local UAT evidence and summarizes
  requirements, features, validation, tests, evaluation, and UAT in the PR body.
- In an active unmanaged Agentic SDLC run, switches to publication-only mode: it requires
  a clean exact promoted SHA with passing UAT and does not stage, commit, merge,
  repair, or otherwise change that SHA. Push and CLI PR creation use one direct
  action with explicit ref and head arguments.
- When Agentic SDLC local state is available, records the PR URL and readiness
  summary in run evidence.
- Reports PR URLs, readiness, and merge order for multi-branch work.

Ordinary completion gives PR readiness, merge settlement and post-merge
verification independent, fixed 86,400-second (24-hour) budgets. Gate changes,
repairs and reruns retain their deadlines. An explicit user continuation can start
a new ordinary observation attempt while preserving the original transaction.
Caller-supplied checkpoint deadlines remain authoritative; release callers retain
their own phase budgets and explicit resume rules. Timeout preserves progress and leaves
GitHub work running, with the exact resume command in the completion report.

## Commit Continuation

One explicit PR task covers successive validated stage/commit/push repairs.
The shared `commit` helper begins a private task before any branch movement,
including clean starts, and binds its grant to the selected repository,
feature branch, native session, origin and base. Each attempt still requires
whole-repository candidate review and normal hooks. The grant permits corrected
no-commit retries and closes when the PR task finishes. It does not change
the one-actual-commit limit for standalone tasks or active SDLC publication-only mode.
See [commit continuation](references/commit-continuation.md) for the private
protocol and recovery boundaries.

## Architecture

```text
Local git state
  |
  v
Branch selection or current-branch reuse
  |
  v
Commit current feature-branch work or reuse requested commits
  |
  v
Base refresh, merge update, or conflict handling
  |
  v
Run focused validation, then nested code-review of the complete final diff
  |
  v
Push branch
  |
  v
Open or reuse GitHub PR
  |
  v
Local review-pr -> safe repairs/review -> reviewed PR (default)
  |
  v
Explicit merge intent only -> merge-pr -> actual merge and result CI
```

## Workflow

1. Inspect repository status, remotes, current branch, and base branch.
2. If already on a non-default branch and no branch was named, reuse that
   branch. For dirty work, run safe formatting, whitespace, lint, build, and
   test checks first, wait for local tests to finish, then stage with repo-root
   `git add -A`, validate the staged diff, and commit.
3. Select or create the target branch only when needed.
4. Refresh against the base branch, then merge `origin/<base>` into the target
   branch before PR creation. For a `main` base branch, merge `origin/main`.
5. Rerun focused validation after formatting fixes, merge, or conflict
   repair, and repair safe branch-owned failures.
6. Freeze the final base/head and run nested read-only code-review of the complete
   diff and affected callers before pushing with an explicit refspec. Safe repairs
   require fresh validation and review.
7. Open or reuse the PR with the requested title and body.
8. For active Agentic SDLC runs, require passing UAT, reverify the recorded
   remote-default branch and HEAD, and publish only the exact promoted SHA;
   route any branch or default drift back through the coordinator.
9. Keep repairing available branch-caused check failures when safe, or mark a
   real blocker. If GitHub checks are still pending, report the PR as pending
   instead of ready.
10. For ordinary work, run local review-pr and safe canonical repairs until the
    final head/base passes, then stop at the reviewed PR by default. Only explicit
    merge intent delegates to merge-pr and observes actual merge and result CI.
    Agentic SDLC remains publication-only.
11. Return PR/review identity, selected completion outcome and any blocker.

## Core Concepts

- Do not leave new work on the default branch.
- For new PR creation, reuse the current non-default branch. Existing PR repairs
  select their bound target, including the namespaced alias for a fork.
- Resolve the actual `origin` default branch and bind both authorization and PR
  creation to that exact base; do not guess a default branch name.
- Do not guess when GitHub authentication, remotes, or branch ownership are
  unclear.
- Always stage local work from the repository root with `git add -A`; do not
  narrow PR commits to selected paths.
- Run format and whitespace checks before tests when those checks may change
  files, wait for tests to finish, then stage and commit.
- Refresh base refs, then use the shared helper's private `sync` before PR
  creation. Review/check the actual result and acknowledge its exact tree.
  The helper owns the normal merge without rewriting history.
- Use the owner's private `publish-target` for ordinary publication. Active
  SDLC retains its separately scoped direct explicit-ref publication.
- Do not rebase or force-push inside this skill.
- Use draft PRs for incomplete work when that better matches readiness.
- Preserve one PR per branch.
- Treat a known fixable branch-owned failure as unfinished PR creation, not as
  a successful handoff with a link.
- Do not call a PR ready while local tests or GitHub checks are still pending.
- Treat active Agentic SDLC PR creation as publication-only. A different remote
  head, conflict, failed check, or requested repair returns to the coordinator.

## Files

- `SKILL.md`: PR creation workflow, guardrails, and command guidance.
- `agents/openai.yaml`: UI metadata and default prompt.
- `references/command-reference.md`: exact Git and GitHub CLI command cookbook
  loaded when executing or validating PR operations.

Private task intake freezes all selected local, remote-only or planned feature
refs and dependencies. Recorded sync handles no-op, fast-forward and normal
merges; multi-branch order checks use one declared scratch ref with exact cleanup.
Safe no-commit retries preserve the original task authorization and require a
fresh candidate review. Explicit finish covers completed, cancelled and
zero-commit tasks at the selected public completion boundary. Private handoffs
retain the grant. Local review and protected Actions approval are separate from CI.

## Ordinary completion

Default invocation performs complete nested code-review before publication,
then local review-pr, safe repair/revalidation and GitHub readiness checks. It
stops at the reviewed PR. Explicit "create a PR and merge it" delegates merge-pr.
An action request such as "Can you create a PR and merge it?" has the same
meaning; asking whether the skill merges by default does not authorize a merge.
There is no preparation flag; publication skills retain their own prep modes. Reuse the current feature branch; from default create a feature before the
canonical whole-repository commit. All agents stay local. Unsafe findings and
human objections stop. Agentic SDLC remains exact-head publication-only.
See [the completion contract](references/ordinary-completion.md).

### Local checkout completion

The canonical commit transaction owns safe target checkout and outer completion.
See [the completion contract](../create-pr/references/checkout-completion.md) for
review switch-back, create-only behavior, verified-result fast-forwards, resumed
work and publication-preserving policy. Merge, CI/Pages, local refs and deletion
are reported independently. No public skill flags are added.
