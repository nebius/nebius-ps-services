# Task Implementer

`task-implementer` is an explicit-only brownfield implementation coordinator
with private file-first intake. Users initialize a project folder once, edit
durable Markdown asks outside Git, and repeatedly run the same prompt. Internal
revisions, steering dispositions, queues, IDs, retries, and handoffs remain
private. Lightweight requirements and task designs are committed in managed
project-document regions.

## Two-Command Workflow

From the project folder:

```text
$task-implementer workspace init
```

Or provide the same folder explicitly:

```text
$task-implementer workspace init /path/to/project-folder
```

Initialization creates or verifies the private `CODE` + `PROMPTS` workspace,
creates one starter prompt only when none exists, opens VS Code when available,
and prints workspace paths plus prompts newest-first by accepted submission.
It is idempotent and never deletes, duplicates, renames, rewrites, or touches
existing prompts or run history.

Edit the starter in VS Code, copy its path from Explorer, and run:

```text
$task-implementer run <prompt-path-or-unique-filename>
```

The first invocation snapshots the prompt, extracts stable `TI-REQ-nnn`
requirements, and constructs the internal `task-1..task-n` queue. The Skill
then claims exactly `task-1` in a private
execution plane, completes and locks its plan before product edits, implements
its just-in-time `TI-DES-nnn` design and managed specification updates through
validation, `code-review`, scoped fixes, and `$commit`, verifies the checkpoint
and next-session handoff, and stops. Each fresh session repeats the same command
to claim, plan, and implement exactly one next task.

The execution plane persists exclusive task ownership after the filesystem
transition lock is released. It records only hashes of runtime-provided
`CODEX_THREAD_ID`, binds the clean claim-time worktree plus authorized plan and
queue, and verifies exact changed-path/commit evidence at checkpoint. Every
session that participated in a task is retired from all other tasks in the
scope. The fingerprint is a cooperative correlation guard, not a cryptographic
identity. Each task gets exactly one post-claim commit and an immutable stopped
checkpoint digest; a new session always begins the next task back in planning.

To steer work, edit the same prompt—prefer its optional `## Steering`
section—and repeat the same command. There is no public `steer` action and no
user-supplied ID. A same-session clean planning task can replan immediately;
another owner's planning task or any implementation task keeps its locked plane
unchanged and returns `STEERING_QUEUED_AFTER_TASK`. A fresh session reconciles
ordered pending revisions after the checkpoint. Contradictory or ambiguous
edits stop before repository changes. An unchanged completed prompt returns
`ALREADY_COMPLETE`; an edited completed prompt starts a new internal run.

After authorization, the Skill creates or incrementally updates only marked
regions in `<project>/docs/requirements.md` and `<project>/docs/design.md`.
Existing content outside the markers is byte-preserved. Agentic SDLC ownership,
malformed markers, unsafe paths, invalid IDs/mappings, or envelope drift fail
closed before product edits. Specification updates share the affected task's
single commit; there is no extra spec commit.

## Prompt Ordering

Prompt filenames stay stable. The filename date is creation metadata, and the
workflow never renames or deliberately touches a prompt to reorder it. Every
validated, lock-acquired run records private `last_invoked_at` activity. Both
commands display prompts newest-first by that activity, with creation time and
path as deterministic fallbacks. VS Code Explorer keeps its normal filename
ordering.

Output contains only last invocation, status, title, and path. It never prints
prompt bodies or requires users to copy internal prompt IDs, run IDs, revisions,
manifests, or handoff paths.

## Editor Convenience

The generated workspace lists `CODE` first and `PROMPTS` second. Its manual
`Task Implementer: New Prompt` task creates additional managed prompts. It does
not start Codex or submit content. There is no public workspace-creation action
beyond initialization.

## Private Storage

Workspaces live under
`${CODEX_HOME:-$HOME/.codex}/task-implementer/projects/`, keyed by canonical Git
root and exact project scope. They are never stored in the repository, even as
ignored files.

On POSIX systems, managed directories are `0700` and files are `0600`. SHA-256
detects drift but does not encrypt content. Prompt files must not contain
credentials, secrets, customer data, or confidential copied material.

## Files

- `SKILL.md`: explicit two-action routing and one-task implementation loop.
- `agents/openai.yaml`: UI metadata and explicit-only invocation policy.
- `references/prompt-workspace.md`: storage, validation, activity, routing,
  retry, failure, and sandbox contracts.
- `references/implementation-loop.md`: queue, reconciliation, interruption,
  per-task gates, handoff, and fresh-session behavior.
- `assets/prompt-template.md`: one-ask Markdown template.
- `assets/handoff-template.md`: private queue and checkpoint template.
- `assets/*-managed-region.md`: compact committed specification templates.
- `scripts/prompt_workspace.py`: internal mechanical CLI and redacted output.
- `scripts/prompt_workspace_core.py`: workspace and prompt validation core.
- `scripts/prompt_workspace_intake.py`: two-command run routing and activity
  transition ownership.
- `scripts/prompt_workspace_execution.py`: task claim, planning authorization,
  checkpoint, recovery, and fresh-session enforcement.
- `scripts/prompt_workspace_specs.py`: steering dispositions plus managed
  specification marker, ID, mapping, ownership, and envelope validation.
- `scripts/prompt_workspace_runs.py`: snapshots, manifests, handoffs, locks,
  verification, and prompt metadata.
- `scripts/test-prompt-workspace.py`: disposable functional tests.
- `scripts/test-task-execution.py`: execution-plane and session-boundary tests.
- `scripts/test-task-specs.py`: steering-ledger and managed-document tests.
- `scripts/test-task-implementer-contract.py`: cross-file contract smoke.
- `evals/trigger-prompts.md`: explicit trigger and non-trigger examples.

## Boundaries

- The helper creates and routes private state, claims dependency-ready tasks,
  locks plans, and validates checkpoints, but never decomposes tasks, starts
  Codex, edits product code, or prints prompt bodies.
- Only the two Skill actions above are public; helper transitions and IDs are
  private implementation details.
- The Skill is for complex sequential brownfield work, not ordinary one-shot
  implementation or Agentic SDLC.
- Do not run parallel write-capable sessions in one scope.
- Use `$align` after the final task and `$sdlc-start` for Agentic SDLC.
