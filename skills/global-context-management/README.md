# Global Context Management

`global-context-management` is a reusable Codex skill plus local runtime setup
for keeping long or complex coding sessions focused.
It reduces context pollution by separating runtime policy, task-state
continuity, and authorized read-only exploration roles when the current Codex
surface exposes delegation tools.

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
  +--> read-only repo_mapper, when authorized and useful
  +--> read-only test_strategist, when authorized and useful
  `--> read-only risk_reviewer, when authorized for risky work
```

### Hooks

Hooks run before the model starts or before a user prompt is submitted. They
provide durable context, not implementation logic.

They should say, in effect:

```text
Here is the workspace root.
Here is the durable task-state path.
Here are bounded same-workspace prior task-state candidate paths, when relevant.
Create only an empty private scaffold at compaction or the first complex prompt.
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
They also must not inject historical task-state contents into model context.
For complex prompts, `UserPromptSubmit` may list only a small set of
same-workspace prior `current.md` candidate paths from the same
`$CODEX_HOME/task-state/<workspace>-<hash>/` bucket. The parent agent decides
whether any candidate is relevant enough to read.

### Task-State Lifecycle

Task-state files are useful only when Codex reads and updates them. Normal
`SessionStart` startup advertises a missing path without creating it and secures
an existing state file. `SessionStart` with `source=compact` and the first
complex `UserPromptSubmit` create an empty scaffold with `0700` directories and
a `0600` `current.md`. Hook scaffolding never contains prompt text or semantic
task state, and hooks never create SDLC run state or workflow state.

The parent agent writes and updates all semantic content in the advertised file
when durable continuity is useful. Any local PreToolUse write guard must explicitly allow
`$CODEX_HOME/task-state` writes; otherwise the hook can advertise a valid path
that later edits cannot persist. That allowlist is separate from broader
runtime files such as `$CODEX_HOME/hooks`, which should remain protected unless
the user intentionally syncs hook sources.

The current session's advertised `current.md` remains the only write target.
Hook-suggested prior same-workspace `current.md` paths are read candidates, not
active state. Read them only when they appear relevant to the current task,
treat them as stale hints, and verify any useful facts against current repo or
runtime evidence.

Use the task-state file in three places:

1. At task start, resume, or after compaction, read the current file when prior
   decisions, validation status, or next action may affect the work. If the hook
   suggests related prior same-workspace task-state candidates, read only the
   relevant candidate summaries.
2. During the task, update it with concise checkpoints after planning, major
   edits, validation, and risk review.
3. Before the final answer or a long pause, leave the latest status and next
   action so a later turn can continue without replaying raw logs.

Treat task state as a continuity note, not an unquestioned source of truth.
Verify facts that may have drifted, keep raw logs and secrets out of the file,
and prefer short decisions over copied command output. If the file is written
but never read on continuation, it becomes an audit trail rather than useful
context management.

Keep `current.md` as a rolling summary, not an append-only transcript. Replace
stale or superseded details with the latest validated state, retain only the
objective, constraints, decisions, changed files, validation status, risks, and
next action needed for continuation, and summarize any older task-state file
that has grown too large to scan quickly before relying on it.

If `troubleshoot` initializes a `codex-remediation-budget:v1` marker, preserve
that bounded machine-readable block exactly during compaction and summary
rewrites. `global-context-management` provides continuity only: it does not
decide whether failures share a blocker, count semantic remediation attempts,
or change a user-defined budget.

### Skill

The skill is the runtime process contract. During a complex task it tells Codex
how to work:

```text
Understand the task.
Read current task state when continuity may matter.
Update task state.
Use targeted reads.
Use bounded read-only exploration when the current prompt or user-enabled local
hook policy request authorizes delegation, and delegation is available and
useful.
Plan the smallest coherent change.
Edit in focused patches.
Validate narrowly first.
Review risk before finalizing.
Update task state and summarize.
```

`SKILL.md` stays concise because it is loaded into the model context when the
skill is used. Detailed local setup lives in `references/local-setup.md`.

### Subagents

Subagents are bounded read-only helpers for large sidecar exploration,
validation planning, and risk review. Use them when the work would pollute the
parent thread, the current prompt or a user-enabled local hook policy request
authorizes delegation, and the current Codex surface exposes delegation tools.

- `repo_mapper`: maps relevant files, symbols, flows, and conventions.
- `test_strategist`: finds focused tests, fixtures, and validation order.
- `risk_reviewer`: checks near-final work for correctness, regressions,
  security, compatibility, edge cases, and missing tests.

Subagents inspect, summarize, and report. They do not edit code. The main agent
owns consolidation, implementation, final verification, and the final answer.
The main agent also owns cleanup: after spawning a helper, it should wait for
the final summary, fold the useful result back into the parent thread, and
close every spawned subagent handle with `close_agent` or equivalent close
controls once it is completed or no longer needed.
Completed agents can remain open and count toward the concurrency limit until
they are closed, so cleanup is part of the parent agent's completion contract
when close controls exist.
If the parent is finalizing while a helper is still running and the result is
no longer needed, it should close that handle before the final response. If the
result is still needed, it should wait for a terminal status, consolidate the
result, then close the handle.
Do not spawn every configured role by default. Keep `max_threads = 4` as the
conservative local thread budget. Use `repo_mapper` and `test_strategist` early
only when their work is useful and independent, close them after consolidation,
then use `risk_reviewer` near the end only for non-trivial or risky changes.
When several helpers are running, the main agent should close each completed
handle as soon as its terminal result arrives, then keep waiting for the
remaining handles until all spawned helpers are closed.
Before the final answer, the main agent should run a final lifecycle sweep over
every spawned handle and report any handle that could not be closed because
close controls were unavailable or failed.

Subagents are not a guaranteed visible button or separate UI in every surface.
They depend on the `multi_agent` feature, current runtime tools, configured
agent roles, and the active instruction policy. The official Codex config docs
describe `multi_agent` as enabling subagent collaboration tools and configured
agent roles as role guidance for choosing and spawning an agent type. Those
settings make delegation possible, but they do not make hooks call subagent
tools directly. In this workflow, authorization can come from the current
prompt or from a user-enabled local hook policy that adds a per-turn bounded
read-only delegation request for complex prompts, when the active runtime and
instruction policy accept that hook context. That is the supported automatic
mode from the user's perspective: enable the local policy once, let the hook
inject the request, and let the parent agent dynamically choose and spawn
useful targeted helpers. The user does not need to name `repo_mapper`,
`test_strategist`, or `risk_reviewer` in each prompt, and the parent agent
should not ask for another user prompt only because the original prompt did not
mention subagents. If delegation is authorized and useful but subagent controls
are not visible, and `tool_search` is available, the main agent should first
search for multi-agent/subagent tools before reporting delegation unavailable.
If a session still cannot spawn a subagent, the main agent should keep working
with narrower reads and report that delegation was unavailable or not
permitted.

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
not directly call the subagent tool or repeat the full helper lifecycle. When
this policy context is present, the main agent should treat it as an explicit
request for bounded read-only delegation for that turn, then dynamically choose
and spawn the smallest useful set of targeted helpers when they are available
and permitted.

This skill's local hook layer owns only the non-SDLC global-context events:
`SessionStart` for stable global context and task-state path injection, and
`UserPromptSubmit` for lightweight prompt-time context, safety, or opt-in
delegation requests. Do not use this layer to run Agentic SDLC loops. SDLC
guardrails belong in separate `PreToolUse` and `Stop` hooks.

Use `SessionStart` for global conventions, workspace context, environment
notes, coding standards, and stable task-state path hints. Do not use it to
select SDLC phases, modify run state, or inject large documents.

Use `UserPromptSubmit` only for small global reminders, prompt safety, and
lightweight context hints. Do not use it to route `sdlc-start`, parse
requirements, select workflow skills, create run state, or inject large
documents.

If an Agentic SDLC task also needs long-task context management, use
`global-context-management` only to manage parent-thread noise, task-state
continuity, and optional read-only helpers. Keep phase selection, SDLC run
state, requirements and design updates, authorization files, and SDLC write
policy in the `sdlc-*` skills and their dedicated hooks.

### Parent/Subagent Lifecycle

Use this mental model when subagent delegation is permitted:

```text
Parent agent:
  1. Spawn bounded read-only helpers for independent sidecar questions.
  2. Continue parent work while helpers run when the parent is not blocked.
  3. Wait for helper results before relying on their findings.
  4. Treat wait results and async completion notices as terminal results.
  5. Close each completed or no-longer-needed handle when close controls exist.
  6. Sweep all spawned handles before the final answer.
  7. Report any unavailable or failed close operation.
  8. Use helper output as evidence, not final authority.
  9. Own edits, verification, risk judgment, and the final answer.
```

## Runtime Boundaries

This setup does not shrink context that already exists. It cannot remove
system/developer instructions, injected memories, the available tool list,
prior turns, or broad command output already returned to the parent thread.

What it can do is change future behavior:

- keep task state in a durable file instead of re-explaining it in chat
- keep raw logs and broad file listings out of the parent thread
- delegate broad read-only mapping and validation planning when authorized by
  the current prompt or local hook policy request, useful, and available
- require concise summaries from subagents
- close completed or no-longer-needed subagent threads after their results are
  consolidated, when close controls are available
- repeat wait-and-close until every spawned subagent handle is closed
- sweep spawned handles before the final answer and report any close failure or
  unavailable close controls
- push final risk review into a bounded read-only pass

If context is still growing too quickly, check the working pattern first:
broad shell output, large file dumps, copied logs, and repeated exploration in
the parent thread will still consume context even when hooks and skills are
installed.

## Workflow

For a complex task, the intended flow is:

1. Receive prompt and injected task-state path; hooks may create only an empty
   private scaffold at compaction or the first complex prompt.
2. Identify the objective, constraints, likely files, and validation path.
3. Read existing task state when prior context may matter, then update it with
   the current plan.
4. Use read-only subagents when they reduce parent-thread noise, the current
   prompt or local hook policy request authorizes delegation, and the current
   runtime permits delegation. Use `repo_mapper` and `test_strategist` early only when
   useful and independent; close completed helpers after consolidation and
   close no-longer-needed running helpers before finalizing. Do not spawn every
   configured role by default.
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
   Here are likely related prior state paths, if any.
   Initialize an empty private scaffold for the first complex prompt.
   Read it when it already exists and prior context may matter.
   Use the global workflow for complex work."

The Skill does this:

During the task:
  "Read useful prior task state or hook-suggested related summaries,
   verify stale facts, then follow the exact process."

The subagents do this when delegation is authorized and available:

When asked by the main agent:
  "Inspect, summarize, and report.
   Do not edit code."

The main agent does this after delegation:
  "Keep working when not blocked.
   Wait for final helper summaries.
   Treat wait results and async completion notices as terminal.
   Close each completed or no-longer-needed handle.
   Sweep all spawned handles before the final answer.
   Report unavailable or failed cleanup.
   Use helper output as evidence.
   Own the final decision."
```

One nuance: hooks do not force Codex to follow the workflow by themselves. They
inject model-visible context. The best-effort automatic behavior comes from the
combination of global AGENTS routing, hook-injected context, skill metadata,
and the skill body. Subagent use has additional gates: the runtime must expose
multi-agent tools, active instructions must permit delegation for the task, and
the current prompt or a user-enabled local hook policy request must authorize
delegation. A prompt can ask for subagents, delegation, or parallel agents; a
user-enabled local hook policy can inject that bounded read-only delegation
request for complex prompts. After either source authorizes delegation, Codex
should dynamically choose and spawn useful targeted roles instead of waiting
for the prompt to name one or asking for another user prompt. Either way,
spawning remains subject to active runtime and instruction policy; hooks still
do not call subagent tools directly.

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
calculation, lazy startup and compaction behavior, empty-scaffold creation,
private task-state permissions including reuse-time permission repair,
prompt-leak prevention, bounded
same-workspace related task-state candidate discovery, no unrelated workspace
candidate leakage, and that existing nonempty `current.md` files are preserved
for the agent to read rather than overwritten or copied into hook context.

Runtime subagent activation is also surface-dependent. Treat it as unverified
until a fresh session can actually spawn a read-only helper after a prompt
request or local hook policy, or the UI/runtime shows the multi-agent tools are
available but delegation is not permitted in that surface.

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

Review the two configured global-context hook commands. They should point to
the local `$CODEX_HOME/hooks/session_start_context.py` and
`$CODEX_HOME/hooks/user_prompt_context.py` scripts. Trust or enable those
hooks from the `/hooks` UI only after confirming the paths are local and
expected.
If other workflows add hooks, review those entries separately and keep their
event ownership distinct.

After trusting the hooks, start another fresh session and confirm a complex
prompt receives an injected durable task-state path under:

```text
$CODEX_HOME/task-state/<workspace>-<hash>/<session-id>/current.md
```

Then confirm a complex prompt either spawns a bounded read-only helper after a
prompt request or local hook policy, or states why delegation is unavailable or
not permitted. A useful non-mutating probe is:

```text
Use $global-context-management. Explicitly spawn one read-only repo_mapper
subagent to inspect this repository. Do not edit files. Wait for it, close it
after the result when close controls are available, then report whether the
subagent was spawned and closed. Keep raw command output out of the answer.
```

To test policy-driven injection without hardcoding agent names into the public
repo, create `$CODEX_HOME/hooks/global_context_policy.json` locally, restart
Codex, trust the updated hook in `/hooks`, and run a complex prompt that does
not mention a specific subagent. The hook should discover read-only agents from
`$CODEX_HOME/config.toml` and request bounded read-only delegation by
configured name. Codex should then dynamically choose and spawn targeted roles
when useful, or state the active runtime/tool reason it cannot spawn them.
