---
name: agent-nebius-auth-diagnose
description: Read-only diagnosis for current-session Nebius project discovery and Codex Agent authentication failures, including missing per-command project selectors, missing or mismatched project credentials, CLI profile checks, and hook denials. Use automatically when Nebius work needs authentication or the auth hook blocks a command; never create, repair, or mutate IAM resources, credentials, profiles, selectors, or hooks.
---

# Agent Nebius Auth Diagnose

Diagnose the current session's project and local agent-auth state without making
changes. Do not write files, modify permissions, activate or update profiles,
generate credentials, change IAM, install hooks, or launch interactive/browser
authentication. You may hand off implicitly to `$agent-nebius-auth-setup` for a
read-only dry-run plan; implicit setup never authorizes mutation.

## Discover the Project

Resolve exactly one project ID in this order:

1. Explicit project ID in the user's current turn.
2. Project ID labeled for this task in the current conversation or current
   injected task-state file.
3. Active workspace configuration explicitly tied to this task.
4. Persistent memory only as a hint requiring current corroboration or user
   confirmation.
5. Ask the user when the result is absent or ambiguous.

Never infer the project from an active profile, inherited environment,
credential filename/count, cwd, legacy default selector, or unrelated prior
task-state.

## Diagnose Read-Only

Once the project is authoritative, inspect only the matching local surfaces:

- `~/.nebius/codex-agent-authkey.<project-id>.json` exists and is a regular
  owned non-symlink file at mode `0600` with a service-account ID; do not print
  its content;
- `codex-agent-<project-id>` exists in read-only profile listing;
- the requested Bash command starts exactly with the
  `CODEX_NEBIUS_PROJECT_ID=<project-id>` assignment followed by one space;
- any explicit CLI profile is the selector-derived `codex-agent-*` profile;
- the installed hook path and registration are present when relevant.

Prefer the setup skill's read-only runtime verifier so diagnosis uses the same
canonical parser and bounded non-interactive checks as repair. Hand off to
`$agent-nebius-auth-setup` in read-only mode and request its `verify` command
for the authoritative project ID.

This check does not need a human/admin profile. A successful result means the
current agent credential/profile can mint and reach the project; it does not
prove that IAM resources match the desired setup plan or that another workflow
did not grant broader access. Do not use the agent profile to list or manage
tenant-wide IAM; a tenant-scope denial is expected for project-only auth.

When explicitly useful, verify token minting only with stdout discarded and
the matching selector/profile. Never expose the token:

```bash
CODEX_NEBIUS_PROJECT_ID=<project-id> \
  nebius iam get-access-token \
  --no-browser \
  --profile codex-agent-<project-id> >/dev/null
```

Classify a hook denial separately from credential failure. Correct these
command-contract failures and retry without setup or user confirmation:

- add exactly one leading selector when it is missing, invalid, duplicate,
  nested, or non-leading;
- remove an explicit profile that conflicts with the selector-derived profile;
- remove assignments or unsets of managed authentication variables;
- replace a token-printing command with the intended normal Nebius command, or
  use only the exact redirected verification form shown above.

Retry with exactly:

```bash
CODEX_NEBIUS_PROJECT_ID=<project-id> <command>
```

For a raw-token child, use the helper path exported by the selector-derived
runtime context:

```bash
python3 "$CODEX_NEBIUS_TOKEN_HELPER" exec-token -- <command> [args...]
```

For a shell-controlled, idempotent operation, map only a real HTTP 401 or gRPC
`UNAUTHENTICATED` response to exit status `77`, then invoke
`retry-idempotent` instead of `exec-token`. The protected helper refreshes
without printing the token and retries exactly once. Never use retry mode for
non-idempotent work or map unrelated failures to `77`.

If runtime verification passes but exact IAM planning cannot authenticate the
required non-agent profile without a browser, report `blocked-admin-auth` as an
administrative planning blocker, not an agent-runtime failure. Do not launch an
interactive login.

If persistent setup or repair is required, report the evidence and hand off to
`$agent-nebius-auth-setup`. You may run its read-only dry-run implicitly. For
first-time bootstrap, ask once to confirm creation of the resolved service
account and the displayed bounded same-target convergence envelope. Do not ask
again for partial convergence while the recorded tenant, project, account
name/identity, group, role, paths, profile, endpoint, and administrative profile
still match. Re-run setup's read-only plan, validate its target and authorization
envelope against the private confirmation record, and use its fresh state-bound
digest automatically until read-only `verify` succeeds. Target, identity, or
authorized-action drift fails closed and requires a new plan.

When setup reports `credential-replacement-required`, do not retry `ensure` to
generate another key. Hand off to setup's separate state-bound replacement
phase; it must record the replacement attempt before invocation and must stop
after failure or interruption without asking the user again.

A future standalone persistent repair, optional repair lease, or hook
installation is outside that completed bootstrap authorization and runs only
after an explicit user request. An existing confirmed repair lease may
authorize only its bound local permission/profile repair during its lifetime.

See [`evals/trigger-prompts.md`](evals/trigger-prompts.md) for trigger and
failure-routing cases.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.
