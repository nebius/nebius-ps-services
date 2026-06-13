---
name: config-codex
description: Configure a public-safe Codex home setup for a developer machine, including global AGENTS.md policy, config.toml features and MCP servers, hooks, task-state layout, custom read-only agents, and validation. Use when a user wants Codex configured similarly to this repo's global context-management workflow without copying personal paths or secrets.
---

# Config Codex

## Purpose

Use this skill to bootstrap or align a user's local Codex runtime setup from
public-safe templates. The goal is a reusable `$CODEX_HOME` layout with global
instructions, features, MCP servers, hooks, task-state storage, and custom
read-only agents that support `global-context-management`.

## Use This Skill For

- Creating or updating a local `$CODEX_HOME` layout.
- Adding global `AGENTS.md` guidance without replacing unrelated user rules.
- Patching `config.toml` for supported Codex features, MCP servers, custom
  agent config layers, and skill discovery.
- Installing hook and custom-agent templates for global context management.
- Validating that the resulting local Codex config parses and is ready for
  hook review.
- Re-running against an already configured laptop and reporting "no changes"
  when the required AGENTS, config, hook, and read-only agent surfaces already
  match the intended setup.

## Safety Rules

- Never copy a live local config into a public repository.
- Never persist personal names, absolute home paths, secrets, tokens, private
  URLs, customer data, raw prompts, command output, stack traces, or broad
  environment dumps.
- Store secret values only in the user's shell, password manager, or external
  secret manager. In `config.toml`, reference secret variable names such as
  `CONTEXT7_API_KEY` or `GITHUB_TOKEN`.
- Inspect and build a patch plan before writing. Back up an existing local file
  only when that file will actually be changed.
- `AGENTS.md` and `config.toml` are patch-only when they already exist. Create
  them from templates only when they are missing.
- Do not overwrite, replace, reformat, sort, or regenerate an existing
  `AGENTS.md` or `config.toml`.
- Do not treat `config.toml.template` as desired state for existing machines.
  It is a create-only baseline plus examples of supported settings.
- For hooks, custom-agent TOML files, and optional policy files, copy missing
  files directly from templates. Replace an existing file only when it still
  matches the previous template, or after showing the diff and receiving user
  confirmation.
- Treat full-access settings as intended only for trusted local developer
  machines.

## Patch-Only Contract

For existing `$CODEX_HOME/AGENTS.md`:

- Preserve all unrelated user rules and ordering.
- If the whole file already matches `assets/AGENTS.md.template`, leave it
  unchanged and do not create a backup.
- Add a compact `config-codex` managed section only if the equivalent guidance
  is missing.
- If managed markers already exist, update only the content between those
  markers.
- Do not delete, rewrite, or deduplicate user-authored sections outside the
  managed block.

For existing `$CODEX_HOME/config.toml`:

- Parse the file before editing.
- Add only missing keys, tables, or `[[skills.config]]` entries that are
  required for the requested setup and absent from the current config.
- Preserve existing user values, comments, profiles, project trust entries,
  MCP servers, app settings, and unrelated feature flags.
- Do not add template-only preferences such as model defaults, app/plugin
  settings, MCP servers, project trust entries, `[[skills.config]]` entries, or
  writable roots when the current config already provides the requested
  behavior or the user did not ask for that integration.
- Treat `hooks = true`, `multi_agent = true`, `agents.max_threads`,
  `agents.max_depth`, and the three read-only custom-agent config references as
  the minimal config surface for global context management.
- Treat explicit `[[skills.config]]` entries for `global-context-management`
  and `config-codex` as optional when the skills are already discoverable from
  the installed user skills directory.
- Do not silently change stricter approval or sandbox settings. Report the
  difference and ask before switching to the trusted-machine full-access
  profile.
- If a target MCP server, custom agent, feature flag, or skill entry already
  exists with different values, report the conflict and patch only after the
  user confirms the desired value.

## Workflow

1. Identify the target `$CODEX_HOME`; default to `$HOME/.codex`.
2. Identify the installed skills directory; default to `$HOME/.agents/skills`.
3. Inspect existing local Codex files with redaction. Do not print secrets.
4. Run an idempotency preflight. If existing files already satisfy the required
   setup, report that no local changes are needed and stop without creating
   backups.
5. Back up only files that the patch plan will change.
6. Create missing local files from templates, but patch existing
   `AGENTS.md` and `config.toml` according to the patch-only contract.
7. Create the local layout:
   - `$CODEX_HOME/hooks/`
   - `$CODEX_HOME/agents/`
   - `$CODEX_HOME/task-state/`
   - optional `$CODEX_HOME/hooks/global_context_policy.json` only when the
     user deliberately wants hook-assisted read-only subagent delegation
8. Render or adapt templates from `assets/`:
   - `AGENTS.md.template`
   - `config.toml.template`
   - `hooks.json.template`
   - hook script templates
   - optional hook policy template
   - custom-agent TOML templates
   - task-state template
   For hook and custom-agent assets, use replace-if-unmodified behavior:
   copy missing files, leave matching files unchanged, and stop for review when
   an existing file differs from both the old and new expected content.
9. Confirm `global-context-management` and `config-codex` are installed,
   discoverable, or explicitly enabled as skill folders. Do not add explicit
   skill entries if discovery already works.
10. Validate local hook scripts, TOML, JSON, feature flags, idempotency, and
   secret hygiene.
11. Tell the user to restart Codex, open `/hooks`, review the two local hooks,
   and trust them only after confirming the paths are expected.

## Template Rules

Use placeholders in public assets:

- `{{CODEX_HOME}}` for the user's Codex home in rendered TOML and text files.
- `{{SKILLS_HOME}}` for the user's installed skills directory.
- `{{PROJECT_ROOT}}` for the user's trusted repository root.
- `${CODEX_HOME:-$HOME/.codex}` inside `hooks.json.template`, so hook commands
  can resolve against the active shell environment without publishing or
  rendering a machine-specific absolute path.

When writing rendered local files, replace template placeholders with that
user's real paths where the target file needs literal paths. Do not commit
rendered files. Treat full-file templates as source material for missing files;
for existing `AGENTS.md` and `config.toml`, extract and patch only the missing
sections or keys.

## Validation

Use the focused checks in `references/local-setup.md`. At minimum:

- Run `python3 scripts/check-local-idempotency.py --strict-agents-template` for
  a laptop already expected to match these templates. This script is read-only.
- Python-compile hook scripts.
- Parse `config.toml` with `tomllib`.
- Parse `hooks.json` with `json`.
- Confirm `codex features list` reports `hooks` and `multi_agent` enabled.
- Run a targeted secret/path scan over changed public files.

Do not claim runtime activation is proven until a fresh Codex session has
loaded the config, the hooks have been trusted in `/hooks`, and a non-mutating
probe shows the injected task-state path and read/update guidance.

Do not claim subagent activation is proven until a fresh Codex session receives
a prompt request or local hook policy request to use subagents and can spawn a
read-only helper, or reports that delegation is unavailable or not permitted in
that surface. If delegation is authorized and useful but subagent controls are
not visible, and `tool_search` is available, the fresh session should first
search for multi-agent/subagent tools before reporting delegation unavailable.
If a local hook policy is enabled, verify it in a fresh trusted-hook session
before claiming hook-assisted delegation works. Do not claim that hooks,
skills, `multi_agent`, or `[agents.*]` config force automatic delegation; they
only make delegation possible when the runtime policy allows it.

## References

- Read `README.md` for the human-facing architecture and core concepts.
- Read `references/local-setup.md` before applying the setup to a real machine.
- Use files in `assets/` as templates for rendered local files.

## Output Contract

Return:

- what local files were created or patched
- what backups were made
- what validations passed or failed
- which values still need user-specific replacement
- how to restart Codex and trust hooks
- whether optional hook-assisted read-only subagent delegation was enabled
- any remaining risk or unverified runtime behavior
