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
- exactly one project selector:
  - project ID: Nebius project ID where the agent will work
  - project name: human-readable project name under the tenant

Defaults:

- service account: `codex-agent-sa`
- credential file: `~/.nebius/codex-agent-authkey.<project_id>.json`
- default project selector: `~/.nebius/codex-agent-default-project-id`
- CLI profile: `codex-agent-<project_id>`
- group: `codex-agent-<project-name-slug>`, resolved from the project metadata
- role: `admin`
- permission scope: project-level access permit on `<project_id>`

## Run

Run the setup script only when the operator explicitly asks to bootstrap or
repair the local Nebius auth state. If the prompt is not explicit, report the
command instead of running it.

```bash
bash scripts/agent-nebius-auth.sh ensure \
  --tenant-id <tenant_id> \
  --project-id <project_id>
```

or:

```bash
bash scripts/agent-nebius-auth.sh ensure \
  --tenant-id <tenant_id> \
  --project-name <project_name>
```

Optional flags:

- `--service-account-name <name>`: default `codex-agent-sa`
- `--role <role>`: default `admin`
- `--repair`: allow replacement of a broken credential file after backing it up
- `--dry-run`: print the planned actions without modifying IAM or local files

Project-level `admin` is intentionally broad. Before running live setup,
confirm that the selected project is the intended authorization boundary. Use
an explicit narrower `--role` when full project administration is unnecessary.

When the operator explicitly asks to install or refresh the runtime hook, run
the root installer from the skills repo root:

```bash
./install-skills.sh --install-hooks agent-nebius-auth/assets/hooks --register-hooks
```

The root installer owns hook payload sync and `hooks.json` registration. The
setup script must not edit `$CODEX_HOME/config.toml`, shell out to the
installer, or duplicate installer merge logic; the skill workflow may run the
installer as a separate explicit hook-install step. The unsupported
`--install-hook` flag fails fast with the installer command to use. Codex runs
matching hooks from all active sources, so do not keep an inline `config.toml`
registration next to the installer-managed `hooks.json` entry. This skill does
not migrate inline hook configuration or project selectors from
`$CODEX_HOME/config.toml`; the installer rejects stale inline agent-nebius-auth
hook entries before copying hooks or writing `hooks.json`. Remove any stale
inline hook entry manually before registering the canonical hook.

## Setup Behavior

Use `scripts/agent-nebius-auth.sh` as the single setup entry point. It is
idempotent and owns bootstrap, verification, and repair of the Nebius service
account credential and CLI profile. It intentionally does not install or
register Codex hooks. The root `install-skills.sh --install-hooks
agent-nebius-auth/assets/hooks --register-hooks` path is the canonical
payload/`hooks.json` registration mode for this hook.
The setup script requires a tenant ID plus exactly one project selector. If
`--project-id` is provided, the script resolves the project name from Nebius
project metadata only when group IAM work is needed. If `--project-name` is
provided, the script resolves the project ID with `nebius iam project
get-by-name` before creating local profile and credential paths. Passing both
selectors fails fast.
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
   drift for the service account, project-name-derived group, access permit,
   and membership.
7. If token minting fails, replaces the credential file only when `--repair`
   is set and the current human/admin session can regenerate it.
8. Records the project ID in `~/.nebius/codex-agent-default-project-id` for the
   installer-managed runtime hook.

When the credential file does not exist, the script:

1. Verifies the current human/default Nebius session.
2. Creates or finds service account `codex-agent-sa` in the project.
3. Creates or finds group `codex-agent-<project-name-slug>` under the tenant.
4. Ensures the group has a project-level access permit with the configured
   role, `admin` by default.
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
   with `nebius iam get-access-token --profile "$NEBIUS_PROFILE"` and exports
   `TOKEN`, `NEBIUS_IAM_TOKEN`, `NEBIUS_PROFILE`, `NEBIUS_PROJECT_ID`, and
   `NEBIUS_AUTH_CREDENTIALS_FILE`.
6. For direct `nebius` CLI commands without an explicit profile, rewrites the
   command to use `nebius --profile codex-agent-<project_id>`.

For API-style commands, the rewritten shell process also defines
`nebius_refresh_token`. The hook calls it once before the user command starts
and wires child Bash processes through a restricted temporary `BASH_ENV` helper
file that contains the function definition but no token value. The helper-owned
`BASH_ENV` is isolated and does not source a caller-supplied `BASH_ENV`, because
ambient Bash startup files can print or trace secret-bearing environments.
Long-running Bash scripts that use raw Bearer-token API calls must call
`nebius_refresh_token` again near each Nebius operation, or use clients that
read `NEBIUS_AUTH_CREDENTIALS_FILE` or the agent CLI profile and can refresh
auth internally. A command that relies on this helper must include
`nebius_refresh_token` or another Nebius-sensitive indicator so the hook knows
to inject auth. A hook cannot refresh environment variables inside an already
running process.

Do not return tokens through `additionalContext`, stderr, stdout, task state,
docs, or final responses. The rewritten command contains only the token-minting
command substitution, not the token value. The wrapper routes the project ID
and credential-file path through non-sensitive local variables before exporting
the documented Nebius environment names so conservative sibling policy hooks do
not mistake public metadata or file paths for token values.

The hook must fail closed for executable direct or nested token-minting
commands, shell tracing, full environment or shell-variable dumps, and output
commands that reference `TOKEN` or `NEBIUS_IAM_TOKEN`, including common nested
shell or Python forms, when token injection would be active. It must allow
ordinary status and log labels through `echo` or `printf`, strict-mode setup,
non-secret exports, named non-secret `printenv` reads, and `env VAR=value
command` wrappers. Literal or quoted documentation/search text that merely
mentions the token-minting command is not an executable token request. The only
manual token-mint exception is one exact agent-profile verification command
whose sole stdout destination is `/dev/null`.

Do not inject Nebius credentials into every Terraform command globally. Treat
Terraform as Nebius-sensitive only when the command or nearby Terraform files
indicate Nebius usage.

## Failure Triage

A `PreToolUse hook (blocked)` result is a local policy classification, not
evidence that the Nebius credential expired, the CLI profile failed, or cluster
access is unavailable. Do not start interactive or browser authentication from
that signal.

First identify which hook produced the message. For an auth-hook disclosure
denial, confirm whether the command actually prints token variables, enables
shell tracing, or dumps the environment; safe status and log output should not
require a workaround. For a separate policy hook, inspect that hook's reason
independently because current Codex runs matching command hooks concurrently.
Only when token minting, the agent CLI profile, the credential file, or a basic
project-access check actually fails should the operator explicitly invoke
`$agent-nebius-auth` to verify or repair setup. Never replace the agent profile
with browser login as an automatic fallback.

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
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/test_install_hook_registration.py
```

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.
