# Global Context Management

`global-context-management` is a reusable Codex skill plus optional local
runtime setup for keeping long or complex coding sessions focused. It reduces
context pollution by separating runtime policy, task-state persistence, and
read-only exploration roles when the current Codex surface exposes them and
the user explicitly authorizes delegation.

## Design Goal

The goal is not to make Codex read less important context. The goal is to make
Codex keep noisy context out of the parent thread while preserving the facts
that matter:

- objective and constraints
- current plan and decisions
- relevant files and symbols
- commands and validation status
- risks and next action

This skill is designed to be global and repo-independent. Public skill files
stay generic. Machine-local hooks, task-state files, and custom agent config
belong under `$CODEX_HOME`, which normally defaults to `$HOME/.codex`.
For full `$CODEX_HOME` bootstrapping, use `config-codex`; this skill documents
the runtime workflow that setup enables.

## Architecture

```text
User prompt
  |
  v
Global hooks inject workspace and task-state path
  |
  v
global-context-management skill defines the task workflow
  |
  v
Main agent owns planning, edits, validation, and final answer
  |
  +--> read-only repo_mapper, when explicitly authorized and useful
  +--> read-only test_strategist, when explicitly authorized and useful
  `--> read-only risk_reviewer, when explicitly authorized for risky work
```

### Hooks

Hooks run before the model starts or before a user prompt is submitted. They
provide durable context, not implementation logic.

They should say, in effect:

```text
Here is the workspace root.
Here is the durable task-state path.
Create task state automatically only for complex prompts.
Read current task state when it already exists and prior context may matter.
For complex work, use global-context-management.
Keep the parent thread concise.
```

The hooks use a session-scoped task-state path such as:

```text
$CODEX_HOME/task-state/<workspace>-<hash>/<session-id>/current.md
```

If no session ID is present, task state is unavailable for that hook event. No
manual or legacy fallback path is created.

They must not persist raw prompts, broad command output, stack traces, secrets,
customer data, private URLs, or broad environment dumps.

### Task-State Lifecycle

Task-state files are useful only when Codex reads and updates them. The
`SessionStart` hook injects the path without creating a missing `current.md`;
the `UserPromptSubmit` hook creates or reuses the file automatically only when
the prompt looks complex. Hooks do not make hidden state automatically active in
the model forever.
Synthetic complex-prompt hook probes that pass a made-up `session_id` against a
live `$CODEX_HOME` can create scaffold-only directories named after that session
ID. Those probe files are not active task context unless a real agent session
later reads and updates that same path.

Use the task-state file in three places:

1. At task start, resume, or after compaction, read the current file when prior
   decisions, validation status, or next action may affect the work.
2. During the task, update it with concise checkpoints after planning, major
   edits, validation, and risk review.
3. Before the final answer or a long pause, leave the latest status and next
   action so a later turn can continue without replaying raw logs.

Treat task state as a continuity note, not an unquestioned source of truth.
Verify facts that may have drifted, keep raw logs and secrets out of the file,
and prefer short decisions over copied command output. If the file is written
but never read on continuation, it becomes an audit trail rather than useful
context management.

### Skill

The skill is the runtime process contract. During a complex task it tells Codex
how to work:

```text
Understand the task.
Read current task state when continuity may matter.
Update task state.
Use targeted reads.
Delegate bounded read-only exploration when explicitly authorized, available,
and useful.
Plan the smallest coherent change.
Edit in focused patches.
Validate narrowly first.
Review risk before finalizing.
Update task state and summarize.
```

`SKILL.md` stays concise because it is loaded into the model context when the
skill is used. Detailed local setup lives in `references/local-setup.md`.

### Subagents

Subagents are optional read-only helpers. They are useful when exploration is
large enough that it would pollute the parent thread, the user explicitly asks
for delegation if the runtime requires it, and the current Codex surface
permits delegation.

- `repo_mapper`: maps relevant files, symbols, flows, and conventions.
- `test_strategist`: finds focused tests, fixtures, and validation order.
- `risk_reviewer`: checks near-final work for correctness, regressions,
  security, compatibility, edge cases, and missing tests.

Subagents inspect, summarize, and report. They do not edit code. The main agent
owns consolidation, implementation, final verification, and the final answer.
The main agent also owns cleanup: after spawning a helper, it should wait for
the final summary, fold the useful result back into the parent thread, and
close the completed subagent thread when close controls are available and no
follow-up is needed.
Do not spawn every configured role by default. Keep `max_threads = 4` as the
conservative local thread budget. Use `repo_mapper` and `test_strategist` early
only when their work is useful and independent, close them after consolidation,
then use `risk_reviewer` near the end only for non-trivial or risky changes.
When several helpers are running, the main agent should close each completed
handle as soon as its terminal result arrives, then keep waiting for the
remaining handles until all spawned helpers are closed.

Subagents are not a guaranteed visible button or separate UI in every surface.
They depend on the `multi_agent` feature, current runtime tools, configured
agent roles, and the active instruction policy. In current Codex surfaces,
enabling `multi_agent` makes the tools available but does not by itself count
as a user request to use them. When the runtime requires explicit user
authorization, the prompt must say to use or spawn subagents, use delegation,
or run parallel agents, or the user must deliberately enable a local hook
policy that injects that request for complex prompts. If a session cannot spawn
a subagent, the main agent should keep working with narrower reads and report
that delegation was unavailable or not permitted.

The optional hook policy lives only under `$CODEX_HOME`:

```json
{
  "auto_read_only_subagents": true,
  "include_agent_descriptions": false
}
```

When that policy is present at
`$CODEX_HOME/hooks/global_context_policy.json`, the `UserPromptSubmit` hook
discovers configured read-only agents from `$CODEX_HOME/config.toml` and the
referenced files under `$CODEX_HOME`. It injects agent names only by default,
not local paths. This is still best-effort model-visible guidance; hooks do
not directly call the subagent tool.

### Parent/Subagent Lifecycle

Use this mental model when subagent delegation is permitted:

```text
Parent agent:
  1. Spawn bounded read-only helpers for independent sidecar questions.
  2. Continue parent work while helpers run when the parent is not blocked.
  3. Wait for helper results before relying on their findings.
  4. Treat wait results and async completion notices as terminal results.
  5. Close each completed handle as soon as no follow-up is needed.
  6. Use helper output as evidence, not final authority.
  7. Own edits, verification, risk judgment, and the final answer.
```

## Runtime Boundaries

This setup does not shrink context that already exists. It cannot remove
system/developer instructions, injected memories, the available tool list,
prior turns, or broad command output already returned to the parent thread.

What it can do is change future behavior:

- keep task state in a durable file instead of re-explaining it in chat
- keep raw logs and broad file listings out of the parent thread
- delegate broad read-only mapping and validation planning when explicitly
  authorized and available
- require concise summaries from subagents
- close completed subagent threads after their results are consolidated, when
  close controls are available
- repeat wait-and-close until every spawned subagent handle is closed
- push final risk review into a bounded read-only pass

If context is still growing too quickly, check the working pattern first:
broad shell output, large file dumps, copied logs, and repeated exploration in
the parent thread will still consume context even when hooks and skills are
installed.

## Workflow

For a complex task, the intended flow is:

1. Receive prompt and injected task-state path; for complex prompts, create or
   reuse the task-state file automatically.
2. Identify the objective, constraints, likely files, and validation path.
3. Read existing task state when prior context may matter, then update it with
   the current plan.
4. Use read-only subagents only when they reduce parent-thread noise, the user
   explicitly authorized delegation when required, and the current runtime
   permits delegation. Use `repo_mapper` and `test_strategist` early only when
   useful and independent; close completed helpers after consolidation. Do not
   spawn every configured role by default.
5. Implement the smallest coherent change in the main thread.
6. Inspect the diff and run focused validation.
7. Use `risk_reviewer` near the end only for non-trivial or risky changes.
8. Address serious findings or record why they are out of scope.
9. Update task state and return the final summary.

## Your Mental Model

This is the right mental model:

```text
The hook does this:

Before Codex starts working:
  "Here is the repo.
   Here is the task-state path.
   Create it automatically only when the prompt looks complex.
   Read it when it already exists and prior context may matter.
   Use the global workflow for complex work."

The Skill does this:

During the task:
  "Read useful prior task state, then follow the exact process."

The subagents do this when explicitly authorized and available:

When asked by the main agent:
  "Inspect, summarize, and report.
   Do not edit code."

The main agent does this after delegation:
  "Keep working when not blocked.
   Wait for final helper summaries.
   Treat wait results and async completion notices as terminal.
   Close each completed handle.
   Use helper output as evidence.
   Own the final decision."
```

One nuance: hooks do not force Codex to follow the workflow by themselves. They
inject model-visible context. The best-effort automatic behavior comes from the
combination of global AGENTS routing, hook-injected context, skill metadata, and
the skill body. Subagent use has an additional gate: the runtime must expose
multi-agent tools, the active instructions must permit delegation for the task,
and some Codex surfaces require the user prompt itself to explicitly ask for
subagents, delegation, or parallel agents. A local hook policy can inject that
explicit request for complex prompts, but it remains subject to the active
runtime and instruction policy.

## File Responsibilities

- `SKILL.md`: runtime instructions Codex follows during complex work.
- `README.md`: human-facing architecture, design, workflow, and mental model.
- `references/local-setup.md`: local installation and validation details.
- `assets/`: templates for hooks, optional hook policy, task state, and local
  custom-agent configs.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.

## Validation

Use static validation before claiming the skill is ready:

```bash
python3 align-skill/scripts/validate-skill-structure.py global-context-management
python3 global-context-management/scripts/validate-local-templates.py
markdownlint README.md CHANGELOG.md global-context-management/**/*.md
git diff --check
```

Runtime hook activation is surface-dependent. Treat it as unverified until a
fresh Codex session has loaded and trusted the hooks.

The local template validator uses disposable Codex homes. It verifies hook path
calculation, lazy SessionStart behavior, private task-state permissions
including reuse-time permission repair, prompt-leak prevention, and that an
existing nonempty `current.md` is preserved for the agent to read rather than
overwritten or copied into hook context.

Runtime subagent activation is also surface-dependent. Treat it as unverified
until a fresh session can actually spawn a read-only helper after an explicit
user request or local hook policy, or the UI/runtime shows the multi-agent
tools are available but delegation is not permitted in that surface.

## Enable And Trust Hooks

After local setup, confirm the required Codex features are enabled:

```bash
codex features list | rg '^(hooks|multi_agent)\s'
```

Expected output should show both features enabled:

```text
hooks        stable  true
multi_agent  stable  true
```

If either feature is disabled, enable it and restart Codex:

```bash
codex features enable hooks
codex features enable multi_agent
```

Restart Codex so the updated config and hook files are loaded.

For Codex CLI, there is no separate restart subcommand. Exit the current
session:

```text
/quit
```

Then start Codex again from a shell in the target repo:

```bash
cd <path-to-target-repo>
codex
```

Or start it from any directory by passing the target repo:

```bash
codex --cd <path-to-target-repo>
```

For the VS Code extension, run `Developer: Restart Extension Host` from the
Command Palette, then open a new Codex chat for the target repo.

In the fresh session, open the hook review UI:

```text
/hooks
```

Review the two configured hook commands. They should point to the local
`$CODEX_HOME/hooks/session_start_context.py` and
`$CODEX_HOME/hooks/user_prompt_context.py` scripts. Trust or enable those hooks
from the `/hooks` UI only after confirming the paths are local and expected.

After trusting the hooks, start another fresh session and confirm a complex
prompt receives an injected durable task-state path under:

```text
$CODEX_HOME/task-state/<workspace>-<hash>/<session-id>/current.md
```

Then confirm a complex prompt either spawns a bounded read-only helper after
an explicit request or local hook policy, or states why delegation is
unavailable or not permitted. A useful non-mutating probe is:

```text
Use $global-context-management. Explicitly spawn one read-only repo_mapper
subagent to inspect this repository. Do not edit files. Wait for it, then
report whether the subagent was spawned, and keep raw command output out of
the answer.
```

To test policy-driven injection without hardcoding agent names into the public
repo, create `$CODEX_HOME/hooks/global_context_policy.json` locally, restart
Codex, trust the updated hook in `/hooks`, and run a complex prompt that does
not mention a specific subagent. The hook should discover read-only agents from
`$CODEX_HOME/config.toml` and request them by configured name.
