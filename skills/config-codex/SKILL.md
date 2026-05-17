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

## Safety Rules

- Never copy a live local config into a public repository.
- Never persist personal names, absolute home paths, secrets, tokens, private
  URLs, customer data, raw prompts, command output, stack traces, or broad
  environment dumps.
- Store secret values only in the user's shell, password manager, or external
  secret manager. In `config.toml`, reference secret variable names such as
  `CONTEXT7_API_KEY` or `GITHUB_TOKEN`.
- Back up existing local files before editing `AGENTS.md`, `config.toml`, or
  `hooks.json`.
- `AGENTS.md` and `config.toml` are patch-only when they already exist. Create
  them from templates only when they are missing.
- Do not overwrite, replace, reformat, sort, or regenerate an existing
  `AGENTS.md` or `config.toml`.
- Treat full-access settings as intended only for trusted local developer
  machines.

## Patch-Only Contract

For existing `$CODEX_HOME/AGENTS.md`:

- Preserve all unrelated user rules and ordering.
- Add a compact `config-codex` managed section only if the equivalent guidance
  is missing.
- If managed markers already exist, update only the content between those
  markers.
- Do not delete, rewrite, or deduplicate user-authored sections outside the
  managed block.

For existing `$CODEX_HOME/config.toml`:

- Parse the file before editing.
- Add only missing keys, tables, or `[[skills.config]]` entries needed for the
  requested setup.
- Preserve existing user values, comments, profiles, project trust entries,
  MCP servers, app settings, and unrelated feature flags.
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
4. Back up `AGENTS.md`, `config.toml`, and `hooks.json` when they exist.
5. Create missing local files from templates, but patch existing
   `AGENTS.md` and `config.toml` according to the patch-only contract.
6. Create the local layout:
   - `$CODEX_HOME/hooks/`
   - `$CODEX_HOME/agents/`
   - `$CODEX_HOME/task-state/`
7. Render or adapt templates from `assets/`:
   - `AGENTS.md.template`
   - `config.toml.template`
   - `hooks.json.template`
   - hook script templates
   - custom-agent TOML templates
   - task-state template
8. Keep `global-context-management` and `config-codex` installed or enabled as
   skill folders, not as paths to individual `SKILL.md` files.
9. Validate local hook scripts, TOML, JSON, feature flags, and secret hygiene.
10. Tell the user to restart Codex, open `/hooks`, review the two local hooks,
   and trust them only after confirming the paths are expected.

## Template Rules

Use placeholders in public assets:

- `$CODEX_HOME` for the user's Codex home.
- `$HOME` for the user's home directory.
- `$HOME/.agents/skills` for the default user skill install location.
- `<PROJECT_ROOT>` for the user's trusted repository root.

When writing rendered local files, replace placeholders with that user's real
paths. Do not commit rendered files. Treat full-file templates as source
material for missing files; for existing `AGENTS.md` and `config.toml`, extract
and patch only the missing sections or keys.

## Validation

Use the focused checks in `references/local-setup.md`. At minimum:

- Python-compile hook scripts.
- Parse `config.toml` with `tomllib`.
- Parse `hooks.json` with `json`.
- Confirm `codex features list` reports `hooks` and `multi_agent` enabled.
- Run a targeted secret/path scan over changed public files.

Do not claim runtime activation is proven until a fresh Codex session has
loaded the config, the hooks have been trusted in `/hooks`, and a non-mutating
probe shows the injected task-state path.

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
- any remaining risk or unverified runtime behavior
