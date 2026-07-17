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
```

The setup script must not patch `$CODEX_HOME/config.toml` or duplicate hook
registration logic. The installer owns hook files and `hooks.json`, does not
migrate inline `config.toml` hook entries or project selectors, and rejects
stale inline agent-nebius-auth hook entries before copying hooks or writing
`hooks.json`.

## Setup

Run setup only when the operator explicitly asks to bootstrap or repair local
Nebius auth:

```bash
bash scripts/agent-nebius-auth.sh ensure \
  --tenant-id <tenant_id> \
  --project-id <project_id>
```

You can also select the project by name under the tenant:

```bash
bash scripts/agent-nebius-auth.sh ensure \
  --tenant-id <tenant_id> \
  --project-name <project_name>
```

Useful optional flags:

- `--service-account-name <name>`: default `codex-agent-sa`
- `--role <role>`: default `admin`
- `--repair`: allow replacement of a broken credential after backup
- `--dry-run`: show the planned setup actions without changing IAM or local auth

Pass exactly one project selector. With `--project-id`, the script resolves the
project name from Nebius metadata only when group IAM work is needed. With
`--project-name`, it resolves the project ID before creating local profile and
credential paths. Passing both fails fast.

The setup path may create or repair Nebius IAM resources. Do not run it against
production or customer environments unless the operator explicitly asked for
that target and understands the credential and IAM changes.

The default permit grants the custom group project-level `admin`, which is a
broad authorization. Confirm the target project before running setup and use an
explicit narrower `--role` when full project administration is unnecessary.
Re-running setup with a valid human/admin profile adds the `admin` permit when
it is missing; it does not remove unrelated existing permits.

## Hook Install

Install or refresh the runtime hook from the skills repo root:

```bash
./install-skills.sh --install-hooks agent-nebius-auth/assets/hooks --register-hooks
```

This is idempotent. Re-running the command should leave matching payload and
registration state unchanged.

Codex runs matching hooks from all active hook sources. Do not keep an inline
`${CODEX_HOME:-$HOME/.codex}/config.toml` hook registration next to the
installer-managed `hooks.json` entry. The installer rejects stale inline
agent-nebius-auth hook entries before making changes; remove them manually
before registering this hook.

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
`NEBIUS_PROFILE`, `NEBIUS_PROJECT_ID`, and `NEBIUS_AUTH_CREDENTIALS_FILE` for
that process. The hook output contains the command substitution, not the token
value.

The rewritten shell also defines and calls `nebius_refresh_token` once before
the command starts, then wires child Bash processes through a restricted
temporary `BASH_ENV` helper file that contains the function definition but no
token value. The helper-owned `BASH_ENV` is isolated and does not source a
caller-supplied `BASH_ENV`, because ambient Bash startup files can print or
trace secret-bearing environments. For long-running Bash scripts that make raw
Bearer-token Nebius API calls, call `nebius_refresh_token` again near each
Nebius operation. Include `nebius_refresh_token` or another Nebius-sensitive
indicator in the top-level command so the hook knows to inject auth. Prefer
`NEBIUS_AUTH_CREDENTIALS_FILE`, the agent profile, or service-account/provider
auth for SDK, Terraform, and CLI clients that can refresh internally. If a tool
reads `NEBIUS_IAM_TOKEN` once and runs past the token lifetime, the hook cannot
refresh that already-running process from the outside.

For direct `nebius` CLI commands without an explicit profile, the hook rewrites
the command to use `--profile codex-agent-<project_id>`.

The hook fails closed for direct token-minting commands unless they are plain
manual agent-profile verification commands whose only stdout destination is
`/dev/null`. It also fails closed for executable nested token-minting commands,
shell tracing, full environment or shell-variable dumps, and output commands
that reference `TOKEN` or `NEBIUS_IAM_TOKEN`, including common nested shell or
Python forms, when token injection would be active.

Ordinary `echo` or `printf` status and log labels, strict-mode setup,
non-secret exports, named non-secret `printenv` reads, and `env VAR=value
command` wrappers remain allowed. Quoted search or documentation text that only
mentions the token-minting command is not treated as execution. The generated
wrapper also avoids literal secret-scanner-shaped assignments for the public
project selector and credential-file path so it can coexist with conservative
sibling policy hooks.

## Verification

Run these local checks from the skill folder after changing the skill:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/test_pre_tool_use_nebius_auth.py
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/test_agent_nebius_auth_setup.py
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/test_install_hook_registration.py
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

- Treat `PreToolUse hook (blocked)` as a local policy result, not proof that a
  credential expired or cluster access failed. Identify the hook and reason
  first; do not launch interactive or browser authentication from a policy
  denial. Current Codex runs matching command hooks concurrently, so separate
  hook messages must be classified independently.
- If the auth hook blocks a command that prints token variables, traces the
  shell, or dumps the environment, keep that command blocked and use bounded
  non-secret status output instead. Safe status and log labels should pass.
- Explicitly invoke `$agent-nebius-auth` for setup verification or repair only
  after token minting, the agent profile, the credential file, or a basic
  project-access check actually fails. Do not replace the service-account path
  with browser login as an automatic fallback.
- `--install-hook` is no longer supported by the setup script. Use the root
  installer command in the Hook Install section.
- Multiple credential files require either `CODEX_NEBIUS_PROJECT_ID` or
  `~/.nebius/codex-agent-default-project-id`.
- A stale inline hook block in `$CODEX_HOME/config.toml` must be removed
  manually before registering the canonical `hooks.json` entry; the installer
  rejects it before copying hook files or writing `hooks.json`.
- After hook changes, restart Codex and review or trust the hook with `/hooks`
  before relying on it.

## Files

- `SKILL.md`: agent-facing setup, hook behavior, guardrails, and validation.
- `assets/hooks/pre_tool_use_nebius_auth.py`: runtime `PreToolUse` hook.
- `assets/hooks.json.template`: installer-managed hook registration template.
- `scripts/agent-nebius-auth.sh`: setup and repair entry point.
- `scripts/test_agent_nebius_auth_setup.py`: fake Nebius CLI setup tests.
- `scripts/test_pre_tool_use_nebius_auth.py`: hook unit tests.
- `scripts/test_install_hook_registration.py`: installer registration,
  idempotency, and installed-hook runtime tests.
- `agents/openai.yaml`: UI metadata.

## Vendor Evidence

- OpenAI Codex Skills: <https://developers.openai.com/codex/skills>
- OpenAI Codex Hooks: <https://developers.openai.com/codex/hooks>
- Nebius access tokens:
  <https://docs.nebius.com/iam/authorization/access-tokens>
- Nebius roles for groups:
  <https://docs.nebius.com/iam/authorization/roles>
- Nebius `get-access-token` CLI reference:
  <https://docs.nebius.com/cli/reference/iam/get-access-token>
- Nebius authorized-key CLI reference:
  <https://docs.nebius.com/cli/reference/iam/auth-public-key>
- Nebius project `get` CLI reference:
  <https://docs.nebius.com/cli/reference/iam/project/get>
- Nebius project `get-by-name` CLI reference:
  <https://docs.nebius.com/cli/reference/iam/project/get-by-name>
