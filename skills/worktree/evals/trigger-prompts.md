# Worktree Trigger Prompts

## Should Trigger

- `$worktree`
- `$worktree Create an isolated worktree from my current clean feature branch
  for the skills project.`
- `$worktree add --project services/example-api`
- `$worktree add --project services/example-api prepare the migration report
  changes.`
- `$worktree add --project services/example-api --reuse
  project-migration-report-a7c2f9 and preserve its unfinished changes.`
- `$worktree integrate project-fix-trigger-validation-a7c2f9 after validating
  the combined result.`
- `$worktree integrate project-fix-trigger-validation-a7c2f9 from my primary
  checkout; safely commit its ordinary dirty child and then my dirty source
  before creating the candidate.`
- `$worktree remove project-fix-trigger-validation-a7c2f9 after its exact local
  merge is present on the source branch.`

## Should Not Trigger

- `Create a normal local commit for the current branch without pushing.`
- `Push this existing feature branch and open a PR.`
- `Use multiple agents and worktrees to implement these dependency waves.`
- `Agentic SDLC reached outer-integration-pending; continue automatically by
  calling worktree integrate for me.`
- `I am inside the managed child; silently integrate it into the source from
  here.`
- `Explain how git worktree differs from cloning a repository.`
- `Review the current pull request and tell me whether it is ready to merge.`

## Expected Boundary

- The skill requires an explicit `$worktree` invocation.
- A Task Implementer or Agentic SDLC handoff reports the exact command and
  stops; its coordinator, next-skill state, and Stop hook do not count as the
  user's explicit invocation.
- No action means `add`; no compatibility aliases are accepted.
- With no task description, `add` derives the task slug from the resolved
  project-directory basename, so invocation from `skills/` creates a
  `project-skills-<6-hex>` worktree and `feature/skills-<6-hex>` branch.
- `add` requires the complete primary checkout to be clean and captures the
  exact current named non-default local source branch and `HEAD` without fetch.
- `--project` selects the initial directory and label only; it is not a
  checkout, staging, or changed-path boundary.
- After `add` or exact `--reuse`, the agent sets subsequent development tool
  calls to the returned `scope_cwd` and re-observes the child branch there.
  It does not claim that a subprocess changed the parent shell, Codex workspace,
  or editor window, and it does not launch an editor implicitly.
- A nested Task Implementer or Agentic SDLC lease blocks outer integration and
  removal until final alignment and internal cleanup release it. Any recorded
  lease participation also makes later child dirt ineligible for auto-commit.
- `integrate` runs only from the primary checkout. Its read-only preflight may
  order one eligible ordinary child commit followed by one source commit before
  it freezes exact clean heads; active attempts and restart never auto-commit.
- The first delegated commit creates a durable source-scoped preparation claim.
  Competing reservations, preparations, nested lease acquisition, removal, and
  source publication block until exact candidate reservation or explicit claim
  abort. Orphan candidate resources always block.
- Each commit binds the reviewed staged tree to the resulting commit tree. A
  hook-modified tree requires complete actual-commit review and exact head/tree
  acknowledgement before candidate creation.
- A successful automatic commit remains local history if a later step fails;
  retry observes it instead of resetting or duplicating it.
- Direct managed-child push or PR creation is rejected; publication happens
  only from the accumulated source branch.
- `integrate` uses one durable private candidate, preserves conflict recovery,
  requires non-mutating combined validation, and advances the source only by
  ff-only promotion of the exact two-parent candidate.
- `remove` does not discard unproved, dirty, advanced, or rewritten work.
