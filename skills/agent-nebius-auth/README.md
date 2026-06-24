# Agent Nebius Auth

`agent-nebius-auth` bootstraps and repairs local Codex Agent authentication for
Nebius projects. It creates or verifies a project-scoped service-account
credential, a Nebius CLI profile, and a local default project selector that the
runtime hook can read.

The skill is setup-only. Runtime token injection is handled by the installed
Codex `PreToolUse` hook in `assets/hooks/pre_tool_use_nebius_auth.py`.

## What It Does

- Creates or repairs `~/.nebius/codex-agent-authkey.<project_id>.json`.
- Creates or updates the `codex-agent-<project_id>` Nebius CLI profile.
- Records `~/.nebius/codex-agent-default-project-id` for the hook.
- Installs the hook only through the root `install-skills.sh` hook workflow.
- Rewrites Nebius-sensitive Bash commands so the shell mints a short-lived
  token at execution time.
- Avoids returning token values through hook output, model context, task state,
  docs, or final responses.

## Design

The workflow has two separate owners:

```text
operator request
  |
  +--> scripts/agent-nebius-auth.sh
  |     - create or repair service-account auth
  |     - configure codex-agent-<project_id> CLI profile
  |     - write ~/.nebius/codex-agent-default-project-id
  |
  `--> install-skills.sh --install-hooks agent-nebius-auth/assets/hooks --register-hooks
        - copy hook payload into $CODEX_HOME/hooks
        - merge hook registration into $CODEX_HOME/hooks.json
        - migrate the old marked inline config.toml block when present
```

The setup script must not patch `$CODEX_HOME/config.toml` or duplicate hook
registration logic. The installer owns hook files and `hooks.json`.

## Setup

Run setup only when the operator explicitly asks to bootstrap or repair local
Nebius auth:

```bash
bash scripts/agent-nebius-auth.sh ensure \
  --tenant-id <tenant_id> \
  --project-id <project_id> \
  --project-name <project_name>
```

Useful optional flags:

- `--service-account-name <name>`: default `codex-agent-sa`
- `--role <role>`: default `editor`
- `--repair`: allow replacement of a broken credential after backup
- `--dry-run`: show the planned setup actions without changing IAM or local auth

The setup path may create or repair Nebius IAM resources. Do not run it against
production or customer environments unless the operator explicitly asked for
that target and understands the credential and IAM changes.

## Hook Install

Install or refresh the runtime hook from the skills repo root:

```bash
./install-skills.sh --install-hooks agent-nebius-auth/assets/hooks --register-hooks
```

This is idempotent. Re-running the command should leave matching payload and
registration state unchanged.

When the installer sees an old marked inline `agent-nebius-auth` block in
`${CODEX_HOME:-$HOME/.codex}/config.toml`, it:

1. Preflights that the old project selector does not conflict with
   `~/.nebius/codex-agent-default-project-id`.
2. Migrates the old selector into
   `~/.nebius/codex-agent-default-project-id` when needed.
3. Backs up `config.toml`.
4. Removes only the marked managed block.
5. Keeps the project ID out of installer stdout and stderr.

If the selector conflicts, the installer fails before copying the hook payload
or writing `hooks.json`, so it does not create a duplicate active hook path.

## Runtime Behavior

On `PreToolUse` for `Bash`, the hook detects Nebius-sensitive commands such as
Nebius API calls, Nebius-related Terraform contexts, Nebius-related tests, and
direct Nebius CLI usage.

Project selection order is:

1. `CODEX_NEBIUS_PROJECT_ID`
2. `~/.nebius/codex-agent-default-project-id`
3. exactly one `~/.nebius/codex-agent-authkey.<project_id>.json` file

For API-style commands, the hook returns an `updatedInput.command` that runs:

```bash
nebius iam get-access-token --profile "$PROFILE"
```

inside the shell command and exports `TOKEN`, `NEBIUS_IAM_TOKEN`,
`NEBIUS_PROFILE`, and `NEBIUS_PROJECT_ID` for that process. The hook output
contains the command substitution, not the token value.

For direct `nebius` CLI commands without an explicit profile, the hook rewrites
the command to use `--profile codex-agent-<project_id>`.

The hook fails closed for direct token-minting commands unless they are plain
manual verification commands that redirect stdout away from model-visible logs.
It also fails closed for nested token-minting commands, shell tracing, and
obvious environment-printing commands when token injection would be active.

## Verification

Run these local checks from the skill folder after changing the skill:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/test_pre_tool_use_nebius_auth.py
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/test_agent_nebius_auth_setup.py
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/test_install_hook_migration.py
```

Run these checks from the skills repo root:

```bash
shellcheck install-skills.sh agent-nebius-auth/scripts/agent-nebius-auth.sh
bash -n install-skills.sh agent-nebius-auth/scripts/agent-nebius-auth.sh
python3 -m json.tool agent-nebius-auth/assets/hooks.json.template >/dev/null
python3 -B align-skill/scripts/validate-skill-structure.py agent-nebius-auth
markdownlint agent-nebius-auth/SKILL.md agent-nebius-auth/README.md README.md CHANGELOG.md
git diff --check
```

Manual token verification, when explicitly needed, must redirect token output
away from model-visible logs:

```bash
nebius iam get-access-token --profile codex-agent-<project_id> >/dev/null
```

## Troubleshooting

- `--install-hook` is no longer supported by the setup script. Use the root
  installer command in the Hook Install section.
- Multiple credential files require either `CODEX_NEBIUS_PROJECT_ID` or
  `~/.nebius/codex-agent-default-project-id`.
- A stale inline hook block should be removed by the root installer only when
  it is wrapped in the managed `agent-nebius-auth` markers.
- After hook changes, restart Codex and review or trust the hook with `/hooks`
  before relying on it.

## Files

- `SKILL.md`: agent-facing setup, hook behavior, guardrails, and validation.
- `assets/hooks/pre_tool_use_nebius_auth.py`: runtime `PreToolUse` hook.
- `assets/hooks.json.template`: installer-managed hook registration template.
- `scripts/agent-nebius-auth.sh`: setup and repair entry point.
- `scripts/test_agent_nebius_auth_setup.py`: fake Nebius CLI setup tests.
- `scripts/test_pre_tool_use_nebius_auth.py`: hook unit tests.
- `scripts/test_install_hook_migration.py`: installer migration and idempotency
  tests.
- `agents/openai.yaml`: UI metadata.

## Vendor Evidence

- OpenAI Codex Skills: <https://developers.openai.com/codex/skills>
- OpenAI Codex Hooks: <https://developers.openai.com/codex/hooks>
- Nebius access tokens:
  <https://docs.nebius.com/iam/authorization/access-tokens>
- Nebius `get-access-token` CLI reference:
  <https://docs.nebius.com/cli/reference/iam/get-access-token>
- Nebius authorized-key CLI reference:
  <https://docs.nebius.com/cli/reference/iam/auth-public-key>
