# Task Implementer

`task-implementer` is an explicit-only brownfield implementation coordinator
with private file-first intake. It lets a user keep many durable Markdown asks
outside Git, prepare an exact immutable revision into a reviewable task queue,
and implement that queue one task per fresh Codex session.

The ownership model is deliberately small:

```text
editable prompt with stable prompt_id
    -> historical runs
        -> immutable revisions
            -> one bound execution revision
                -> one handoff queue with task-1..task-n
```

One prompt represents one independent ask, not one project and not one task.
One ask may decompose into many implementation tasks. The handoff, not separate
task files, owns queue status and checkpoints.

## Typical Workflow

```text
$task-implementer workspace init services/nebius-cxcli
$task-implementer workspace new "Add prompt workspace support"
```

Edit the generated prompt in the saved `CODE` + `PROMPTS` VS Code workspace,
then submit it:

```text
$task-implementer prepare <private-prompt-path>
```

`prepare` validates and snapshots the prompt, inspects the repository, creates
a reviewable handoff queue, and stops without product edits. Approval is an
explicit second action:

```text
$task-implementer run <run-id>
```

Each implementation session performs exactly one task through context,
brainstorm/design/plan, implementation, validation, `code-review`, fixes, and
`$commit`, saves the checkpoint, and stops. The next fresh session uses:

```text
$task-implementer continue <run-id>
```

If the editable prompt changes during an unfinished run:

```text
$task-implementer reconcile <run-id> <private-prompt-path>
```

Reconciliation appends a revision, preserves completed work and stable task
IDs, proposes superseding or additive queue changes, and stops without product
edits.

## Private Storage

Workspaces live under
`${CODEX_HOME:-$HOME/.codex}/task-implementer/projects/`, keyed by canonical
Git root and repository scope. They are never stored in the repository, even
as ignored files. A generated VS Code workspace lists `CODE` first and
`PROMPTS` second; this is editor convenience rather than a Codex dependency.

On POSIX systems, managed directories are `0700` and files are `0600`. SHA-256
detects drift but does not encrypt content. Prompt files must not contain
credentials, secrets, customer data, or confidential copied material.

## Files

- `SKILL.md`: explicit action routing and the sequential implementation loop.
- `agents/openai.yaml`: UI metadata and explicit-only invocation policy.
- `references/prompt-workspace.md`: storage, prompt, lifecycle, resubmission,
  failure, and sandbox contracts.
- `references/implementation-loop.md`: queue construction, per-task gates,
  reconciliation, handoff discipline, and fresh-session patterns.
- `assets/prompt-template.md`: one-ask Markdown template.
- `assets/handoff-template.md`: private queue and checkpoint template.
- `scripts/prompt_workspace.py`: standard-library-only mechanical helper.
- `scripts/prompt_workspace_core.py`: workspace and prompt validation core.
- `scripts/prompt_workspace_runs.py`: run, revision, drift, and listing logic.
- `scripts/test-prompt-workspace.py`: disposable-repository functional tests.
- `scripts/test-task-implementer-contract.py`: cross-file workflow smoke test.
- `evals/trigger-prompts.md`: explicit trigger and non-trigger examples.

## Boundaries

- The helper creates private state but never decomposes tasks, starts Codex,
  edits product code, or prints prompt bodies.
- `prepare` and `reconcile` are planning-only and stop before implementation.
- `run` and `continue` read immutable snapshots, never editable source prompts.
- The Skill is for complex sequential brownfield work, not ordinary one-shot
  implementation or Agentic SDLC.
- `global-context-management` may support context hygiene but must not invoke
  this workflow implicitly.
- Do not run parallel write-capable agents in one scope.
- Use `$align` after the final task; use `$sdlc-start` for Agentic SDLC.
