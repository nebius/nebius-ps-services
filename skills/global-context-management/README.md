# Global Context Management

`global-context-management` is a reusable Codex skill plus optional local
runtime setup for keeping long or complex coding sessions focused. It reduces
context pollution by separating runtime policy, task-state persistence, and
read-only exploration roles.

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

## Architecture

```text
User prompt
  |
  v
Global hooks inject workspace and task-state context
  |
  v
global-context-management skill defines the task workflow
  |
  v
Main agent owns planning, edits, validation, and final answer
  |
  +--> read-only repo_mapper, when useful
  +--> read-only test_strategist, when useful
  `--> read-only risk_reviewer, before finalizing risky work
```

### Hooks

Hooks run before the model starts or before a user prompt is submitted. They
provide durable context, not implementation logic.

They should say, in effect:

```text
Here is the workspace root.
Here is the durable task-state file.
For complex work, use global-context-management.
Keep the parent thread concise.
```

The hooks create a session-scoped task-state path such as:

```text
$CODEX_HOME/task-state/<workspace>-<hash>/<session-id>/current.md
```

They must not persist raw prompts, broad command output, stack traces, secrets,
customer data, private URLs, or broad environment dumps.

### Skill

The skill is the runtime process contract. During a complex task it tells Codex
how to work:

```text
Understand the task.
Update task state.
Use targeted reads.
Delegate bounded read-only exploration when useful.
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
large enough that it would pollute the parent thread.

- `repo_mapper`: maps relevant files, symbols, flows, and conventions.
- `test_strategist`: finds focused tests, fixtures, and validation order.
- `risk_reviewer`: checks near-final work for correctness, regressions,
  security, compatibility, edge cases, and missing tests.

Subagents inspect, summarize, and report. They do not edit code. The main agent
owns consolidation, implementation, final verification, and the final answer.

## Workflow

For a complex task, the intended flow is:

1. Receive prompt and injected task-state context.
2. Identify the objective, constraints, likely files, and validation path.
3. Update the task-state file with the current plan.
4. Use read-only subagents only when they reduce parent-thread noise.
5. Implement the smallest coherent change in the main thread.
6. Inspect the diff and run focused validation.
7. Use a read-only risk review for non-trivial or risky changes.
8. Address serious findings or record why they are out of scope.
9. Update task state and return the final summary.

## Your Mental Model

This is the right mental model:

```text
The hook does this:

Before Codex starts working:
  "Here is the repo.
   Here is the task-state file.
   Use the global workflow for complex work."

The Skill does this:

During the task:
  "Here is the exact process to follow."

The subagents do this:

When asked by the main agent:
  "Inspect, summarize, and report.
   Do not edit code."
```

One nuance: hooks do not force Codex to follow the workflow by themselves. They
inject model-visible context. The best-effort automatic behavior comes from the
combination of global AGENTS routing, hook-injected context, skill metadata, and
the skill body.

## File Responsibilities

- `SKILL.md`: runtime instructions Codex follows during complex work.
- `README.md`: human-facing architecture, design, workflow, and mental model.
- `references/local-setup.md`: local installation and validation details.
- `assets/`: templates for hooks, task state, and local custom-agent configs.
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
