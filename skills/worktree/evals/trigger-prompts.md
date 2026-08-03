# Worktree Trigger Prompts

## Should Trigger

- `$worktree Create an isolated worktree from my current clean feature branch
  for the skills project.`
- `$worktree add --project services/example-api prepare the migration report
  changes.`
- `$worktree add --project services/example-api --reuse
  project-migration-report-a7c2f9 and preserve its unfinished changes.`
- `$worktree integrate project-fix-trigger-validation-a7c2f9 after validating
  the combined result.`
- `$worktree remove project-fix-trigger-validation-a7c2f9 after its exact local
  merge is present on the source branch.`

## Should Not Trigger

- `Create a normal local commit for the current branch without pushing.`
- `Push this existing feature branch and open a PR.`
- `Use multiple agents and worktrees to implement these dependency waves.`
- `Explain how git worktree differs from cloning a repository.`
- `Review the current pull request and tell me whether it is ready to merge.`

## Expected Boundary

- The skill requires an explicit `$worktree` invocation.
- No action means `add`; no compatibility aliases are accepted.
- `add` requires the complete primary checkout to be clean and captures the
  exact current named non-default local source branch and `HEAD` without fetch.
- `--project` selects the initial directory and label only; it is not a
  checkout, staging, or changed-path boundary.
- A nested Task Implementer or Agentic SDLC lease blocks outer integration and
  removal until final alignment and internal cleanup release it.
- Direct managed-child push or PR creation is rejected; publication happens
  only from the accumulated source branch.
- `integrate` uses one durable private candidate, preserves conflict recovery,
  requires non-mutating combined validation, and advances the source only by
  ff-only promotion of the exact two-parent candidate.
- `remove` does not discard unproved, dirty, advanced, or rewritten work.
