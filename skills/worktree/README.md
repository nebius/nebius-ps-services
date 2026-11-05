# Worktree

`worktree` creates full-repository linked worktrees for parallel monorepo work
and integrates each committed child locally into the current non-default source
branch. Child branches are never pushed or used for pull requests.

Both coordinators use the shared stop-before-repair worker boundary. An
unexpected failed check or blocking review finding returns preserved evidence
and a blocked result before any repair or rerun. Only negative outcomes declared
in immutable assignment validation before execution are expected feedback.
Required safe teardown and lease release remain allowed. The owning coordinator
classifies and authorizes recovery or replanning; workers cannot relabel failures
or erase them with a later successful check.

Before actions with known access prerequisites, both worker envelopes direct
agents to supported tools and native approval mechanisms. An approval request
does not grant access, a denial remains a policy/authority stop, and an
unexpected executed failure still requires the blocked handoff. Recovery uses
the existing shared engine with a stopped predecessor, released runtime,
unchanged assignment and fresh identity; it never grants permission itself.

## Public Actions

```text
$worktree [add] [<task>] [--project <directory>] [--reuse <exact-name>]
$worktree integrate <exact-name> [--restart]
$worktree remove <exact-name>
```

Creation requires the complete primary checkout to be clean. It captures the
exact current local feature-branch SHA and uses:

```text
git worktree add --no-track -b <generated-child> <path> <captured-source-sha>
```

The helper completes a nonmutating preflight before it creates or locks private
state, then repeats the same checks under the lifecycle lock. The sibling
worktree parent and `.worktree-skill` directory must be canonical directories,
never symlinks. Concurrent first-use directory creation accepts another
creator's canonical directory, then revalidates it before acquiring the
lifecycle lock. Files and symlinks remain errors; selection and activation
still run under the same lifecycle lock.

Commit preparation and Worktree ownership transitions share the selected
agent's repository lock under its private configuration home. Claude uses
`CLAUDE_CONFIG_DIR` (default `~/.claude`); Codex uses `CODEX_HOME` (default
`~/.codex`). Standalone script installation includes the shared runtime;
install the full catalog for workflows that call sibling skills.

Codex and Claude are used independently. This lock coordinates callers within
one selected installation; it does not provide cross-agent synchronization or
workflow handoff.

Implement uses the shared private `prepare-specs` transition to allocate its
integration checkout before initial specification publication. The following
`prepare` journals and commits only the published canonical pair, then freezes
execution and creates worker waves. This preserves the accepted source and
keeps initial spec changes out of uncommitted worker inputs. These are internal
workflow transitions, not additional public Worktree actions.
SDLC's `prepare` owns the initial source contract commit after objective impact
and plan validation. It admits only the published canonical pair and exact
validated document inputs, rejects unrelated changes, and journals the parent,
branch, objective, plan, file hashes and tree before committing. Recovery accepts
only the matching clean direct child. Managed SDLC intake acquires ownership
before authoring; preparation records the contract head in that lease before
creating integration. Clean sources must match accepted or completed workflow
checkpoints; arbitrary commits cannot substitute for this transaction.

The transition helper consistently selects the private execution with
`--execution`, including worker reconciliation. Ordinary feature executions
and document delivery slices use the same argument; it does not change task
feature membership or coordinator authorization.
For a proven active-task design defect, private `prepare-design-revision` consumes
the canonical classifier's active counted dispatch, exact native terminations
and released leases. It retains the unmerged failed wave before the semantic
owner publishes a corrected pair. Private `seal-design-revision` seals only that
owner-validated pair, journals the exact parent/tree, and safely resumes an
interrupted commit. Fresh impact and replanning then create new assignments;
old failures and worker artifacts remain historical evidence. See the
[execution-plane recovery sequence](../sdlc-prepare-execution/references/execution-plane.md).
The shared plan parser preserves multiline task contracts and ownership lists,
and rejects empty fields, duplicate fields and malformed task headings before
preparation. Implement and SDLC consume this same parser.

`--project` selects the returned starting directory; it is not a sparse
checkout, branch argument, staging boundary, or changed-path restriction.

Both execution coordinators use one five-minute startup window from arming to
`task-start`, followed by the existing one-hour work limit. Prepare the handoff
before arming and spawn immediately afterward. Repeated arming never renews time;
new policy requires fresh assignments after prior writers are quiescent.

When `<task>` is omitted, `add` normalizes the resolved project directory's
basename into the task slug. For example, invoking `$worktree` from `skills/`
creates `project-skills-<6-hex>` on branch `feature/skills-<6-hex>` under the
sibling worktree parent and returns `<worktree>/skills` as the starting
directory. An explicit task keeps using its derived public-safe slug; a
basename that normalizes to empty falls back to `work`.

After `add` or exact `--reuse`, Codex verifies the child from that returned
directory and uses it as the working directory for subsequent development
commands. This is command routing, not a promise that a subprocess changed the
parent shell, Codex workspace, or editor window. Opening or retargeting an
editor remains an explicit user action; integration and removal still run from
the primary checkout.

## Parallel Development, Serial Integration

After creation, the source branch and its children may advance independently
until integration starts. Later source commits do not appear automatically in
existing children, and exact `--reuse` never rebases or refreshes a child.

```text
<source-branch> advances independently
           +
child A / child B / child C advance independently
           |
           v
integrate each child serially into <source-branch>
           |
           v
publish the accumulated <source-branch> once
```

Run `$worktree integrate <exact-name>` from the primary checkout. A fresh
integration follows one canonical order:

```text
primary checkout on <source-branch>
           |
           v
read-only child and source preflight
           |
           v
safely commit eligible ordinary child dirt
           |
           v
safely commit eligible source dirt
           |
           v
revalidate exact clean source and child heads
           |
           v
create and validate the private candidate
           |
           v
fast-forward <source-branch> to that candidate
```

Use these boundaries:

- `add` still requires a clean primary checkout. A fresh `integrate` may create
  one whole-repository local child commit and then one source commit when the
  complete diffs are coherent and safe, no Git operation or conflict exists,
  and no integration reservation or orphan candidate exists.
- Automatic integration commits apply only to ordinary children. Implement and
  Agentic SDLC execute committed history through shared private workflow
  transactions. Active coordinator leases keep nested dirt with its owner.
- Each automatic commit uses repo-root `git add -A`, a truthful message, and
  normal hooks. The reviewed staged tree is bound to the resulting commit tree;
  hook-added content requires complete actual-commit review before integration.
  A durable source-scoped preparation claim blocks competing integration,
  nested lease acquisition, removal, and publication while ordered commits are
  created. A successful commit is retained if a later step fails; retry uses
  the same preparation evidence instead of resetting or duplicating it.
- Worktree ownership transitions and the direct `$commit` transaction share
  one Codex-private lock derived from the Git common directory. An active
  Worktree preparation or reservation for the source ref blocks direct commit;
  neither workflow adopts or rewrites the other's claim.
- The final preflight freezes exact clean source and child SHAs. Candidate
  creation compares those SHAs and the private preparation token again while
  atomically consuming the claim into its durable reservation.
- If an interrupted handoff leaves both the exact preparation and its
  reservation, a token-bound retry validates both records and consumes the
  preparation before candidate work resumes.
- Integrate one child at a time. The next child therefore starts from all
  source commits and previously integrated children.
- Once an integration attempt starts, keep both source and child heads stable.
  Neither checkout is auto-committed during resume, validation, conflict
  recovery, or restart. Source movement makes the retained attempt stale and
  requires an explicit reviewed `--restart`; child movement also fails closed.
- Explicitly aborting only a preparation claim preserves all Git commits and
  requires a fresh preflight. Orphan candidate branches, paths, symlinks, and
  registered worktrees always block instead of being adopted.
- Resolve merge conflicts only in the returned recovery candidate, then repeat
  the same integration command. Never resolve them in the primary or child
  checkout.
- After all children are integrated, publish only the accumulated source
  branch. Remove each child separately after its exact local integration proof
  is recorded.

Owned SDLC verification profiles propagate through coordinator Git, linked
workers, native dispatch and qualification subprocesses. The shared validator
rechecks exact repository membership and fixed configuration before Git use;
existing task leases, registration and promotion gates remain authoritative.
Ordinary worktrees without a verifier profile keep their existing behavior.

## VS Code Worktree Discovery

VS Code users can enable automatic Git worktree discovery in their user or
workspace settings:

```json
{
  "git.detectWorktrees": true
}
```

VS Code then lists detected worktrees in the Source Control Repositories view,
where each worktree can be opened in the current or a new window. This optional
editor setting is separate from the skill: `worktree` does not change VS Code
settings or open an editor. See [Git branches and worktrees in VS Code][1].

[1]: https://code.visualstudio.com/docs/sourcecontrol/branches-worktrees

After the guarded commit phase, integration creates a durable private merge
candidate from the exact preflight source head, merges the exact child head with
`--no-ff`, retains conflicts for developer resolution, and exposes the exact
candidate for non-mutating alignment/tests. Only that validated SHA may
fast-forward the clean checked-out source branch. Cleanup remains a separate
proof-gated action.

Agentic SDLC may use private nested worktrees inside one child. Its lease blocks
outer integration until internal promotion, cleanup, alignment, and evidence
gates finish. Managed Agentic SDLC remains pending
until exact source-integration proof is recorded. At that boundary the caller
returns the recorded primary path plus the exact
`$worktree integrate <generated-name>` command and stops; only a fresh explicit
user invocation from that primary checkout may start outer integration. A
workflow continuation or recorded next skill must not invoke it automatically.
Ownership-manifest schema v4
records whether a nested lease is `active` or `released` plus its exact owner
and token, so a missing lease record cannot silently unlock the outer lifecycle.
Lease schema v4 retains ordered promotion heads and persists an exact
`released` terminal receipt; replay is accepted only when owner, token,
promoted head, clean outer checkout, and absent resources still agree.
Integration and removal rebind the receipt to the exact manifest and live outer
identity and rescan all private resources, so receipt tampering or resurrection
cannot cross the outer lifecycle boundary.
Removal keeps an exact private removal-intent snapshot until receipt, manifest,
and resource revalidation finish; interruption at either deletion boundary is
therefore retryable without losing the receipt compare-and-set anchor.

Implement and Agentic SDLC use the shared workflow execution substrate documented
in [workflow-contract.md](references/workflow-contract.md). Worker launch envelopes,
strict native-result ingestion and immutable per-attempt receipts share this
owner across parallel and sequential adapters. Blocked or replan results stop
new dispatch while active peers drain; recovery preserves historical receipts.
Accepted objectives,
immutable bases, fresh task worktrees, slot refill, result admission and delivery
share these Git lifecycle primitives. Execution bases also bind mandatory workflow
references, supporting owner instructions, and the worker result schema, so
contract drift is rejected before task dispatch. Obsolete persistent-lane state is rejected
and retained without conversion or cleanup.

Native worker output uses a provider-supported schema. Local result validation
enforces status-dependent spec gaps and unique typed requirement/design IDs.
Launch failures retain the native exit code and a fixed schema-rejection label
when recognized; raw native error output is never reflected in the result.
Rejected structured results retain a fixed reason identifying the failed guard,
including status/spec-gap mismatches. Diagnostics never echo rejected values or
unknown field names, and invalid results remain `WORKER_FAILED`.

Native `task-arm` and `authorize-recovery` validate the selected home, registered
project/run, canonical assignment and current coordinator binding before
admission. The shared launch envelope declares the project-state directory and,
for runtime tasks or recovery, the shared runtime-lease directory. Both host
adapters add these directories without changing sandbox mode or approval policy;
the sequential adapter completes admission before spawning. Unavailable host
capability blocks launch. The owner prepares missing lease storage after checks.
These grants include sibling project runs and shared lease records; helper
ownership and write-claim checks enforce finer authority, not OS isolation
between records. No grant permits unrelated state edits.

A zero-exit worker still awaiting `task-start` may return a screened blocked or
replan diagnostic as `prestart_result`. Input and canonical result are each
limited to 64 KiB. This remains `WORKER_FAILED`, stops later assignments and
grants no result receipt, completion, recovery or replanning authority. Invalid,
oversized or implemented output yields only a fixed reason; terminal liveness
failures retain precedence.

Workers can return `blocked` with empty spec gaps when a non-specification failure
leaves work or validation incomplete. After full validation, the dispatcher
returns `WORKER_BLOCKED` with the validated `worker_result` and exit 2, stopping
later assignments. The coordinator owns diagnosis after observed termination;
blocked evidence never enters task completion, admission or integration. Workers
must report unperformed checks honestly instead of inventing a specification gap.

Push and PR guards classify the primary and every linked checkout against Git
metadata, ownership manifests, integration reservations, and nested lease
resources.
Private candidates/workers, partial or malformed ownership, and resurrected
absent resources fail closed; only the primary source and genuinely unmanaged
manual worktrees may publish.

Private state lives beneath the sibling worktree parent. Old state schemas are
rejected without migration. The helper never fetches to choose a base, pushes,
creates PRs, deletes remote refs, force-removes worktrees, rebases, or
cherry-picks.
