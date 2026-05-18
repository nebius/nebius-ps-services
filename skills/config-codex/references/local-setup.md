# Local Setup Reference

Use this reference when applying `config-codex` to a real machine. Public repo
files must stay generic; rendered local files belong under that user's
`$CODEX_HOME`.

## Inputs

Collect these values first:

```text
CODEX_HOME=${CODEX_HOME:-$HOME/.codex}
SKILLS_HOME=$HOME/.agents/skills
PROJECT_ROOT=<PROJECT_ROOT>
```

Use real paths only in local rendered files and commands. Do not commit them.

## Backup

Back up existing files before editing:

```bash
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
ts="$(date +%Y%m%d%H%M%S)"

mkdir -p "$CODEX_HOME"

for file in AGENTS.md config.toml hooks.json; do
  if [ -f "$CODEX_HOME/$file" ]; then
    cp "$CODEX_HOME/$file" "$CODEX_HOME/$file.bak.$ts"
  fi
done
```

## Directory Layout

Create the runtime directories:

```bash
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

mkdir -p "$CODEX_HOME/hooks" "$CODEX_HOME/agents" "$CODEX_HOME/task-state"
chmod 700 "$CODEX_HOME/task-state"
```

## Create Or Patch From Templates

Use the files in `assets/` as source templates:

```text
assets/AGENTS.md.template              -> $CODEX_HOME/AGENTS.md
assets/config.toml.template            -> $CODEX_HOME/config.toml
assets/hooks.json.template             -> $CODEX_HOME/hooks.json
assets/hooks/session_start_context.py.template
  -> $CODEX_HOME/hooks/session_start_context.py
assets/hooks/user_prompt_context.py.template
  -> $CODEX_HOME/hooks/user_prompt_context.py
Optional local hook policy:
assets/hooks/global_context_policy.json.template
  -> $CODEX_HOME/hooks/global_context_policy.json
assets/agents/repo_mapper.toml.template
  -> $CODEX_HOME/agents/repo_mapper.toml
assets/agents/test_strategist.toml.template
  -> $CODEX_HOME/agents/test_strategist.toml
assets/agents/risk_reviewer.toml.template
  -> $CODEX_HOME/agents/risk_reviewer.toml
```

Replace placeholders:

- `{{CODEX_HOME}}` with the user's Codex home.
- `{{SKILLS_HOME}}` with the user's installed skills directory.
- `{{PROJECT_ROOT}}` with the user's trusted project root.

The `hooks.json` template intentionally uses
`${CODEX_HOME:-$HOME/.codex}` directly, so the hook commands stay portable when
the user sets `CODEX_HOME` in the shell before starting Codex.

If `$CODEX_HOME/AGENTS.md` is missing, create it from
`assets/AGENTS.md.template`. If it exists, do not replace it. Append or update a
small managed section for `config-codex`/`global-context-management` guidance
and leave unrelated user rules untouched.

Recommended managed block markers:

```markdown
<!-- BEGIN config-codex managed context -->
...
<!-- END config-codex managed context -->
```

If `$CODEX_HOME/config.toml` is missing, create it from
`assets/config.toml.template` after replacing placeholders. If it exists, do not
replace it. Parse it first, then add only missing settings:

- `[features]` entries such as `hooks` and `multi_agent`.
- `[agents]` limits and `[agents.<name>]` custom-agent references.
- Missing MCP server tables.
- Missing `[[skills.config]]` entries for skill folder paths.
- Missing trusted project entries the user explicitly wants.

Preserve existing user values, comments, profiles, project trust entries, MCP
servers, app settings, and unrelated feature flags. If an existing value
conflicts with the template, report the difference and ask before changing it.
Do not silently relax approval or sandbox settings.

## Optional Hook-Assisted Subagent Policy

To have the `UserPromptSubmit` hook inject a request to use configured
read-only subagents for complex prompts, create this local-only file:

```json
{
  "auto_read_only_subagents": true,
  "include_agent_descriptions": false
}
```

Save it as `$CODEX_HOME/hooks/global_context_policy.json`. The public templates
do not hardcode agent names for this path. The hook reads `$CODEX_HOME/config.toml`,
discovers `[agents.<name>]` entries whose referenced config files have
`sandbox_mode = "read-only"`, and injects those agent names into model-visible
context. It does not inject local agent config paths, and it does not directly
call the subagent tool. The parent agent still owns lifecycle cleanup: wait for
returned summaries, consolidate them, and close completed subagent threads when
close controls are available and no follow-up is needed.
With multiple subagents, close each completed handle as its terminal result
arrives and continue waiting on the remaining handles.

## Secret Handling

Do not put actual token values in `config.toml`.

For Context7:

```bash
export CONTEXT7_API_KEY="<context7-api-key>"
```

For GitHub MCP:

```bash
export GITHUB_TOKEN="<github-token>"
```

Store those exports in the user's preferred local shell or secret-management
setup. Do not commit them.

## Validation

Compile hooks:

```bash
python3 -m py_compile \
  "$CODEX_HOME/hooks/session_start_context.py" \
  "$CODEX_HOME/hooks/user_prompt_context.py"
```

Parse TOML:

```bash
python3 - <<'PY'
import os
import tomllib
from pathlib import Path

codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
print("config.toml is valid TOML")
PY
```

Parse hooks JSON:

```bash
python3 - <<'PY'
import json
import os
from pathlib import Path

codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
print("hooks.json is valid JSON")
PY
```

Check features:

```bash
codex features list | rg '^(hooks|multi_agent)\s'
```

Expected:

```text
hooks        stable  true
multi_agent  stable  true
```

If needed:

```bash
codex features enable hooks
codex features enable multi_agent
```

## Restart And Trust Hooks

Codex CLI has no separate restart command. Exit:

```text
/quit
```

Then start a fresh session:

```bash
cd <PROJECT_ROOT>
codex
```

Or:

```bash
codex --cd <PROJECT_ROOT>
```

For VS Code, run `Developer: Restart Extension Host` from the Command Palette.

In the fresh session:

```text
/hooks
```

Review the two local hooks and trust them only when the paths point to the
expected scripts under `$CODEX_HOME/hooks/`.

## Runtime Probe

After trusting hooks, run a non-mutating probe:

```bash
codex exec --sandbox read-only --cd <PROJECT_ROOT> \
  "Summarize active instruction sources, available skills/custom agents, and the injected durable task-state path. Do not edit files."
```

Expected evidence:

- `global-context-management` is available.
- `config-codex` is available.
- `repo_mapper`, `test_strategist`, and `risk_reviewer` are available, or the
  session clearly reports that subagent delegation is unavailable or not
  permitted in the current surface.
- The injected task-state path is session-scoped under
  `$CODEX_HOME/task-state/<workspace>-<hash>/<session-id>/current.md`.
- Complex-task guidance tells Codex to read current task state when prior
  context may matter, then keep checkpoint updates concise.
  If the optional policy is enabled, it should also mention the discovered
  configured read-only agents by name.

Direct hook unit probes against a live `$CODEX_HOME` with synthetic
`session_id` values create scaffold-only task-state directories named after
those IDs. They validate hook path calculation, not active persistent model
state. Prefer `global-context-management/scripts/validate-local-templates.py`
for hook-unit validation because it uses disposable temporary homes.

Then run an explicit subagent probe:

```bash
codex exec --sandbox read-only --cd <PROJECT_ROOT> \
  "Use $global-context-management. Explicitly spawn one read-only repo_mapper subagent to inspect this repository. Do not edit files. Wait for it, then report whether the subagent was spawned."
```

If that succeeds but ordinary complex prompts do not spawn subagents, the
configuration is working; the remaining gate is the runtime policy that
requires the user prompt to explicitly ask for subagents, delegation, or
parallel agents, or the optional local hook policy has not been enabled or
trusted in a fresh session.
