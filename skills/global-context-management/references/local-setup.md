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
|   `-- user_prompt_context.py
|-- agents/
|   |-- repo_mapper.toml
|   |-- test_strategist.toml
|   `-- risk_reviewer.toml
`-- task-state/
```

The source-controlled skill lives in a public skills repository. The runtime
hooks, custom agent configs, and task-state files live only under
`$CODEX_HOME`.

Template files for `hooks.json`, hook scripts, custom agent config layers, and
task-state structure are available in this skill's `assets/` directory. Copy
or adapt them locally; do not commit the rendered `$CODEX_HOME` files.

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
- Use the durable task-state path injected by global hooks. Do not create
  repo-local task-state files unless explicitly requested.
- Keep the parent thread focused on objective, constraints, decisions, current
  plan, changed files, verification status, risks, and final answer.
- Keep raw logs, broad file listings, abandoned attempts, secrets, customer
  data, broad environment dumps, and large copied documentation blocks out of
  the parent thread.
- Use bounded read-only subagents for noisy exploration when useful, and use a
  read-only risk review before finalizing non-trivial changes.
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
