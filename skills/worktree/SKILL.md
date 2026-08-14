---
name: worktree
description: "Requires explicit invocation to create, locally integrate, exactly reuse, or safely remove a full-repository linked Git worktree for monorepo work. Use `$worktree add` from a clean named non-default source branch, `$worktree integrate` from the primary checkout to safely commit eligible ordinary child/source dirt before exact candidate validation, and `$worktree remove` after exact local proof. Do not use for push, PR creation, publication, or parallel-agent orchestration."
---

# Worktree

## Help

For `$worktree --help` or `$worktree -h`, return concise help and stop before
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

## Purpose

Create parallel full-repository child worktrees from the exact current local
feature branch, then serialize their proved commits back into that source
branch without publishing child branches.

## When To Use

- The user explicitly invokes `$worktree`, `$worktree add`,
  `$worktree integrate`, or `$worktree remove`.
- A monorepo developer wants separate linked checkouts for concurrent project
  work while retaining one local source branch for final publication.

## When Not To Use

- Do not invoke implicitly.
- Do not use from the default branch or detached HEAD. `add`, `remove`, restart,
  and active-attempt recovery require a clean primary checkout; only a fresh
  `integrate` may commit eligible primary dirt.
- Do not use child worktrees for push or PR publication. Publish only the
  accumulated source branch through the standalone Git/PR skills.
- Do not invoke these public actions as Task Implementer's internal worker
  manager. Task Implementer is an approved private consumer of separate lane
  primitives owned by this helper; those primitives are not public Worktree
  actions or compatibility aliases.
- A caller or coordinator reaching an outer-lifecycle handoff must return the
  exact `$worktree ...` command and stop. A recorded next skill, hook
  continuation, or internal phase transition is not the fresh explicit user
  invocation required to run this skill.

## Inputs

```text
$worktree [add] [<task description>] [--project <repo-relative-directory>] [--reuse <exact-name>]
$worktree integrate <generated-worktree-name> [--restart]
$worktree remove <generated-worktree-name>
```

- When a task description is present, derive one public-safe task slug from it.
  When it is absent, let the helper normalize the resolved project directory's
  basename; for example, scope `skills` defaults to task slug `skills`. An empty
  normalized basename falls back to `work`.
- Never pass raw prompt text, secrets, or a full project path into generated
  identities.
- `--project` selects an existing starting directory and descriptive label. It
  never creates a partial checkout or restricts changed paths. After successful
  creation or reuse, that returned directory becomes the operational directory
  for subsequent development commands.
- `--reuse` and lifecycle actions require the exact generated name.

## Required Reads

- Read [references/lifecycle.md](references/lifecycle.md) before integration,
  recovery, restart, or cleanup.
- For `integrate`, load `$commit` before an eligible automatic commit and load
  `$align` for candidate validation. Load the relevant Task Implementer or
  Agentic SDLC handoff when that workflow produced the child head.

## Writes

- Creates a sibling `<repo-name>-worktrees/` parent, one generated child branch,
  one full linked worktree, branch-associated Git config, and private atomic
  ownership state under `.worktree-skill/` outside the repository checkout.
- Integration creates a private candidate branch/worktree, one exact merge
  commit, and advances the checked-out source branch only by verified
  fast-forward.
- A fresh explicit integration may create one whole-repository local commit in
  an eligible ordinary dirty child and then one in the dirty primary source.
  It retains either commit if a later step fails.
- The first delegated commit creates a durable source-scoped preparation claim
  under private state. That claim blocks competing source integration,
  coordinator lease acquisition, removal, and source publication until the
  exact candidate reservation consumes it or an explicit reviewed restart
  drops only the claim while retaining every commit.
- Worktree ownership transitions also take the same Codex-private
  common-repository lock used by direct `$commit` transactions. A direct
  transaction that acquires it first is revalidated before Worktree proceeds;
  an existing preparation or reservation for the source ref blocks the direct
  transaction. This serializes ownership without sharing or replacing either
  workflow's claim schema.
- Removal non-forcibly removes the child worktree, deletes its unchanged local
  ref with an expected-old SHA, deletes any exact released nested-lease
  receipt, and deletes its private manifest.
- The deterministic helper classifies dirt and freezes exact heads. For an
  eligible delegated `$commit`, its private integration-commit action binds the
  reviewed staged tree, holds the durable preparation claim, runs the exact
  normal-hook commit, and records its direct-child/tree proof. Neither layer
  fetches for lifecycle decisions, pushes, creates PRs, deletes remote branches,
  or writes private workflow state into the repo.
- Private Task Implementer lane primitives keep separate schema-v1 lane state
  and immutable generation receipts under the same protected state root. The
  private prepare primitive journals and claims an exact whole-lane candidate
  for review without mutating the real index or history. The token-bound open
  creates at most one repo-root fixed-message checkpoint and publishes its exact
  clean post-checkpoint head as the lane and lease baseline. Hook mutation
  rotates the token; interrupted retries adopt only the exact reviewed staged
  tree or exact reviewed clean direct child, and active-generation dirt never
  triggers another checkpoint. These primitives continue
  reusing schema-v4 ownership, exact integration candidates, locks, and
  expected-head cleanup. Public `integrate` and `remove` reject those lanes,
  and ordinary coordinator lease acquisition rejects them before lease or
  manifest mutation. Branch `lane_id`, `source_ref`, and `incarnation` metadata
  is an all-or-none identity; incomplete or missing metadata for a live lane
  fails closed before the checkout can be classified as an ordinary managed
  child. Task Implementer and Agentic SDLC remain separate peer workflows over
  this shared Worktree substrate.

## Process

### Add

1. From the primary checkout, run the nonmutating preflight: verify the entire
   worktree is clean, no Git operation is active, the branch is named and not
   configured `origin/HEAD`, and the sibling parent/state path is canonical and
   non-symlinked. The helper repeats this preflight under its lifecycle lock
   before creating state or worktree resources.
2. Resolve the project directory. Derive and pass a task slug when the user
   supplied a task description; otherwise omit `--task-slug` so the helper
   derives it from the resolved directory basename. Then run:

   ```bash
   python3 <skill-dir>/scripts/worktree_manager.py add \
     [--task-slug <public-safe-slug>] [--project <directory>] [--reuse <exact-name>]
   ```

3. Treat the returned source SHA as immutable creation evidence. Return the
   resolved task slug, generated name, branch, full worktree path, and selected
   starting directory.
4. Adopt the returned `scope_cwd` as the active operational directory for
   subsequent development work. Set each tool call's working-directory field to
   that exact path, or run `cd -- "$scope_cwd"` only inside an interactive shell
   whose state the agent owns. From that directory, re-observe `pwd`, the child
   branch, exact `HEAD`, and cleanliness before reporting readiness.
5. Keep later lifecycle actions anchored to the clean primary checkout. A
   helper subprocess cannot change its parent process, the Codex workspace, or
   an editor window, so never claim a persistent shell/editor switch unless it
   is independently observed. Never launch or reopen an editor unless the user
   explicitly asks.

### Integrate

1. Run only from the primary checkout on the recorded source branch. Invoke the
   read-only preflight, adding `--restart` only for an explicit restart:

   ```bash
   python3 <skill-dir>/scripts/worktree_manager.py integration-preflight \
     --name <exact-name> [--restart]
   ```

2. For a fresh `commit-required` result, inspect both complete checkout diffs
   before staging. Stop on conflicts, active Git operations, suspected secrets
   or private endpoints, unclear generated files, incoherent changes, nested
   lease participation, any reservation, or orphan candidate resources.
   Otherwise use delegated `$commit` in the returned `commit_order`:
   ordinary child first, primary source second. Set the exact checkout as the
   working directory, run repo-root `git add -A`, validate
   and inspect the full staged diff, generate a truthful message, and record
   the reviewed staged tree with `git write-tree`.
3. Return to the primary checkout and create the exact normal-hook commit
   through the private helper. The first call omits the preparation token; each
   later call repeats the token returned by preflight or the prior commit:

   ```bash
   python3 <skill-dir>/scripts/worktree_manager.py integration-commit \
     --name <exact-name> --target <child-or-source> \
     --expected-head <pre-commit-sha> \
     --expected-tree <reviewed-staged-tree> \
     --message <truthful-message> \
     [--preparation-token <exact-token>]
   ```

   The helper atomically claims the source lifecycle before committing and
   rechecks lease/reservation/candidate ownership, branch, head, complete
   staging, and the reviewed tree. It records one direct-child commit and
   compares `HEAD^{tree}` with the reviewed tree. If a hook changed the commit
   tree, inspect the complete actual commit, then bind that exact head/tree with
   `integration-commit-review --name <exact-name> --target <target>
   --preparation-token <exact-token> --commit-head <sha> --commit-tree <tree>`.
   Retain every successful commit if any later step fails; never reset, revert,
   amend, or discard it.
4. Re-observe the same branches, exact direct-child commit transitions, and
   complete cleanliness after each commit. Rerun preflight until it returns
   `ready-clean`; do not create a candidate from a `blocked` or
   `commit-required` result.
5. Start or resume the exact integration with the clean heads and optional
   preparation token returned by the final preflight:

   ```bash
   python3 <skill-dir>/scripts/worktree_manager.py integrate \
     --name <exact-name> \
     --expected-source-head <source-sha> \
     --expected-child-head <child-sha> \
     [--preparation-token <exact-token>] [--restart]
   ```

6. If the helper reports `conflict`, preserve the returned recovery worktree.
   The developer resolves the conflict there and stages the resolution, then
   reruns preflight and repeats the exact integration. Never merge or resolve
   in the primary or child.
7. If the helper reports `validation-required`, bind all checks to the returned
   candidate SHA and worktree. Run changed-surface `$align`, relevant tests,
   and any managed Agentic SDLC combined UAT non-mutatingly. If source changes
   are required, make and commit them in the child, then restart; do not repair
   the candidate outside conflict resolution.
8. Promote only the exact validated candidate, repeating the exact expected
   heads returned by the resume preflight:

   ```bash
   python3 <skill-dir>/scripts/worktree_manager.py integrate \
     --name <exact-name> \
     --expected-source-head <source-sha> \
     --expected-child-head <child-sha> \
     --validated-head <candidate-sha>
   ```

9. If the source moved, preserve the attempt. After review, explicitly use
   `--restart` to abort and remove only the exact owned candidate and rebuild
   from the current clean source and child heads. Restart and any active
   attempt never auto-commit. If restart instead finds only a commit-preparation
   claim, use its exact private `integration-preparation-abort` action; this
   removes no Git commit and preflight must be repeated from the retained heads.

### Remove

1. Run from the clean primary checkout with the exact name:

   ```bash
   python3 <skill-dir>/scripts/worktree_manager.py remove --name <exact-name>
   ```

2. Permit removal only when the child is unchanged from its base or its exact
   recorded merge remains reachable from the source branch. Keep any dirty,
   advanced, rewritten, leased, or unverifiable resource intact.

## Idempotency

- Exact `--reuse` preserves an active child and reports source-head drift; it
  never rebases or refreshes the child.
- Repeating a description-less add in the same project resolves the same
  project-basename slug and fails closed on the existing lifecycle unless the
  exact generated name is supplied with `--reuse`.
- Repeating `integrate` resumes the same durable `(source head, child head,
  candidate ref/path)` attempt. A promoted-but-unrecorded candidate reconciles
  from Git proof before cleanup. If an interrupted handoff leaves both the
  exact preparation and its reservation, a token-bound retry consumes the
  preparation before candidate work resumes.
- Repeating a fresh integration after a retained automatic commit observes that
  checkout as clean and does not create a duplicate commit.
- Repeating `remove` resumes from remaining exact local resources. Missing
  resources are accepted only when the durable manifest proves their identity.
- Nested lease release persists a schema-v4 terminal receipt. Replays require
  the exact owner, token, promoted head, clean outer checkout, and absent
  resources; the receipt is removed only with the outer lifecycle. Schema-v4
  ownership manifests retain the lease participation state and exact identity,
  while ordered promotion heads permit successive Task Implementer waves only
  through expected-head compare-and-set. Outer removal retains an exact private
  removal-intent snapshot until receipt, manifest, and final resource cleanup
  complete.
- Persistent Task Implementer lanes are idempotent by logical common-directory,
  primary-checkout, named source-ref, and project-scope identity. Their
  generations are monotonic, released receipts are immutable, integration
  consumes a contiguous pending range and rearms the same lane, and explicit
  lane removal records an incarnation boundary without deleting Task prompt
  history.

## Failure Handling

- Preflight failure creates no branch or worktree.
- Integration preflight is read-only. A blocked automatic commit creates no
  candidate; a successful earlier child or source commit remains local and is
  reported for an exact retry.
- Partial creation retains a recovery manifest only for exact clean resources
  at the recorded base.
- Merge conflict retains the private candidate and integration reservation;
  other integration into the same source is blocked until reconciliation.
- A Task Implementer candidate rejected by combined review is archived by its
  exact SHA behind a private compare-and-set ref before only its clean temporary
  candidate resources and reservation are released. The source ref stays
  unchanged and the pending lane becomes correction-ready. An exact replay may
  re-read that terminal receipt while the correction run has only a pending
  generation checkpoint; it does not advance integration, finish the
  checkpoint, or change either head. Ordinary Worktree integration has no
  review-rejection transition.
- Source or child movement, malformed state, wrong merge parents, failed
  validation, failed fast-forward, or uncertain cleanup fails closed and
  preserves evidence.
- Older manifests, reservations, and lease schemas return
  `WORKFLOW_UPGRADE_REQUIRED`; there is no migration or compatibility path.

## Must Not

- Never use `git worktree remove --force`, broad prune/GC, reset, rebase,
  cherry-pick, force-push, or direct checked-out-ref updates.
- Never allow direct managed-child, integration-candidate, Task Implementer
  worker, or Agentic SDLC worker `commit-push`/`create-pr`; state-backed guards
  must route to the owning local promotion and integration workflow.
- Never treat `--project` as a staging or changed-path boundary. Git operations
  see the full linked checkout.
- Never treat a subprocess-local `cd` as proof that the parent shell, Codex
  workspace, or editor changed directories or selected the child branch.
- Never launch, reopen, or retarget an editor as an implicit side effect of
  `add` or `--reuse`.
- Never auto-commit a nested/coordinated child, candidate, restart, active
  attempt, conflict resolution, or unsafe/unclear diff. Only a fresh ordinary
  child and then its primary source are eligible.
- Never report integration or cleanup complete without re-observing exact Git
  identity, ancestry, parents, index, and cleanliness.

## Completion Criteria

- `add`: a clean child exists on a generated no-upstream branch at the exact
  captured local source SHA, and a read-only command run with `scope_cwd` as its
  working directory confirms that child identity.
- `integrate`: the source checkout is clean at the exact validated two-parent
  merge SHA, durable proof is recorded, and the private candidate is absent.
- `remove`: the child worktree, local child ref, and manifest are absent; remote
  state is untouched.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return the action, resolved task slug, generated name, child/source branches
and exact SHAs, worktree/start-directory paths, adopted operational directory,
editor-switch status, preflight classification and commit order, every retained
automatic commit SHA, candidate or recovery path when applicable, validation
status, retained resources, and precise next action.
