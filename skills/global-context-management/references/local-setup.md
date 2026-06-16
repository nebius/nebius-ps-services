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
  concise checkpoints, and do not create repo-local task-state files unless
  explicitly requested.
- Keep the parent thread focused on objective, constraints, decisions, current
  plan, changed files, verification status, risks, and final answer.
- Keep raw logs, broad file listings, abandoned attempts, secrets, customer
  data, broad environment dumps, and large copied documentation blocks out of
  the parent thread.
- Use bounded read-only subagents for noisy exploration when the user
  explicitly requests delegation, or when a user-enabled local hook policy
  injects that request, and subagents are useful, available, and permitted.
  After authorization, choose targeted helper roles yourself instead of waiting
  for the user to name an exact role. Use a read-only risk review before
  finalizing non-trivial changes only when delegation is authorized, useful,
  available, and permitted.
- When subagents are used, ask them for concise final summaries, wait for the
  result, consolidate it in the parent thread, and close completed subagent
  threads when close controls are available and no follow-up is needed.
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
max_threads = 4
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
they do not count as authorization, and they do not guarantee a separate
user-visible control in every Codex surface. Current public Codex docs say
Codex only spawns subagents when explicitly asked. In this workflow, that
explicit request can come from the prompt, or from the optional local hook
policy below when active runtime instructions accept the hook context. Once
delegation is authorized, Codex should choose targeted helper roles when they
are useful; the prompt does not need to name a specific role.
When delegation is authorized and useful but the active tool list does not show
subagent controls, and `tool_search` is available, Codex should search for
multi-agent/subagent tools before reporting delegation unavailable.

Keep `max_threads = 4` as the conservative local thread budget. Do not spawn
every configured read-only role by default: use `repo_mapper` and
`test_strategist` early only when useful and independent, close completed
helpers after consolidation, and use `risk_reviewer` near the end only for
non-trivial or risky changes.

If the user wants hook-assisted delegation for complex prompts, create this
local-only policy file:

```json
{
  "auto_read_only_subagents": true,
  "include_agent_descriptions": false
}
```

Save it as `$CODEX_HOME/hooks/global_context_policy.json`. The hook will read
`$CODEX_HOME/config.toml`, discover configured `[agents.<name>]` entries whose
referenced configs under `$CODEX_HOME` have `sandbox_mode = "read-only"`, and
add those read-only role names as a model-visible delegation request. It does
not inject local agent config paths and does not directly call the subagent
tool. The hint is local-policy context for that turn, so the main agent still
uses targeted read-only helpers only when useful, available, and permitted. The
hint tells Codex not to spawn every configured role by default. The parent
agent still owns
lifecycle cleanup: wait for returned summaries, consolidate them, and close
completed subagent threads when close controls are available and no follow-up
is needed. With multiple subagents, close each completed handle as its
terminal result arrives and continue waiting on the remaining handles.

## Hook Review

After adding or changing hooks, restart Codex and use `/hooks` to review and
trust the new non-managed hooks before relying on them.

The provided `hooks.json` template resolves hook scripts through
`${CODEX_HOME:-$HOME/.codex}`. If you use a non-default `CODEX_HOME`, confirm
the `/hooks` UI shows commands pointing at that directory.

## Validation

Run:

```bash
python3 <path-to-skill>/scripts/validate-local-templates.py

python3 -m py_compile \
  "$CODEX_HOME/hooks/session_start_context.py" \
  "$CODEX_HOME/hooks/user_prompt_context.py"

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
agent edits under `$CODEX_HOME/task-state`. The global-context hooks only
advertise or reuse the session-scoped `current.md`; the parent agent creates
and updates that file when continuity is useful. Keep broader runtime paths
such as `$CODEX_HOME/hooks` protected unless the user deliberately syncs hook
sources.

Direct hook unit probes against a live `$CODEX_HOME` with synthetic
`session_id` values should not create task-state files or directories. They
validate hook path calculation and prompt-time hints, not active persistent
model state. Prefer
`scripts/validate-local-templates.py` for hook-unit validation because it uses
disposable temporary homes. If a hook payload has no `session_id`, task state is
unavailable and no manual or legacy fallback path is created.

Use a second probe for subagent availability:

```text
Use $global-context-management. Explicitly spawn one read-only repo_mapper
subagent to inspect this repository. Do not edit files. Wait for it, then
report whether the subagent was spawned, and keep raw command output out of
the answer.
```

If the probe does not spawn a subagent, check:

- `codex features list` shows `multi_agent` enabled.
- `$CODEX_HOME/config.toml` contains the `[agents]` and `[agents.<name>]`
  entries.
- The session was restarted after editing config.
- The current Codex surface exposes multi-agent tools.
- If multi-agent tools are not exposed directly, `tool_search` is available and
  can discover deferred multi-agent/subagent tools.
- The user prompt explicitly asks Codex to use or spawn subagents, use
  delegation, or run parallel agents, or the enabled local hook policy adds a
  bounded read-only delegation request for the prompt.
- The current user or developer instructions permit delegation for the task.

If `$CODEX_HOME/hooks/global_context_policy.json` is enabled, also run a
complex prompt without naming a specific subagent and confirm the hook-injected
context discovers the configured read-only agent names and requests bounded
read-only delegation. Codex may then choose useful targeted roles itself. If it
does not, check that the hook was trusted after the file changed and that each
referenced agent config uses `sandbox_mode = "read-only"`.

Even when the probe succeeds, context can still grow quickly if broad command
output, long logs, large file dumps, or repeated exploration are returned in
the parent thread. The skill reduces future noise; it cannot remove context
that already entered the conversation.
