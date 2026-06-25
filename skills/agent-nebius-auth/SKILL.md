---
name: agent-nebius-auth
description: Setup-only Skill for operator-run bootstrap or repair of Codex Agent Nebius authentication using a project service account, custom group, project access permit, authorized-key credential file, CLI profile, and Codex PreToolUse hook. Use only when explicitly asked to set up, repair, verify, or install agent-nebius-auth for Nebius; do not use for runtime token injection or general Nebius cloud automation.
---

# Agent Nebius Auth

Use this Skill only for operator-run setup and repair of local Codex Agent
Nebius authentication.

Do not use this Skill as the runtime token mechanism. Runtime token handling
belongs to the installed Codex `PreToolUse` hook in
`assets/hooks/pre_tool_use_nebius_auth.py`.

## Inputs

Required:

- tenant ID: Nebius tenant ID that owns the project
- project ID: Nebius project ID where the agent will work
- project name: human-readable project name used to derive the group name

Defaults:

- service account: `codex-agent-sa`
- credential file: `~/.nebius/codex-agent-authkey.<project_id>.json`
- default project selector: `~/.nebius/codex-agent-default-project-id`
- CLI profile: `codex-agent-<project_id>`
- group: `codex-agent-<project-name-slug>`
- role: `editor`
- permission scope: project-level access permit on `<project_id>`

## Run

Run the setup script only when the operator explicitly asks to bootstrap or
repair the local Nebius auth state. If the prompt is not explicit, report the
command instead of running it.

```bash
bash scripts/agent-nebius-auth.sh ensure \
  --tenant-id <tenant_id> \
  --project-id <project_id> \
  --project-name <project_name>
```

Optional flags:

- `--service-account-name <name>`: default `codex-agent-sa`
- `--role <role>`: default `editor`
- `--repair`: allow replacement of a broken credential file after backing it up
- `--dry-run`: print the planned actions without modifying IAM or local files

When the operator explicitly asks to install or refresh the runtime hook, run
the root installer from the skills repo root:

```bash
./install-skills.sh --install-hooks agent-nebius-auth/assets/hooks --register-hooks
```

The root installer owns hook payload sync and `hooks.json` registration. The
setup script must not edit `$CODEX_HOME/config.toml`, shell out to the
installer, or duplicate installer merge logic; the skill workflow may run the
installer as a separate explicit hook-install step. The old `--install-hook`
flag fails fast with the installer command to use. Codex runs matching hooks
from all active sources, so do not keep a stale inline `config.toml`
registration next to the installer-managed `hooks.json` entry. When the root
installer registers this hook, it removes the old marked inline
`agent-nebius-auth` block from `$CODEX_HOME/config.toml`, backs that file up,
and migrates the legacy project selector to
`~/.nebius/codex-agent-default-project-id` without printing the project ID.

## Setup Behavior

Use `scripts/agent-nebius-auth.sh` as the single setup entry point. It is
idempotent and owns bootstrap, verification, and repair of the Nebius service
account credential and CLI profile. It intentionally does not install or
register Codex hooks. The root `install-skills.sh --install-hooks
agent-nebius-auth/assets/hooks --register-hooks` path is the canonical
payload/`hooks.json` registration mode for this hook.
After a successful setup, the script records the selected project ID in
`~/.nebius/codex-agent-default-project-id` so the generic installer-managed
hook can select the correct project-specific agent profile without an inline
`config.toml` environment assignment.

The script serializes setup with both a project-specific lock and a global
Nebius profile lock because `nebius profile create/update/activate` changes
global CLI profile state. During profile create/update it restores the previous
active profile, including on interrupted runs when possible. If no profile was
active but an explicit human/admin profile was selected through `NEBIUS_PROFILE`
or equivalent CLI profile resolution, it restores that effective human profile.

When the credential file exists, the script:

1. Repairs file permissions to `0600`.
2. Creates or updates the `codex-agent-<project_id>` CLI profile.
3. Preserves the previous active or effective human/admin Nebius CLI profile if
   profile creation or update temporarily switches the active profile.
4. Verifies that the profile can mint a service-account token.
5. Verifies basic project access through the service-account profile.
6. If a human/admin Nebius session is available, verifies and repairs IAM
   drift for the service account, group, access permit, and membership.
7. If token minting fails, replaces the credential file only when `--repair`
   is set and the current human/admin session can regenerate it.
8. Records the project ID in `~/.nebius/codex-agent-default-project-id` for the
   installer-managed runtime hook.

When the credential file does not exist, the script:

1. Verifies the current human/default Nebius session.
2. Creates or finds service account `codex-agent-sa` in the project.
3. Creates or finds group `codex-agent-<project-name-slug>` under the tenant.
4. Ensures the group has a project-level access permit with the configured role.
5. Ensures the service account is a member of the group.
6. Generates `~/.nebius/codex-agent-authkey.<project_id>.json`.
7. Creates the `codex-agent-<project_id>` CLI profile.
8. Restores the previous active or effective human/admin Nebius CLI profile if
   profile creation changed it.
9. Verifies service-account token minting and basic project access.
10. Records the project ID in `~/.nebius/codex-agent-default-project-id` for
    the installer-managed runtime hook.

If the credential file exists but is broken, the script must not delete or
overwrite it unless `--repair` is set and the human/admin Nebius session is
valid. Without that session, report the drift and stop.

An active `codex-agent-*` profile is not a valid human/admin session for IAM
repair. Treat that state as service-account-only unless the effective Nebius
CLI profile comes from an explicit human/admin selector such as
`NEBIUS_PROFILE`; verify existing agent access when possible, but do not
attempt tenant or project IAM repair through the agent profile.

## Hook Behavior

The hook assumes setup already happened. It must not call the Skill or repair
local auth state.

On `PreToolUse` for `Bash`, the hook:

1. Reads the pending tool call JSON from stdin.
2. Detects Nebius-sensitive commands such as Nebius API calls,
   Nebius-related Terraform contexts, Nebius-related tests, or direct Nebius
   CLI usage.
3. Resolves the project ID from `CODEX_NEBIUS_PROJECT_ID`,
   `~/.nebius/codex-agent-default-project-id`, or exactly one local
   `~/.nebius/codex-agent-authkey.<project_id>.json` file.
4. Derives the profile as `codex-agent-<project_id>`.
5. Rewrites the pending command so the shell process mints a short-lived token
   with `nebius iam get-access-token --profile "$PROFILE"` and exports
   `TOKEN`, `NEBIUS_IAM_TOKEN`, `NEBIUS_PROFILE`, and `NEBIUS_PROJECT_ID`.
6. For direct `nebius` CLI commands without an explicit profile, rewrites the
   command to use `nebius --profile codex-agent-<project_id>`.

Do not return tokens through `additionalContext`, stderr, stdout, task state,
docs, or final responses. The rewritten command contains only the token-minting
command substitution, not the token value. The hook must fail closed for direct
token-minting commands, nested token-minting commands, shell tracing, and
obvious environment-printing commands such as `echo`, `printf`, `printenv`,
`env`, `export`, or `set` when token injection would be active.

Do not inject Nebius credentials into every Terraform command globally. Treat
Terraform as Nebius-sensitive only when the command or nearby Terraform files
indicate Nebius usage.

## Verification

After setup, manual verification may use:

```bash
nebius iam get-access-token --profile codex-agent-<project_id> >/dev/null
```

Do not include the resulting token in transcripts, docs, task state, or final
responses.

When changing this Skill, re-check current Codex hook docs and current Nebius
CLI help for:

- `PreToolUse` `updatedInput.command` behavior
- `nebius iam auth-public-key generate`
- `nebius profile create`
- `nebius profile update`
- `nebius profile active`
- `nebius profile activate`
- `nebius profile current`
- `nebius iam service-account get-by-name`
- `nebius iam group get-by-name`
- `nebius iam access-permit create`
- `nebius iam group-membership create`

Run local regression checks after hook changes:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/test_pre_tool_use_nebius_auth.py
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/test_agent_nebius_auth_setup.py
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/test_install_hook_migration.py
```

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings back
into this skill's local source materials before completion when the current task
contract allows source edits. Update the narrowest appropriate surface:
`SKILL.md` for runtime rules, `scripts/` for deterministic setup logic, and
`assets/hooks/` for runtime hook behavior.

If the current task is explicitly read-only/report-only, or source writes are
outside this skill's task contract, do not edit skill sources; report that it
was skipped instead.

Do not capture secrets, private URLs, customer data, raw logs, one-off local
state, or unverified/vendor-specific claims. If a useful learning is not safe,
not evidence-backed, or outside this skill's scope, report that it was skipped.
