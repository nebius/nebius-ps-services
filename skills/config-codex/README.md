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
|   |-- global_context_state.py
|   |-- session_start_context.py
|   `-- user_prompt_context.py
|-- agents/
|   |-- repo_mapper.toml
|   |-- test_strategist.toml
|   `-- risk_reviewer.toml
|-- task-state/
`-- task-implementer/  # optional private prompt workspace
```

Normal session startup advertises task state lazily. Compaction and the first
complex prompt initialize only an empty private scaffold; the parent agent owns
the concise semantic summary. The shared helper also provides read-only nested
permission auditing and an explicit content-preserving repair command.

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
Existing local files are inspected; changed targets are backed up
  |
  v
$CODEX_HOME is patched or populated
  |
  +--> AGENTS.md global policy
  +--> config.toml features, MCP, skills, custom agents
  +--> hooks.json and hook scripts
  +--> read-only custom-agent config layers
  +--> private task-state directory
  `--> optional private task-implementer directory
  |
  v
Codex is restarted and hooks are reviewed in /hooks
```

## Core Concepts

### Public Templates, Local Rendering

Assets in this skill are public templates. Most files use placeholders such as
`{{CODEX_HOME}}`, `{{SKILLS_HOME}}`, and `{{PROJECT_ROOT}}`;
`hooks.json.template` intentionally uses `${CODEX_HOME:-$HOME/.codex}` so hook
commands can follow the active shell environment without rendering a machine
path. A user or Codex agent renders or adapts those templates into real files
on that user's machine.

### Back Up, Then Patch

First inspect, then decide whether any write is needed. Existing `AGENTS.md`,
`config.toml`, and `hooks.json` should be backed up only when that specific
file will actually be changed. This keeps reruns idempotent: if the local
laptop already satisfies the contract, the skill should report "no changes"
and leave the filesystem untouched.

For `AGENTS.md` and `config.toml`, patching is not just preferred; it is the
default contract:

- If the file is missing, create it from the template.
- If the file exists, preserve unrelated user content and add only the missing
  guidance, keys, tables, or skill entries.
- If an existing value conflicts with the template, report it instead of
  silently overwriting it.
- If the file already satisfies the contract, do not rewrite it, reformat it,
  sort it, normalize comments, or create a backup.

For `AGENTS.md`, exact template parity is a no-op. Otherwise, add or update
only the compact managed context section. Empty or stale managed markers do not
satisfy validation; the content between the markers must carry the current
durable guidance. For `config.toml`, parse the file first and patch the
minimum settings needed for hooks, multi-agent support, and the three read-only
custom agent config layers.

The managed defaults permit cleanup of temporary trees created by the current
task only after the exact task-specific path is resolved and validated under
the system temporary directory. Use a scoped non-forced deletion such as
`find "$task_temp_dir" -depth -delete`; do not target the temporary root or an
unresolved variable.

Do not treat `assets/config.toml.template` as desired state for existing
machines. It is the create-only baseline for a missing config plus examples of
supported settings. Do not add template-only model defaults, app/plugin
settings, MCP servers, project trust entries, `[[skills.config]]` entries, or
writable roots unless the user explicitly asked for them or the current setup
cannot otherwise satisfy the requested behavior. If `global-context-management`
and `config-codex` are already discoverable from the installed skills
directory, explicit `[[skills.config]]` entries are optional and should not be
added just to match the template.

The create-only baseline selects `gpt-5.6-sol`, `xhigh` reasoning for both
normal and Plan modes, and the Fast service tier. Existing configs retain their
current model preferences unless the user explicitly asks to change them.

### Private Prompt Workspace Is Explicitly Opt-In

When a user asks to enable the `task-implementer` private prompt workspace,
create `$CODEX_HOME/task-implementer` outside every Git worktree and Git
metadata directory with mode `0700`. Reject a symlink at that path and never
print prompt bodies during setup or validation.

For an existing `workspace-write` sandbox, append the rendered absolute prompt
root to `sandbox_workspace_write.writable_roots` while preserving all current
roots. A `danger-full-access` setup needs no writable-root patch. Never switch
the user's `sandbox_mode` or `approval_policy` for this integration. If access
cannot be persisted, report this exact generic per-session alternative:

```bash
codex --add-dir "${CODEX_HOME:-$HOME/.codex}/task-implementer"
```

The normal idempotency preflight ignores this optional integration. Its opt-in
mode validates the location, symlink, mode, and sandbox access contract:

```bash
python3 config-codex/scripts/check-local-idempotency.py \
  --codex-home "$CODEX_HOME" \
  --require-task-implementer-workspace
```

The public-safe MCP baseline in `assets/config.toml.template` covers reusable
servers such as Context7, Playwright, Terraform, MarkItDown, Microsoft Learn,
GitHub, and OpenAI developer docs. Existing local configs may also contain
plugin-managed or machine-specific MCP servers with absolute commands, private
environment values, or tool-specific installers. Preserve those entries when
patching an existing `config.toml`; do not copy their private values into this
public skill. Restore those integrations through their owning plugin or setup
skill.

Hook scripts, custom-agent TOML files, and optional policy files are template
assets rather than semantic patch targets. Copy them when missing. If an
existing file matches the current template, leave it unchanged. If it differs
from the expected template, stop and show the diff instead of overwriting local
customizations; replace it only after the user has reviewed the diff and
confirmed that the local customization should be discarded.

`hooks.json` is a semantic merge target, not a byte-for-byte payload template.
The required global-context `SessionStart` and `UserPromptSubmit` entries must
be present, but additional reviewed workflow hooks, such as Agentic SDLC
`PreToolUse` and `Stop`, should be preserved and reviewed under their owning
workflow.

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
Here is the task-state path.
Here are bounded same-workspace prior task-state candidate paths, when relevant.
Create only an empty private scaffold at compaction or the first complex prompt.
Read existing task state when prior context may matter.
Keep raw logs and broad exploration output out of task state.
Keep current.md as a rolling summary, not an append-only transcript.
Use skills for workflow-specific instructions.
```

Hooks do not implement the task. Normal startup remains lazy; compaction and
the first complex prompt create only an empty `0600` scaffold below `0700`
directories. The parent agent owns all semantic updates when continuity is
useful. The config template allows sandbox writes under
`$CODEX_HOME/task-state`; any installed PreToolUse guard should allow that
same path while continuing to protect unrelated runtime files such as
`$CODEX_HOME/hooks`.
The task-state file is a compact continuation record: replace stale or
superseded details with the latest validated state, keep only the objective,
constraints, decisions, changed files, validation status, risks, and next
action needed for continuation, and summarize oversized historical files
before relying on them.
For complex prompts, the prompt-time hook may also list a bounded set of
same-workspace prior `current.md` candidate paths from the same workspace hash
bucket. It injects paths only, not historical task-state contents. The parent
agent should read only relevant candidates as stale hints, verify them against
current repo or runtime evidence, and keep the current session's advertised
`current.md` as the write target.
If a user deliberately opts in by creating
`$CODEX_HOME/hooks/global_context_policy.json`, the `UserPromptSubmit` hook can
also discover configured read-only agents from `$CODEX_HOME/config.toml` and
add an explicit per-turn bounded delegation request for complex prompts. The
hook injects agent names only by default and does not directly call subagent
tools. The parent Codex agent then dynamically decides whether to spawn the
smallest useful set of read-only helpers, while detailed helper lifecycle rules
remain in `global-context-management`.

This global-context setup owns only the non-SDLC hook events: `SessionStart`
for stable global context and task-state path injection, and `UserPromptSubmit`
for lightweight prompt-time context, safety, or opt-in delegation requests.
Workflow-specific systems such as Agentic SDLC must use separate hook events,
for example `PreToolUse` for hard guardrails and `Stop` for bounded
continuation.

Use `SessionStart` for global conventions, workspace context, environment
notes, coding standards, and stable task-state path hints. Do not use it to
select SDLC phases, modify run state, or inject large documents.

Use `UserPromptSubmit` only for small global reminders, prompt safety, and
lightweight context hints. Do not use it to route `sdlc-start`, parse
requirements, select workflow skills, create run state, or inject large
documents.

### Skills Provide Workflow

`global-context-management` defines the task workflow after hooks inject the
state path. It tells Codex when to read and update task state, how to avoid
parent-thread noise, when to use read-only subagents if delegation is
authorized by the current prompt or a user-enabled local hook policy request
and the current runtime permits it, and how to validate and review risk. Once
delegation is authorized, Codex should dynamically choose and spawn targeted
helper roles when useful; the prompt does not need to name a specific role, and
the parent agent should not ask for another user prompt only because the
original prompt did not mention subagents.

### Custom Agents Are Read-Only Helpers

The custom agent config layers define three bounded roles:

- `repo_mapper`: maps relevant files, symbols, flows, and conventions.
- `test_strategist`: identifies focused tests and validation order.
- `risk_reviewer`: reviews near-final work for defects and missing tests.

They inspect, summarize, and report. The main agent owns edits, final
decisions, and cleanup. After a helper returns its final summary, the main
agent should consolidate the useful result and close every spawned subagent
handle with `close_agent` or equivalent close controls once it is completed or
no longer needed. Completed helpers can remain open and count toward the
concurrency limit until closed, so cleanup is part of the parent agent's
completion contract when close controls exist.
With multiple helpers, it should close each completed handle as its terminal
result arrives and continue waiting on the remaining handles. Before the final
answer, it should run a final lifecycle sweep over every spawned handle and
report any handle that could not be closed because close controls were
unavailable or failed.

The expected operating model is:

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

Custom agents require the `multi_agent` feature, the configured `[agents.*]`
roles, a restarted Codex session, and a surface that exposes subagent tools.
They may not appear as separate user-visible controls in every surface. In
current Codex surfaces, this setup makes subagent tools available but does not
make hooks call subagent tools directly. Prompts that need subagents can ask
Codex to use or spawn subagents, use delegation, or run parallel agents. For
automatic behavior from the user's perspective, the private local hook policy
injects a bounded read-only delegation request for each complex prompt. Treat
that policy request as sufficient authorization when the active runtime and
instructions accept hook context. After either source authorizes delegation,
Codex should dynamically choose and spawn the useful targeted role instead of
waiting for the prompt to name one or asking for another user prompt. The
policy file keeps that opt-in local without hardcoding agent names in the
public repo:

When delegation is authorized and useful but subagent controls are not visible,
and `tool_search` is available, Codex should search for multi-agent/subagent
tools before reporting delegation unavailable.

```json
{
  "auto_read_only_subagents": true,
  "include_agent_descriptions": false
}
```

Place it at `$CODEX_HOME/hooks/global_context_policy.json`.

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

4. Before writing, run the read-only idempotency preflight. On an already
   configured laptop this should pass and the skill should stop without
   creating backups or changing files:

   ```bash
   python3 config-codex/scripts/check-local-idempotency.py \
     --codex-home "$CODEX_HOME"
   ```

   If the user also requested private prompt-workspace access, create the
   `0700` directory, apply only the mode-appropriate writable-root patch, and
   rerun with `--require-task-implementer-workspace`.

5. If the preflight fails, let Codex patch only the failed surfaces from the
   templates in `assets/`. Existing `AGENTS.md` and `config.toml` must be
   patched in place, not replaced.

6. If a local guard blocks a safe patch, stop rather than bypassing it. Report
   the blocked surface, the exact intended change, and the manual out-of-band
   action. Keep the recommendation narrow. For example, if only the
   `Context Management` bullet in `$CODEX_HOME/AGENTS.md` is stale, tell the
   user to patch only that bullet and leave `config.toml`, hooks, hook policy,
   agent TOMLs, and `hooks.json` untouched.

7. Validate the rendered setup:

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

8. Confirm feature flags:

   ```bash
   codex features list | rg '^(hooks|multi_agent)\s'
   ```

   Expected output should show both features enabled:

   ```text
   hooks        stable  true
   multi_agent  stable  true
   ```

9. Return an alignment report before final restart instructions:

   ```text
   Aligned: hooks/user_prompt_context.py matches the installed template.
   Aligned: hooks/global_context_policy.json enables read-only delegation.
   Aligned: hooks and multi_agent are enabled.
   Aligned: repo_mapper, test_strategist, and risk_reviewer are read-only.
   Aligned: hooks.json has required global hooks and preserves workflow hooks.
   Not aligned: AGENTS.md has one stale Context Management bullet.
   Manual action: patch only that bullet from assets/AGENTS.md.template.
   Leave untouched: config.toml, hooks, hook policy, agent TOMLs, hooks.json.
   ```

10. Restart Codex. For Codex CLI, exit the current session:

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

1. In the fresh session, open:

   ```text
   /hooks
   ```

   Review the two global-context hook commands. They should resolve to the
   local hook scripts, either directly or through
   `${CODEX_HOME:-$HOME/.codex}`:

   ```text
   $CODEX_HOME/hooks/session_start_context.py
   $CODEX_HOME/hooks/user_prompt_context.py
   ```

   Trust or enable the hooks only after the resolved paths are local and
   expected. If other workflows add their own hooks, review those separately
   and keep their event ownership distinct.

1. Run a non-mutating probe:

   ```bash
   codex exec --sandbox read-only --cd <PROJECT_ROOT> \
     "Summarize active instruction sources, available skills/custom agents, and the injected durable task-state path. Do not edit files."
   ```

   Treat runtime activation as unverified until this probe shows the expected
   task-state path under:

   ```text
   $CODEX_HOME/task-state/<workspace>-<hash>/<session-id>/current.md
   ```

   The probe should also show that complex-task guidance tells Codex to read
   current task state when prior context may matter, consider bounded related
   same-workspace prior task-state candidate paths when matching summaries
   exist, and update the current `current.md` at checkpoints. To prove subagent
   activation, run a second probe whose prompt explicitly asks Codex to spawn a
   read-only helper, or enable the local hook policy and run a complex prompt
   that should discover configured read-only agents from `$CODEX_HOME` and
   authorize the parent agent to dynamically choose useful helpers.

   If subagent controls are not visible in an otherwise authorized probe, the
   agent should use `tool_search` to look for deferred multi-agent/subagent
   tools before reporting delegation unavailable.

   Do not run complex synthetic hook probes against a live `$CODEX_HOME`: the
   first complex prompt intentionally initializes an empty private scaffold.
   Prefer
   `global-context-management/scripts/validate-local-templates.py` for
   hook-unit validation because it uses disposable temporary homes and checks
   lazy normal startup, compaction and complex-prompt empty scaffolds, related
   same-workspace prior task-state candidate paths are bounded
   and do not leak contents or unrelated workspace files, an existing nonempty
   `current.md` is preserved for the agent to read instead of being overwritten
   or injected into hook context, and loose task-state file permissions are
   repaired on reuse. No manual or legacy task-state path is created when a
   hook payload lacks `session_id`.

## File Responsibilities

- `SKILL.md`: runtime procedure Codex follows when configuring a user's Codex
  home.
- `README.md`: human-facing design, architecture, core concepts, and workflow.
- `references/local-setup.md`: detailed setup and validation checklist.
- `assets/AGENTS.md.template`: generic global instruction file.
- Its managed global defaults include the bounded `troubleshoot` remediation
  policy and preservation of the private remediation marker for existing
  merge-safe installations.
- `assets/config.toml.template`: public-safe Codex config template.
- `assets/hooks.json.template`: hook registration template using
  `${CODEX_HOME:-$HOME/.codex}` so hook commands follow the active Codex home.
- `assets/hooks/*.py.template`: context-injection hook templates.
- `assets/hooks/global_context_policy.json.template`: optional local policy
  template for hook-assisted read-only subagent delegation.
- `assets/agents/*.toml.template`: read-only custom-agent config templates.
- `assets/task-state-template.md`: initial durable task-state document.
- `scripts/check-local-idempotency.py`: read-only preflight that checks the
  minimal no-change contract for an already configured Codex home without
  printing config values. It checks required global hook registrations as a
  subset so extra reviewed workflow hooks can coexist, and validates the
  current managed `AGENTS.md` block content when the whole file does not match
  the template. Exact `AGENTS.md` template parity and public MCP baseline
  parity are explicit audit modes, not normal laptop setup requirements.
  Private task-implementer directory and workspace access checks are likewise
  opt-in through `--require-task-implementer-workspace`.

Low-level hook file sync can use the root installer:

```bash
./install-skills.sh --install-all-hooks
./install-skills.sh --install-hooks config-codex/assets/hooks --register-hooks
```

The first command syncs every reviewed hook-only bundle under the source skills
folder. The second command syncs only this skill's hook payload templates. Both
copy into `$CODEX_HOME/hooks` with `.template` stripped from installed file
names, copy missing hook files, leave matching hook files unchanged, and
record hook file provenance hashes. They back up differing existing hook files
under `$CODEX_HOME/.install-hooks-state/backups/`, then refresh them from
source. Add
`--register-hooks` when the installer should semantically merge the bundle's
hook registration into `$CODEX_HOME/hooks.json`; registration is validated
before payload sync and still does not trust hooks, patch `config.toml`, replace
`AGENTS.md`, or replace the full `config-codex` setup workflow.

- `agents/openai.yaml`: UI metadata and implicit invocation policy.

## Validation For This Skill

From the skills repo root:

```bash
python3 align-skill/scripts/validate-skill-structure.py config-codex
python3 align-skill/scripts/validate-skill-structure.py global-context-management
python3 config-codex/scripts/check-local-idempotency.py \
  --codex-home "$HOME/.codex"
python3 config-codex/scripts/test-check-local-idempotency.py
python3 global-context-management/scripts/validate-local-templates.py
markdownlint README.md CHANGELOG.md config-codex/**/*.md
git diff --check
```

Also confirm duplicate hook and custom-agent templates stay aligned between
`config-codex/assets/` and `global-context-management/assets/`, then run a
targeted public-file scan for personal paths, names, tokens, private URLs, and
real customer/project identifiers before publishing.

## Sources

- OpenAI Codex skills: <https://developers.openai.com/codex/skills>
- OpenAI Codex config reference:
  <https://developers.openai.com/codex/config-reference>
- OpenAI Codex hooks: <https://developers.openai.com/codex/hooks>
