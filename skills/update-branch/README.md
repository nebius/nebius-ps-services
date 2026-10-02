# Update Branch

`update-branch` merges the latest `origin` default branch into your current local
feature branch, preserving its commits and keeping you on the same branch.
It fast-forwards when possible and otherwise creates a merge commit.

```text
$update-branch
$update-branch --help
```

Use `/update-branch` in Claude Code or `/skills:update-branch` in the Claude
plugin. Invocation is explicit-only; help performs no repository operations.
There are no positional arguments or additional public flags.

The skill requires a clean ordinary primary checkout and existing `origin`
access. It stops on local edits, detached/default branches, unfinished Git
operations or a linked-worktree registration for the current branch. Git's
primary record is excluded; matching locked or prunable registrations still
block, as does running inside a linked checkout. The blocker names the branch
and registered path; failed inspection is reported separately.
Unrelated/detached worktrees, sibling container directories or symlinks, ancestry
and `worktreeSkill` metadata alone do not block, including stale current-branch
metadata. Active workflow restrictions on the current branch/checkout and explicit
repository-wide Git-write locks remain binding. The skill never repairs or
deletes ownership records, and rechecks the association before merging.
It discovers the live default branch instead of assuming `main`, fetches only
that branch and merges the fetched commit. It does not synchronize the feature
branch's own upstream, switch branches, rebase, stash or push.

If local changes block the update, review, stage, and commit your work, then rerun
`$update-branch` once the working directory is clean. Resolve dirty submodule work
inside the submodule first. The skill does not stage or commit that work for you.

Clear conflicts are resolved and checked before completing the merge. Ambiguous
choices are returned to you with the merge left pending. Hooks, signing and
ignored local files stay protected. Final branch, ancestry and cleanliness checks
separate a completed update from a pending or failed operation.

The runtime is instruction-only: [SKILL.md](SKILL.md) owns the workflow,
[agents/openai.yaml](agents/openai.yaml) owns Codex metadata, and `evals/` holds
development checks. No helper, custom state or GitHub API is required. Normal
updates use Git checks; conflict-resolution edits receive focused project tests.
The workflow assumes one Git writer and stops if it observes competing changes.

## Validation

From the skills catalog, run the structure validator for repository Codex and
Claude profiles with `--require-evals update-branch`, plus portable core and
plugin-catalog checks. Disposable Git fixtures exercise the command semantics;
trigger and quality definitions are separate from actual native behavior evidence.
Compare worktree eligibility output with captured pre-change working bytes
in a disposable baseline when an authenticated clean runner is available.
Never copy account credentials into evaluation homes or test on a real branch.

## Official references

- [Git fetch](https://git-scm.com/docs/git-fetch): explicit refspec and refmap behavior.
- [Git merge](https://git-scm.com/docs/git-merge): fast-forward, conflicts and safeguards.
- [Git ls-remote](https://git-scm.com/docs/git-ls-remote): live symbolic default discovery.
- [Git status](https://git-scm.com/docs/git-status): explicit untracked/submodule checks.
- [Git worktree](https://git-scm.com/docs/git-worktree): primary-first NUL records and branch associations.
