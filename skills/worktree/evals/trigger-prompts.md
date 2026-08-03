# Worktree Trigger Prompts

## Should Trigger

- `$worktree Create an isolated worktree for the current skills project to fix
  trigger validation.`
- `$worktree add --project services/example-api prepare the migration report
  changes.`
- `$worktree add --project services/example-api --reuse
  project-migration-report-a7c2f9 and preserve its unfinished changes.`
- `$worktree push Commit the managed worktree changes as "fix(skills): tighten
  trigger validation" and push them.`
- `$worktree create-pr Open or reuse the PR for this managed worktree with title
  "Harden worktree cleanup".`
- `$worktree remove project-fix-trigger-validation-a7c2f9 after verifying its PR
  was merged.`

## Should Not Trigger

- `Create a normal local commit for the current branch without pushing.`
- `Push this existing feature branch and open a PR.`
- `Use multiple agents and worktrees to implement these dependency waves.`
- `Explain how git worktree differs from cloning a repository.`
- `Review the current pull request and tell me whether it is ready to merge.`

## Expected Boundary

- The skill requires an explicit `$worktree` invocation.
- No action means `add`; no compatibility aliases are accepted.
- An existing active scope/task lifecycle blocks duplicate creation and is
  reusable only by its exact generated name.
- `push` and `create-pr` compose with their existing sibling skills only after
  an action-bound private publication reservation plus managed identity and
  project-scope checks.
- A nested `task-implementer` run owns the outer branch until its internal
  resources are cleaned and final alignment releases the lease; while active,
  `push`, `create-pr`, and `remove` fail closed.
- Repeating an interrupted publication action resumes its reservation; a
  different action cannot clear or replace it.
- `remove` does not merge PRs or abandon unproved work.
