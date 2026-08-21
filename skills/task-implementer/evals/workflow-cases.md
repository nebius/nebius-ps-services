# Supplemental Workflow Cases

`task-implementer` is explicit-only because it creates private state, isolated
Git worktrees/branches, worker commits, and coordinator integration commits.
Generic parallel requests do not trigger it. `trigger-prompts.csv` is the sole
canonical trigger authority; these post-routing cases retain detailed workflow
expectations. Contract tests remain required for lifecycle and output
assertions; canonical CSV validation does not replace them.

## Post-Routing Workflow Expectations

Canonical routing rows `task-implementer-positive-help` and
`task-implementer-positive-help-short`:

```text
$task-implementer --help
$task-implementer -h
```

Return concise, report-only help with purpose, explicit invocation policy,
exactly five workflow actions (`workspace init`, `workspace reuse`, `run`,
`integrate`, and `workspace remove`), their arguments, and
`-h, --help`, then stop. After the selected `SKILL.md` loads, do not initialize
a workspace, inspect the project, call additional tools, change private state,
or start any workflow action.

```text
$task-implementer workspace init
```

Initialize the exact current project scope, create one starter only when none
exists, and do not implement work.

The preceding assertion applies after canonical routing row
`task-implementer-positive-init-current`.

```text
$task-implementer workspace init services/nebius-cxcli
```

Resolve the monorepo scope while keeping every future worker worktree a full
repository checkout. Create or reuse the persistent lane from committed source
`HEAD`; source-checkout dirt is allowed and excluded.

The preceding assertion applies after canonical routing row
`task-implementer-positive-init-project`.

```text
$task-implementer workspace reuse services/nebius-cxcli
```

From either the primary source project or its owning managed lane, resolve and
open the exact existing workspace. Permit dirty or active live lane state
without refreshing, repairing, checkpointing, migrating, queuing, or running
anything. Missing or mismatched state fails closed.

The preceding assertion applies after canonical routing row
`task-implementer-positive-reuse`.

```text
$task-implementer integrate services/nebius-cxcli
```

Require a clean source checkout and lane, no active generation, and a
contiguous pending range. Validate one exact two-parent candidate, promote it
with expected-old source-head proof, consume every pending generation, release
their claims, and rearm the same lane.

The preceding assertion applies after canonical routing row
`task-implementer-positive-integrate`.

```text
$task-implementer workspace remove services/nebius-cxcli
```

Remove only an idle, clean, fully integrated lane with exact reachability proof.
Preserve private prompt/run history and make repeated removal idempotent.

The preceding assertion applies after canonical routing row
`task-implementer-positive-remove`.

```text
$task-implementer run 2026-07-12_1430--add-retries.md
```

Resolve the unique managed prompt, plan all tasks and deterministic dependency
waves, then coordinate every wave until done or blocked. Keep all internal IDs,
branches, paths, and transitions private.

The preceding assertion and all remaining `run` scenarios apply after
canonical routing row `task-implementer-positive-run`. Their placeholders
describe post-routing state fixtures, not additional trigger-eval prompts.

```text
$task-implementer run <prompt-with-five-disjoint-tasks-then-one-dependent-task>
```

Put the five completely disjoint tasks in one logical wave, dispatch them in
capacity-sized batches to isolated full-repository worktrees, and place the
dependent task in the next wave. Capacity must not change logical waves.

```text
$task-implementer run <prompt-with-two-tasks-that-touch-the-same-lockfile>
```

Combine the tasks before IDs lock when they form one coherent result;
otherwise add a dependency and serialize them. Never dispatch overlapping
dependency-file writers.

```text
$task-implementer run <prompt-with-distinct-kubernetes-resources-and-live-apply>
```

The code/config edits may be analyzed by resource identity, but live external
Kubernetes mutation remains a singleton conflict domain and needs separate
explicit authority.

```text
$task-implementer run <same-prompt-after-appending-a-Steering-note>
```

Recompute a merely planned wave safely. If assignments or integration already
started, preserve the immutable active wave and return
`STEERING_QUEUED_AFTER_WAVE`; reconcile before promotion or at the next wave
boundary.

```text
$task-implementer run <same-prompt-after-a-worker-crash>
```

Re-observe coordinator/wave state, journals, refs, and worktrees. Retain exact
resources, do not dispatch new batch members after failure, and resume without
duplicating branches, worktrees, assignments, commits, or merges.

```text
$task-implementer run <managed-prompt-while-the-source-checkout-is-dirty>
```

Use the existing clean persistent lane and leave source dirt untouched. Acquire
the next exact generation, enforce repository-wide claims, and promote internal
waves only to the lane branch. Never fetch, use a remote default, or copy dirty
source files. Dirty source state is not part of the run baseline.

```text
$task-implementer run <same-completed-prompt-after-an-interrupted-final-release>
```

Resume the private finalizer and release the existing generation only after
re-observing the final promoted head and absent internal resources. Do not
start a new generation, expire the lease, or force-clear state.

```text
$task-implementer run <same-prompt-after-an-integration-conflict>
```

Confirm the persistent lane remains at the recorded base, retain integration
and worker resources, and return `INTEGRATION_CONFLICT`. Do not partially merge
the wave into the project branch.

```text
$task-implementer run <same-completed-prompt-file>
```

For unchanged content, record activity and return `ALREADY_COMPLETE`. For
edited content, start a new internal run.

## Rejected Interface Assertions

Canonical rows `task-implementer-negative-unsupported-01` through
`task-implementer-negative-unsupported-11` are the sole routing cases for
unsupported public actions. Reject them without translating them to hidden
aliases, then explain the five-action interface without exposing internal IDs.

Canonical adjacent-boundary rows route generic parallel work to ordinary
authorized delegation, prompt wording to editing or brainstorming, one-shot
implementation to the normal implementation flow, context management to
`global-context-management`, review to `code-review`, commit or PR work to its
matching Git workflow, and Agentic SDLC work to prompt-bound `$sdlc-start run`.
