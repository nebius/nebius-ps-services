# Worktree

`worktree` creates full-repository linked worktrees for parallel monorepo work
and integrates each committed child locally into the current non-default source
branch. Child branches are never pushed or used for pull requests.

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
never symlinks.

`--project` selects the returned starting directory; it is not a sparse
checkout, branch argument, staging boundary, or changed-path restriction.

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
- Automatic child commits apply only to ordinary children. Any Task Implementer
  or Agentic SDLC lease participation binds an exact child head, so a dirty
  nested/coordinated child blocks and returns to its owning workflow.
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

Task Implementer uses a different private consumer boundary: Worktree owns its
persistent per-project lane creation, monotonic generation leases,
repository-wide claims, two-parent source integration, rearm, and removal
primitives. Lane state and immutable generation receipts are separate from the
general schema-v4 manifest, so ordinary public Worktree children remain
compatible. Ordinary Task/Agentic coordinator lease acquisition also rejects a
Task lane before mutation; workflows must use their own matching consumer
boundary rather than nest or share execution state. The branch's `lane_id`,
`source_ref`, and `incarnation` fields are one indivisible identity. Partial
metadata fails closed, and a live lane whose branch or worktree matches but
whose branch metadata is absent cannot fall through to ordinary managed-child
classification. Public `$worktree integrate` and `$worktree remove` reject Task
lanes; only `$task-implementer integrate` and `$task-implementer workspace
remove` may drive those private lifecycle transitions.

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
