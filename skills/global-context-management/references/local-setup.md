# Local Setup

This reference explains the local-only setup for `global-context-management`.
Do not copy machine-specific paths, secrets, or task-state contents into a
public repository.

## Layout

Use `$CODEX_HOME`, defaulting to `$HOME/.codex`:

```text
$CODEX_HOME/
|-- AGENTS.md
|-- config.toml
|-- hooks.json
|-- hooks/
|   |-- global_context_state.py
|   |-- session_start_context.py
|   |-- user_prompt_context.py
|   `-- global_context_policy.json  # optional local opt-in
|-- agents/
|   |-- repo_mapper.toml
|   |-- test_strategist.toml
|   `-- risk_reviewer.toml
`-- task-state/
```

The source-controlled skill lives in a public skills repository. The runtime
hooks, custom agent configs, and task-state files live only under
`$CODEX_HOME`.

Template files for `hooks.json`, hook scripts, optional hook policy, custom
agent config layers, and task-state structure are available in this skill's
`assets/` directory. Copy or adapt them locally; do not commit the rendered
`$CODEX_HOME` files.
These assets are the workflow-source mirror; for direct hook-bundle sync, use
the installable `config-codex/assets/hooks` bundle so local setup ownership
stays with `config-codex`.

## Backup First

Back up existing local Codex files before editing:

```bash
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
ts="$(date +%Y%m%d%H%M%S)"

cp "$CODEX_HOME/AGENTS.md" "$CODEX_HOME/AGENTS.md.bak.$ts"
cp "$CODEX_HOME/config.toml" "$CODEX_HOME/config.toml.bak.$ts"

if [ -f "$CODEX_HOME/hooks.json" ]; then
  cp "$CODEX_HOME/hooks.json" "$CODEX_HOME/hooks.json.bak.$ts"
fi
```

## Minimal Global AGENTS.md Addition

Append a compact context-management section to `$CODEX_HOME/AGENTS.md`.
Keep detailed workflow instructions in this skill instead of expanding the
global file.

```markdown
## Context Management

- For non-trivial planning, implementation, debugging, refactoring, migration,
  architecture, review, testing, CI failure, or multi-file coding tasks, use
  the `global-context-management` skill.
- Read the durable task-state file injected by global hooks at task start,
  resume, or after compaction when prior context may matter. Update it with
  concise checkpoints. Hooks may create only an empty private scaffold at
  compaction or the first complex prompt; the parent owns semantic content.
  Do not create repo-local task-state files unless explicitly requested.
- If hooks suggest same-workspace prior task-state candidate paths, read only
  candidates that appear relevant to the current task, treat them as stale
  hints, and verify against current repo or runtime evidence.
- Keep `current.md` as a rolling summary, not an append-only transcript:
  replace stale details with the latest validated state, and summarize any
  oversized historical task-state file before relying on it.
- Preserve an active `codex-remediation-budget:v1` marker exactly while
  rewriting the current task state. Attempt classification and limits belong to
  `troubleshoot`, not the global-context hooks.
- Keep the parent thread focused on objective, constraints, decisions, current
  plan, changed files, verification status, risks, and final answer.
- Keep raw logs, broad file listings, abandoned attempts, secrets, customer
  data, broad environment dumps, and large copied documentation blocks out of
  the parent thread.
- Use bounded read-only subagents for noisy exploration when the current prompt
  asks for delegation, or when a user-enabled local hook policy injects a
  current-turn bounded read-only delegation request, and subagents are useful,
  available, and permitted. Treat that policy request as sufficient
  authorization; do not ask for another user prompt only because the original
  prompt did not name subagents. After authorization, dynamically choose and
  spawn targeted helper roles yourself instead of waiting for the user to name
  an exact role. Use a read-only risk review before finalizing non-trivial
  changes only when delegation is authorized, useful, available, and permitted.
- When subagents are used, ask them for concise final summaries, wait for the
  result, consolidate it in the parent thread, and close completed subagent
  threads when close controls are available. Before the final response, close
  every spawned subagent handle that is completed or no longer needed; if close
  controls are unavailable or cleanup fails, report that residual handle.
  With multiple subagents, close each completed handle as its terminal result
  arrives, then continue waiting on the remaining handles.
- If delegation is authorized and useful but subagent tools are not visible,
  and `tool_search` is available, first search for multi-agent/subagent tools.
  If subagents are still unavailable, denied, or not permitted, continue with
  narrow local reads and state that delegation was unavailable or not permitted.
```

## Config Snippet

Patch `$CODEX_HOME/config.toml`; do not replace the whole file. Use config
layers for custom agents:

```toml
[features]
hooks = true
multi_agent = true

[agents]
max_threads = 16
max_depth = 1

[agents.repo_mapper]
description = "Read-only codebase explorer for relevant files, symbols, execution paths, dependencies, and conventions."
nickname_candidates = ["Mapper", "Pathfinder", "Scout"]
config_file = "agents/repo_mapper.toml"

[agents.test_strategist]
description = "Read-only verification planner for tests, fixtures, CI commands, local commands, and validation gaps."
nickname_candidates = ["Verifier", "Test Scout", "Checkmate"]
config_file = "agents/test_strategist.toml"

[agents.risk_reviewer]
description = "Read-only reviewer focused on correctness, regressions, security, compatibility, edge cases, and missing tests."
nickname_candidates = ["Reviewer", "Sentinel", "Auditor"]
config_file = "agents/risk_reviewer.toml"
```

If an MCP server currently stores a secret directly in `config.toml`, move it
to an environment variable and configure the server to use `env_vars`.

The `multi_agent` feature and `[agents.*]` roles make subagent delegation
available to Codex. They do not force every complex task to spawn a subagent,
they do not count as authorization by themselves, and they do not guarantee a
separate user-visible control in every Codex surface. In this workflow,
authorization can come from the prompt, or from the optional local hook policy
below when active runtime instructions accept the hook context. Treat the
enabled policy's current-turn delegation request as sufficient authorization:
the parent agent should dynamically choose and spawn targeted helper roles when
they are useful, and should not ask for another user prompt only because the
original prompt did not name subagents.
When delegation is authorized and useful but the active tool list does not show
subagent controls, and `tool_search` is available, Codex should search for
multi-agent/subagent tools before reporting delegation unavailable.

Keep `max_threads = 16` as the configured local thread budget. Do not spawn
every configured read-only role by default: use `repo_mapper` and
`test_strategist` early only when useful and independent, close completed
helpers after consolidation, and use `risk_reviewer` near the end only for
non-trivial or risky changes.
Completed helpers can still count toward concurrency until closed, so cleanup
is part of the parent agent's completion contract when close controls exist.

If the user wants hook-assisted delegation for complex prompts, create this
local-only policy file:

```json
{
  "auto_read_only_subagents": true,
  "include_agent_descriptions": false
}
```

Save it as `$CODEX_HOME/hooks/global_context_policy.json`. The optional policy
template is enabled because creating this local file is the deliberate opt-in.
The hook will read
`$CODEX_HOME/config.toml`, discover configured `[agents.<name>]` entries whose
referenced configs under `$CODEX_HOME` have `sandbox_mode = "read-only"`, and
add those read-only role names as a model-visible delegation request. It does
not inject local agent config paths and does not directly call the subagent
tool. The hint is local-policy context for that turn, so the main agent
dynamically decides whether to spawn the smallest useful set of targeted
read-only helpers. The hint tells Codex not to spawn every configured role by
default. The parent agent still owns lifecycle cleanup: wait for returned
summaries, consolidate them, and close completed subagent threads when close
controls are available. Before the final response, close every spawned handle
that is completed or no longer needed. With multiple subagents, close each
completed handle as its terminal result arrives and continue waiting on the
remaining handles. If close controls are unavailable or cleanup fails, report
the residual open or running handle instead of leaving it silent.

## Hook Review

After adding or changing hooks, restart Codex and use `/hooks` to review and
trust the new non-managed hooks before relying on them.

The provided `hooks.json` template resolves hook scripts through
`${CODEX_HOME:-$HOME/.codex}`. If you use a non-default `CODEX_HOME`, confirm
the `/hooks` UI shows commands pointing at that directory.

## Validation

Run local template validation and syntax-check rendered hooks without bytecode
writes:

```bash
python3 <path-to-skill>/scripts/validate-local-templates.py

python3 - <<'PY'
import os
from pathlib import Path

codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
for path in (
    codex_home / "hooks/global_context_state.py",
    codex_home / "hooks/session_start_context.py",
    codex_home / "hooks/user_prompt_context.py",
):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
print("hook scripts parse")
PY

python3 - <<'PY'
import os
import tomllib
from pathlib import Path

codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
print("config.toml is valid TOML")
PY

codex features list
```

Then run a non-mutating Codex probe from a test repository and confirm the
injected task-state path appears. Treat runtime activation as unverified until
observed in the target Codex surface.

For persistence, also confirm any installed PreToolUse write guard allows
agent edits under `$CODEX_HOME/task-state`. Normal startup stays lazy;
compaction and the first complex prompt create only an empty private scaffold.
The parent agent writes and updates all semantic content. Keep broader runtime paths
such as `$CODEX_HOME/hooks` protected unless the user deliberately syncs hook
sources.
Treat existing `current.md` files as compact rolling summaries: replace stale
details instead of appending transcripts, keep raw logs and secrets out, and
summarize files that have grown too large to scan before relying on them.
For complex prompts, the prompt-time hook may list a bounded set of
same-workspace prior `current.md` candidate paths from the same workspace hash
bucket. It must not inject the contents of those files. Treat candidate paths
as optional stale context to inspect only when relevant; the current session's
advertised `current.md` remains the write target.

Do not run complex synthetic hook probes against a live `$CODEX_HOME`; the
first complex prompt intentionally creates an empty scaffold. Prefer
`scripts/validate-local-templates.py` for hook-unit validation because it uses
disposable temporary homes. If a hook payload has no `session_id`, task state is
unavailable and no manual or legacy fallback path is created.

Use a second probe for subagent availability:

```text
Use $global-context-management. Explicitly spawn one read-only repo_mapper
subagent to inspect this repository. Do not edit files. Wait for it, close it
after the result when close controls are available, then report whether the
subagent was spawned and closed. Keep raw command output out of the answer.
```

If the probe does not spawn a subagent, check:

- `codex features list` shows `multi_agent` enabled.
- `$CODEX_HOME/config.toml` contains the `[agents]` and `[agents.<name>]`
  entries.
- The session was restarted after editing config.
- The current Codex surface exposes multi-agent tools.
- If multi-agent tools are not exposed directly, `tool_search` is available and
  can discover deferred multi-agent/subagent tools.
- The current prompt asks Codex to use or spawn subagents, use delegation, or
  run parallel agents, or the enabled local hook policy adds a bounded
  read-only delegation request for the prompt.
- The current user or developer instructions permit delegation for the task.

If `$CODEX_HOME/hooks/global_context_policy.json` is enabled, also run a
complex prompt without naming a specific subagent and confirm the hook-injected
context discovers the configured read-only agent names and requests bounded
read-only delegation. Codex should then dynamically choose and spawn useful
targeted roles itself. If it does not, check that the hook was trusted after
the file changed and that each referenced agent config uses
`sandbox_mode = "read-only"`.

Even when the probe succeeds, context can still grow quickly if broad command
output, long logs, large file dumps, or repeated exploration are returned in
the parent thread. The skill reduces future noise; it cannot remove context
that already entered the conversation.
