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
- CLI profile: `codex-agent-<project_id>`
- group: `codex-agent-<project-name-slug>`
- role: `editor`
- permission scope: project-level access permit on `<project_id>`

## Run

Run the setup script only when the operator explicitly asks to bootstrap,
repair, or install the local auth hook. If the prompt is not explicit, report
the command instead of running it.

```bash
bash scripts/agent-nebius-auth.sh ensure \
  --tenant-id <tenant_id> \
  --project-id <project_id> \
  --project-name <project_name> \
  --install-hook
```

Optional flags:

- `--service-account-name <name>`: default `codex-agent-sa`
- `--role <role>`: default `editor`
- `--repair`: allow replacement of a broken credential file after backing it up
- `--install-hook`: add or update the Codex `PreToolUse` hook block
- `--dry-run`: print the planned actions without modifying IAM or local files

If the skill and hook payload are installed separately with the root installer,
register the runtime hook with:

```bash
./install-skills.sh --install-hooks agent-nebius-auth/assets/hooks --register-hooks
```

Use either this installer-managed `hooks.json` registration or the setup
script's `--install-hook` inline `config.toml` block for a given machine, not
both. Codex runs matching hooks from all active sources, so duplicate
registrations can make the Nebius auth hook run more than once.

## Setup Behavior

Use `scripts/agent-nebius-auth.sh` as the single setup entry point. It is
idempotent and owns bootstrap, verification, repair, and its inline
`config.toml` hook registration mode. The root `install-skills.sh
--install-hooks agent-nebius-auth/assets/hooks --register-hooks` path is a separate
payload/`hooks.json` registration mode for operators who already installed the
skill and do not want the setup script to manage inline hook config.

When the credential file exists, the script:

1. Repairs file permissions to `0600`.
2. Creates or updates the `codex-agent-<project_id>` CLI profile.
3. Verifies that the profile can mint a service-account token.
4. Verifies basic project access through the service-account profile.
5. If a human/admin Nebius session is available, verifies and repairs IAM
   drift for the service account, group, access permit, and membership.
6. If token minting fails, replaces the credential file only when `--repair`
   is set and the current human/admin session can regenerate it.

When the credential file does not exist, the script:

1. Verifies the current human/default Nebius session.
2. Creates or finds service account `codex-agent-sa` in the project.
3. Creates or finds group `codex-agent-<project-name-slug>` under the tenant.
4. Ensures the group has a project-level access permit with the configured role.
5. Ensures the service account is a member of the group.
6. Generates `~/.nebius/codex-agent-authkey.<project_id>.json`.
7. Creates the `codex-agent-<project_id>` CLI profile.
8. Verifies service-account token minting and basic project access.
9. Installs or updates the `PreToolUse` hook when `--install-hook` is set.

If the credential file exists but is broken, the script must not delete or
overwrite it unless `--repair` is set and the human/admin Nebius session is
valid. Without that session, report the drift and stop.

## Hook Behavior

The hook assumes setup already happened. It must not call the Skill or repair
local auth state.

On `PreToolUse` for `Bash`, the hook:

1. Reads the pending tool call JSON from stdin.
2. Detects Nebius-sensitive commands such as Nebius API calls,
   Nebius-related Terraform contexts, Nebius-related tests, or direct Nebius
   CLI usage.
3. Resolves the project ID from `CODEX_NEBIUS_PROJECT_ID`, or from exactly one
   local `~/.nebius/codex-agent-authkey.<project_id>.json` file.
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
- `nebius iam service-account get-by-name`
- `nebius iam group get-by-name`
- `nebius iam access-permit create`
- `nebius iam group-membership create`

Run local regression checks after hook changes:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/test_pre_tool_use_nebius_auth.py
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
