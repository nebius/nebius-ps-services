# Task Implementer Execution Loop

This reference defines the coordinator-owned dependency-wave execution plane.
Canonical specs are root-owned project truth and are not an authorization
state machine.
Each dependency wave uses full-repository linked worktrees and one
coordinator-owned integration branch.

## Plan Contract

Normalize every task before locking IDs:

- stable task ID and concise objective;
- dependencies;
- write claims using `exact: repo/path` or `prefix: repo/directory`;
- keyed conflict domains;
- validation commands;
- done criteria;
- relevant prompt, repository-instruction, security, and external-action
  constraints.

Combine overlapping work when it is one coherent outcome. Otherwise add a
dependency. Build deterministic earliest-fit waves in stable task order.

The coordinator owns shared requirements, design, README, changelog, and other
declared coordination files. Requirements/design publication delegates to the
`maintain-project-specs` paired transaction. Workers never change those files
even when a lexical claim would otherwise include them.

## Prompt Impact

Task Implementer owns its prompt-impact claim and receipt schemas. The claim
classifies every accepted statement occurrence exactly once and binds the
accepted root-intent digest plus current canonical requirements/design receipt.

- Material ambiguity produces no plan basis.
- Contract or execution impact requires safe replanning with a distinct plan
  digest.
- Proven no-effect revisions may retain an existing plan basis.
- Exact requirements/design byte or receipt drift invalidates the current basis.
- Publication is serialized, append-only, owner-only, and compare-and-set.

The canonical v2 parser, pair validator, publisher, and receipt come from
`maintain-project-specs`. The receipt is immutable plan evidence, not workflow
authority. No historical lifecycle phase, project-instruction decision, or
Stop continuation is an execution prerequisite.

## Wave Preparation

1. Verify the persistent lane branch, `HEAD`, common Git directory,
   cleanliness, generation lease, and active claims.
2. Journal resource intent before creating the integration branch/worktree.
3. Create the integration worktree at the exact recorded lane base and
   re-observe its branch, head, root, common directory, and cleanliness.
4. Apply coordinator-owned spec/doc changes through the exact stage and commit
   transitions. A changed contract is one clean direct-child commit.
5. Existing applicable project instructions are read from the integration
   checkout. Task Implementer does not render or mutate them.
6. Create immutable assignments only for the active capacity batch.

## Assignment Contract

Each assignment binds:

- schema and task ID;
- run/wave identity and assignment digest;
- absolute worktree and selected-project cwd;
- branch, base commit, and Git common directory;
- exact helper/workspace paths and helper digest;
- inherited root-intent digest and canonical project-spec receipt bound to the
  contract commit;
- write claims and conflict domains;
- validation and done criteria;
- canonical worker guardrails;
- immutable predecessor handoff.

The worker starts through the exact `start_context` returned by `task-arm` or
`task-rearm`. Do not transcribe or reconstruct private worktree paths.

## Worker Loop

1. Verify assignment digest, cwd, branch, head, common directory, and clean
   starting state.
2. Read the immutable incoming handoff and applicable repository instructions.
3. Implement only the assigned task and declared write claims.
4. Maintain heartbeat and bounded execution state.
5. Run task validation and `code-review`.
6. Invoke `$commit` exactly once inside the assigned worktree.
7. Publish immutable result evidence with commit/tree/path/validation digests
   and a typed `spec_gaps` list. A non-empty list returns `REPLAN_REQUIRED`.

Workers must not touch the primary checkout, persistent lane, integration
worktree, other workers, canonical specs, shared coordinator files, Git
maintenance, external systems, or undeclared paths. A worker proposes a spec
gap with kind, summary, evidence, requirement IDs, and design IDs; only the
root coordinator decides whether to reconcile it.

## Coordinator Verification

Before accepting a result, independently prove:

- assignment and result schemas/digests;
- worker session ownership and terminal state;
- registered worktree/branch/common-directory identity;
- commit ancestry and exact parent;
- commit tree and changed path set;
- every path is within immutable claims and outside coordinator ownership;
- validation and review evidence is bound to the exact commit.

A worker may truthfully return `REPLAN_REQUIRED`. Preserve its evidence and
resource unless an exact clean no-op or journaled tracked archive satisfies the
documented recovery contract. Never infer success from prose.

## Integration and Promotion

1. Merge accepted worker commits into the integration branch in stable task-ID
   order using `git merge --no-ff --no-edit`.
2. Run combined validation and integration `code-review` at the exact merged
   head.
3. Reconcile queued steering and worker `spec_gaps`. Product fixes become
   isolated correction tasks; coordinator-only docs/spec changes use the
   canonical paired publisher and journaled coordinator commit.
4. Remove only clean verified worker worktrees and refs using exact expected
   paths and SHAs.
5. Under the shared Git lock, revalidate the lane at its expected base and
   fast-forward to the exact integration head.
6. Mark tasks done only after promotion postconditions pass.
7. Reconcile workflow-owned prompt-impact state.
8. Remove the exact integration worktree, then its exact ref. Record and retain
   anything that cannot be proven safe to remove.

Historical lifecycle state is not consulted at dispatch, promotion, cleanup,
or finalization. Canonical spec evidence is checked at plan/assignment and
post-implementation reconciliation boundaries. There is no terminal lifecycle
overlay or project-instruction mutation.

## Correction Waves

At a proven safe boundary, a correction plan may replace only the resource-free
future tail or append a ready correction frontier to a retained
`promotion_pending` wave. Completed waves and accepted worker commits remain
immutable. Dependent corrections wait for their predecessors.

Replanning uses a new plan identity and exact current prompt-impact basis. It
never edits a locked plan in place or reuses a lifecycle reconciliation as
authorization.

## Resume and Recovery

Coordinator-v7, task-plane, journal, Git, Worktree, lease, and interop state are
authoritative. Handoff Markdown is only a human projection after coordinator
creation.

- Replays consume recorded canonical arguments.
- An observed exact effect is completed without repeating the mutation.
- A stale token fails before effect.
- Fresh workers return `wait`; expired or ambiguous ownership requires
  confirmation.
- Dirty, divergent, missing, malformed, or unknown-writer resources remain
  blocked and retained.
- Coordinator v1 through v6 are unsupported; do not migrate or shim them.
- Historical lifecycle artifacts are ignored for active decisions.

## Finalization

After every wave is `done` and all internal resources are absent:

1. Verify the lane branch, clean final promoted head, prompt-impact plan basis,
   and summary inputs twice.
2. Run changed-surface `$align`.
3. Bind the bounded alignment evidence to the private finalization transition.
4. Seal the deterministic run summary and release the lane generation.
5. Update the handoff projection and activate the unchanged queue head.

No lifecycle receipt, project-instruction receipt, reload flag, or Stop hook is
required. An interrupted finalization resumes from its own private intent and
prepared summary evidence.

## Source Integration

Public `integrate` requires no active generation, contiguous released
generation receipts, exact clean source and lane identities, and no Git
operation in progress. When histories differ, build one exact two-parent
candidate, run combined validation/review, and advance only the recorded source
ref through expected-old compare-and-set. Never push or open a PR.

## Forbidden Recovery

Never use reset, stash, broad clean, force removal, force ref deletion,
cherry-pick, rebase, squash, broad prune, GC, submodule initialization,
permission widening, or external mutation to make evidence appear healthy.
