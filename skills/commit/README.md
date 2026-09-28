# Commit

`commit` creates a fast local Git commit on the current branch without pushing.
It is intentionally smaller than `commit-push`, `create-pr`, and
`sdlc-commit`: it only inspects, stages, validates, commits, and reports status.
It may also execute one exact commit delegated by a fresh explicit
`$worktree integrate` after that workflow proves the checkout is eligible.

## Usage

```text
$commit [commit-message]
run $commit [commit-message]
apply $commit [commit-message]
execute $commit [commit-message]
```

The message is optional. Natural-language action requests and native skill
mentions anywhere in a request are supported: "please commit all changes",
"could you commit this?", or "use commit for these changes". No additional
public flags exist. Help, discussion, examples, negation, and requests to edit
the skill are non-actions. A default-branch commit needs separate clear consent
for that branch; commit-push always rejects default-branch publication.

The hook records nonauthorizing root-turn metadata for the current checkout.
The root agent interprets user intent and passes the receipt digest and typed
action to preparation. The helper then binds a one-use authorization under the
repository lock. Receipt validity proves origin and freshness, not meaning;
semantic judgment belongs to the root agent. Neither implicit skill selection
nor a receipt grants Git authority. Never ask users to repeat a magic phrase.
Unrelated turns update a separate receipt without overwriting active claims.
A fresh action after a consumed transaction gets a new receipt; replaying the
same receipt cannot create another authorization.

Capture failures report a fixed nonblocking reason: `PROMPT_UNAVAILABLE`,
`NATIVE_IDENTITY_UNAVAILABLE`, `REPOSITORY_UNAVAILABLE`,
`RECEIPT_WRITE_FAILED`, or `CAPTURE_FAILED`. They expose no raw prompt,
exception details or usable receipt digest. Validation failures preserve the
previous receipt; a publication error may occur after atomic replacement.
Neither outcome changes authorization or claim state. Do not reuse an earlier
receipt when the current prompt has no successful capture context.

Diagnose native event delivery, required identity fields, Git discovery and
private-state publication at their owning boundary. Codex uses native
`turn_id`; Claude uses its native `prompt_id` through the host adapter.
Do not invent identifiers or derive them from transcripts. Installation and
fixture tests do not prove active-session dispatch: verify native hook trust
and observe a real prompt produce fresh receipt context before publication.

## What It Does

- Resolves the Git repository root and runs Git commands from there.
- Inspects the complete tracked and untracked diff before staging and stops on
  obvious unsafe or incoherent content.
- Stages the complete repository diff with repo-root `git add -A`.
- Runs lightweight staged validation with `git diff --cached --check`.
- Uses a provided commit message or generates a concise imperative one.
- Creates a local commit with normal hooks enabled.
- Reports the final branch status and whether anything remains dirty.
- Uses a hidden one-shot authorization and claim so the helper can execute
  exactly this whole-repository commit without allowing raw Git mutation.
- Shares that local transaction owner with a semantic commit-and-push request;
  the publication skill still owns and bounds the later remote effect.

## Architecture

```text
Semantic root-user request and receipt binding
  |
  v
Current branch and fast safety checks
  |
  v
Temporary-index candidate and complete diff inspection
  |
  v
Selected-installation repository lock and drift revalidation
  |
  v
Exact full-repository staging, validation, and local commit
  |
  v
Final status report
```

## Core Concepts

- The skill is current-branch only.
- `git add -A` is mandatory and always runs from the Git repository root
  because monorepo changes often span projects.
- The public workflow stays `$commit [commit-message]`; authorization, the
  temporary index, token, lock, and claim states are internal implementation
  details.
- The current working directory never narrows the commit scope.
- Repository-shaping Git environment is rejected before discovery or
  mutation; only the transaction-owned temporary preview may override
  `GIT_INDEX_FILE`.
- Project lifecycle status is advisory and never gates direct execution.
  Explicit current-turn authorization plus the transaction's Git, branch,
  Worktree, secret, and workflow-conflict checks remain authoritative.
- The skill never pushes, opens PRs, repairs branches, or writes Agentic SDLC
  run state.
- Commit hooks should run normally.
- Delegated worktree commits remain local, require the exact preflight branch
  and head, bind the reviewed staged tree to the resulting commit tree, and
  return a clean direct-descendant commit to the integration workflow. A
  durable source-scoped preparation claim blocks competing Git/Worktree owners
  while the commit runs. Delegation is never permitted for nested/coordinated
  children or active integration attempts.
- Direct transactions move through `PREPARED`, `STAGED`, and `COMMITTED`;
  drift becomes `STALE`, while a hook-altered or otherwise uncertain commit
  becomes `REVIEW_REQUIRED`. The private review transition can complete only
  the currently checked-out, clean, exact direct child after its actual commit
  and tree have been reviewed. Failed hooks that create no commit become stale
  for a fresh explicit retry. Recovery never resets, amends, or unstages user
  work.
- Worktree ownership and direct commits share one common-repository lock within
  the selected agent installation. A
  direct claim refuses an active Worktree preparation or reservation for the
  same source ref, and malformed ownership or coordination records fail closed
  before staging. Active Agentic SDLC runs continue to own commits through
  `sdlc-commit`.
- Task Implementer workers receive the same hidden transaction interface from
  `task-start`, bound to their immutable assignment, running task plane, worker
  session, branch, and base commit. It can refresh a stale preview before the
  one direct-child commit, but cannot authorize a second commit. The helper
  revalidates live worker ownership before adopting an interrupted exact-child
  commit. This is delegated worker authorization, not an implicit root-user
  `$commit`.
  The transition returns a transient canonical `commit_context`: exact
  PATH-canonical Python, helper, exact scope cwd, worker repository, raw
  `CODEX_THREAD_ID`, evidence paths, and prepare argv. The separately returned
  session fingerprint is persisted evidence only and is never a `--session-id`
  value. This keeps mixed hook/worker Python versions usable while rejecting
  non-PATH interpreters, arbitrary wrappers, alternate helpers, or weakened
  owner checks.

## Files

- `SKILL.md`: Runtime workflow, guardrails, commands, and output contract.
- `agents/openai.yaml`: UI metadata and default prompt.
- `assets/hooks/commit_intent.py`: Nonauthorizing root-turn metadata for
  semantic commit and commit-push action binding.
- `scripts/commit_transaction.py`: Temporary-index preview, one-shot claim,
  locked staging, normal-hook commit, exact recovery verification, and private
  acknowledgement of a reviewed hook-modified direct child.
