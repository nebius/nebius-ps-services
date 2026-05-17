# Config Codex

`config-codex` is a public Codex skill for bootstrapping a local Codex runtime
setup similar to the global context-management setup used in this skills repo.
It packages the design, templates, and validation workflow needed to configure
Codex without publishing personal paths or secrets.

## What It Does

The skill helps a user create or align this local layout:

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

It also points Codex at user-installed skills under `$HOME/.agents/skills`,
including `global-context-management` and `config-codex`.

## Design Goal

The goal is repeatable local setup, not copying a live laptop configuration.
Public files in this repo stay generic. Rendered files under a user's
`$CODEX_HOME` can contain that user's real paths, but they should not be
committed.

The setup is intentionally split into two layers:

- `config-codex`: configures the Codex runtime layout and local policy files.
- `global-context-management`: governs how Codex should work during complex
  repository tasks after that runtime setup is active.

## Architecture

```text
User asks for Codex setup
  |
  v
config-codex skill selects public templates
  |
  v
Existing local files are backed up
  |
  v
$CODEX_HOME is patched or populated
  |
  +--> AGENTS.md global policy
  +--> config.toml features, MCP, skills, custom agents
  +--> hooks.json and hook scripts
  +--> read-only custom-agent config layers
  `--> private task-state directory
  |
  v
Codex is restarted and hooks are reviewed in /hooks
```

## Core Concepts

### Public Templates, Local Rendering

Assets in this skill use placeholders such as `$CODEX_HOME`, `$HOME`, and
`<PROJECT_ROOT>`. They are safe to publish. A user or Codex agent renders those
templates into real files on that user's machine.

### Back Up, Then Patch

Existing `AGENTS.md`, `config.toml`, and `hooks.json` should be backed up
before changes. For real users, patching is safer than replacing the full file
because local MCP servers, profiles, app settings, and project trust entries
may already exist.

For `AGENTS.md` and `config.toml`, patching is not just preferred; it is the
default contract:

- If the file is missing, create it from the template.
- If the file exists, preserve unrelated user content and add only the missing
  guidance, keys, tables, or skill entries.
- If an existing value conflicts with the template, report it instead of
  silently overwriting it.

For `AGENTS.md`, add or update only the compact managed context section. For
`config.toml`, parse the file first and patch the minimum supported settings
needed for features, MCP servers, custom agent config layers, and skill
discovery.

### Secrets Stay Out Of Config

The config template references secret environment variable names, not values:

- `CONTEXT7_API_KEY`
- `GITHUB_TOKEN`

The actual values belong in the user's shell profile, password manager, or
secret manager. Do not print or commit them.

### Hooks Provide Context

The hook layer injects durable context before Codex starts work:

```text
Here is the workspace root.
Here is the task-state file.
Use global-context-management for complex work.
Keep the parent thread concise.
```

Hooks do not implement the task. They make the right context visible to Codex.

### Skills Provide Workflow

`global-context-management` defines the task workflow after hooks inject the
state path. It tells Codex when to update task state, how to avoid parent-thread
noise, when to use read-only subagents, and how to validate and review risk.

### Custom Agents Are Read-Only Helpers

The custom agent config layers define three bounded roles:

- `repo_mapper`: maps relevant files, symbols, flows, and conventions.
- `test_strategist`: identifies focused tests and validation order.
- `risk_reviewer`: reviews near-final work for defects and missing tests.

They inspect, summarize, and report. The main agent owns edits and final
decisions.

### Full Access Is A Trusted-Machine Profile

The template mirrors a high-autonomy local developer setup:

```toml
approval_policy = "never"
sandbox_mode = "danger-full-access"
```

Use that profile only on a trusted local machine and trusted repositories. New
or shared-machine users should tighten these settings before enabling the
setup.

## Workflow

1. Install the reusable skills:

   ```bash
   ./install-skills.sh
   ```

2. Ask Codex to use this skill from the target machine:

   ```text
   $config-codex Configure my local Codex setup using the public templates. Back up existing files, keep secrets out of config, and validate before telling me it is ready.
   ```

3. Review the target values:

   ```text
   $CODEX_HOME=$HOME/.codex
   skills home=$HOME/.agents/skills
   project root=<PROJECT_ROOT>
   ```

4. Let Codex patch local files from the templates in `assets/`. Existing
   `AGENTS.md` and `config.toml` must be patched in place, not replaced.

5. Validate the rendered setup:

   ```bash
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

   python3 - <<'PY'
   import json
   import os
   from pathlib import Path

   codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
   json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
   print("hooks.json is valid JSON")
   PY
   ```

6. Confirm feature flags:

   ```bash
   codex features list | rg '^(hooks|multi_agent)\s'
   ```

   Expected output should show both features enabled:

   ```text
   hooks        stable  true
   multi_agent  stable  true
   ```

7. Restart Codex. For Codex CLI, exit the current session:

   ```text
   /quit
   ```

   Then start a fresh session:

   ```bash
   cd <PROJECT_ROOT>
   codex
   ```

   Or start from any directory:

   ```bash
   codex --cd <PROJECT_ROOT>
   ```

   For the VS Code extension, run `Developer: Restart Extension Host` from the
   Command Palette, then open a new Codex chat for the target repo.

8. In the fresh session, open:

   ```text
   /hooks
   ```

   Review the two local hook commands. They should point to:

   ```text
   $CODEX_HOME/hooks/session_start_context.py
   $CODEX_HOME/hooks/user_prompt_context.py
   ```

   Trust or enable the hooks only after the paths are local and expected.

9. Run a non-mutating probe:

   ```bash
   codex exec --ask-for-approval never --cd <PROJECT_ROOT> \
     "Summarize active instruction sources, available skills/custom agents, and the injected durable task-state path. Do not edit files."
   ```

   Treat runtime activation as unverified until this probe shows the expected
   task-state path under:

   ```text
   $CODEX_HOME/task-state/<workspace>-<hash>/<session-id>/current.md
   ```

## File Responsibilities

- `SKILL.md`: runtime procedure Codex follows when configuring a user's Codex
  home.
- `README.md`: human-facing design, architecture, core concepts, and workflow.
- `references/local-setup.md`: detailed setup and validation checklist.
- `assets/AGENTS.md.template`: generic global instruction file.
- `assets/config.toml.template`: public-safe Codex config template.
- `assets/hooks.json.template`: hook registration template.
- `assets/hooks/*.py.template`: context-injection hook templates.
- `assets/agents/*.toml.template`: read-only custom-agent config templates.
- `assets/task-state-template.md`: initial durable task-state document.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.

## Validation For This Skill

From the skills repo root:

```bash
python3 align-skill/scripts/validate-skill-structure.py config-codex
markdownlint README.md CHANGELOG.md config-codex/**/*.md
git diff --check
```

Also run a targeted public-file scan for personal paths, names, tokens,
private URLs, and real customer/project identifiers before publishing.

## Sources

- OpenAI Codex skills: <https://developers.openai.com/codex/skills>
- OpenAI Codex config reference:
  <https://developers.openai.com/codex/config-reference>
- OpenAI Codex hooks: <https://developers.openai.com/codex/hooks>
