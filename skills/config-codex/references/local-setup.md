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

## Idempotency First

Before writing anything, inspect the current local setup and build a patch
plan. If no file needs to change, do not create backups and do not rewrite
files just to match template formatting.

For a laptop expected to match this repo's canonical local setup exactly, run:

```bash
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

python3 config-codex/scripts/check-local-idempotency.py \
  --codex-home "$CODEX_HOME" \
  --strict-agents-template
```

If the check passes, report that no local changes are required for the checked
surfaces. If it fails, patch only the failed surfaces.

When local policy blocks an otherwise safe patch, do not work around the
guard. Report the blocked file, the smallest intended edit, and the manual
out-of-band action. The report should also name files that are already aligned
and should not be touched.

## Backup

Back up only files that the patch plan will change. Do not create backup files
for a no-op run.

Example backup helper for the files that will be changed:

```bash
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
ts="$(date +%Y%m%d%H%M%S)"

mkdir -p "$CODEX_HOME"

for file in "$@"; do
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

For hook scripts, custom-agent TOML files, and optional policy files, use
replace-if-unmodified behavior:

- Copy the file when the target is missing.
- Leave the file unchanged when the target already matches the current
  template.
- Replace the file only when it still matches the previous known template, or
  after the user reviews the diff and confirms the local customization should
  be discarded.

Replace placeholders:

- `{{CODEX_HOME}}` with the user's Codex home.
- `{{SKILLS_HOME}}` with the user's installed skills directory.
- `{{PROJECT_ROOT}}` with the user's trusted project root.

The `hooks.json` template intentionally uses
`${CODEX_HOME:-$HOME/.codex}` directly, so the hook commands stay portable when
the user sets `CODEX_HOME` in the shell before starting Codex.

Treat `hooks.json` as a semantic merge target on existing machines. Ensure the
global-context `SessionStart` and `UserPromptSubmit` entries from the template
are present, but preserve additional reviewed workflow entries such as Agentic
SDLC `PreToolUse` and `Stop` hooks. Do not replace `hooks.json` just to match
the template byte-for-byte.

The root `install-skills.sh --register-hooks` path follows the same semantic
merge model for hook bundles: it validates `hooks.json`, preserves existing
entries, and appends only missing source entries. It still does not trust hooks
or patch `config.toml`.

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
replace it. Parse it first, then add only missing settings required by the
requested setup:

- `[features]` entries such as `hooks` and `multi_agent`.
- `[agents]` limits and `[agents.<name>]` custom-agent references.
- MCP server tables only for integrations the user explicitly requested.
- `[[skills.config]]` entries only when skill discovery is not already working
  and the user wants explicit entries.
- Trusted project entries only for project roots the user explicitly wants.

Preserve existing user values, comments, profiles, project trust entries, MCP
servers, app settings, and unrelated feature flags. If an existing value
conflicts with the template, report the difference and ask before changing it.
Do not silently relax approval or sandbox settings.

Do not add template-only model defaults, app/plugin settings, MCP servers,
project entries, skill entries, or writable roots to an existing config merely
because they appear in `assets/config.toml.template`.

For a missing config, the public-safe MCP baseline in the template restores the
reusable MCP servers that can be expressed without private values. Existing
configs may also contain plugin-managed or machine-specific MCP servers with
absolute commands or private environment values. Preserve those entries during
patching, but restore them through their owning plugin or setup skill rather
than copying local values into public templates.

## Optional Hook-Assisted Subagent Policy

To have the `UserPromptSubmit` hook add a lightweight hint about configured
read-only subagents for complex prompts, create this local-only file:

```json
{
  "auto_read_only_subagents": true,
  "include_agent_descriptions": false
}
```

Save it as `$CODEX_HOME/hooks/global_context_policy.json`. The optional policy
template is enabled because creating this local file is the deliberate opt-in.
The public templates do not hardcode agent names for this path. The hook reads
`$CODEX_HOME/config.toml`, discovers `[agents.<name>]` entries whose referenced
config files have `sandbox_mode = "read-only"`, and injects those agent names
into model-visible context as an explicit bounded read-only delegation request.
It does not inject local agent config paths, and it does not directly call the
subagent tool. The hint is local-policy context for that turn, so the main
agent dynamically decides whether to spawn the smallest useful set of targeted
read-only helpers. After authorization, the prompt does not need to name a
specific helper role.
The parent agent still owns lifecycle cleanup: wait for returned summaries,
consolidate them, and close completed subagent threads when close controls are
available and no follow-up is needed.
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

Run the read-only idempotency preflight:

```bash
python3 config-codex/scripts/check-local-idempotency.py \
  --codex-home "$CODEX_HOME" \
  --strict-agents-template
```

The preflight checks the required global hook registrations as a subset and
allows extra reviewed workflow hooks to coexist in `hooks.json`.

Summarize the result as an alignment matrix before making or recommending
changes:

```text
Aligned: <surface and evidence>
Not aligned: <surface and exact drift>
Blocked: <surface Codex could not patch because of local guard policy>
Manual action: <narrow edit the user can apply after review>
Leave untouched: <aligned local files that should not be changed>
```

For example, if only `$CODEX_HOME/AGENTS.md` differs from
`assets/AGENTS.md.template`, report the exact stale bullet and tell the user to
patch only that bullet. Do not recommend replacing the whole file, and do not
touch `config.toml`, hooks, hook policy, custom-agent TOMLs, or `hooks.json`
when those surfaces already validate.

Syntax-check rendered hooks without bytecode writes:

```bash
python3 - <<'PY'
import os
from pathlib import Path

codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
for path in (
    codex_home / "hooks/session_start_context.py",
    codex_home / "hooks/user_prompt_context.py",
):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
print("hook scripts parse")
PY
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
  session first uses `tool_search` to look for deferred multi-agent/subagent
  tools when controls are not visible, then clearly reports that subagent
  delegation is unavailable or not permitted in the current surface.
- The injected task-state path is session-scoped under
  `$CODEX_HOME/task-state/<workspace>-<hash>/<session-id>/current.md`.
- Complex-task guidance tells Codex to read current task state when prior
  context may matter, then keep checkpoint updates concise.
  If the optional policy is enabled, it should also mention the discovered
  configured read-only agents by name.
- Sandbox configuration and any installed PreToolUse write guard allow
  `$CODEX_HOME/task-state` so the parent agent can create and update the
  advertised `current.md`, while broader runtime paths such as
  `$CODEX_HOME/hooks` remain protected unless deliberately synced.

Direct hook unit probes against a live `$CODEX_HOME` with synthetic
`session_id` values should not create task-state files or directories. They
validate hook path calculation and prompt-time hints, not active persistent
model state. Prefer
`global-context-management/scripts/validate-local-templates.py` for hook-unit
validation because it uses disposable temporary homes. If a hook payload has no
`session_id`, task state is unavailable and no manual or legacy fallback path is
created.

Then run an explicit subagent probe:

```bash
codex exec --sandbox read-only --cd <PROJECT_ROOT> \
  "Use $global-context-management. Explicitly spawn one read-only repo_mapper subagent to inspect this repository. Do not edit files. Wait for it, then report whether the subagent was spawned."
```

If that succeeds but ordinary complex prompts do not spawn subagents, the
configuration is working; the remaining gate is delegation authorization. A
prompt can explicitly ask for subagents, delegation, or parallel agents, and
the optional local hook policy can inject that request for complex prompts
after it is enabled and trusted in a fresh session. Once authorization is
present, Codex should dynamically choose and spawn useful targeted roles
itself; the prompt does not need to name the exact role.

If the explicit probe does not see subagent controls but `tool_search` is
available, the agent should search for multi-agent/subagent tools before
reporting delegation unavailable.
