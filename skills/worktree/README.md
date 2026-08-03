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

Integration creates a durable private merge candidate from the current source
head, merges the exact clean child head with `--no-ff`, retains conflicts for
developer resolution, and exposes the exact candidate for non-mutating
alignment/tests. Only that validated SHA may fast-forward the clean checked-out
source branch. Cleanup remains a separate proof-gated action.

Task Implementer and Agentic SDLC may use private nested worktrees inside one
child. Their lease blocks outer integration until internal promotion, cleanup,
alignment, and evidence gates finish. Managed Agentic SDLC remains pending
until exact source-integration proof is recorded. Ownership-manifest schema v4
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
