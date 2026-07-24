---
name: agent-nebius-auth-diagnose
description: Read-only diagnosis for current-session Nebius project discovery and Codex Agent authentication, tenant quota-read, CLI profile, selector, and hook failures. Use automatically when Nebius authentication is missing or a runtime auth hook blocks a command. Never create, repair, or mutate IAM, credentials, profiles, selectors, leases, or hooks.
---

# Agent Nebius Auth Diagnose

Diagnose current-session project and agent-auth state without mutation. Do not
write files, change permissions, update profiles, generate credentials, change
IAM, create repair leases, install hooks, or launch interactive authentication.

## Discover the Project

Resolve exactly one project ID in this order:

1. Explicit project ID in the current user turn.
2. Project ID labeled for this task in the conversation or injected task state.
3. Workspace configuration explicitly tied to this task.
4. Persistent memory only as a corroborated hint.
5. Ask when the result is absent or ambiguous.

Never infer authority from an active profile, inherited environment,
credential filename or count, cwd, a legacy default selector, or unrelated
task state.

Project selection is task execution context, not a typed skill argument.
Implicit skill selection and hook feedback do not inject a project ID into a
later tool call. After resolving exactly one project, carry that selection for
the current task only. Replace it when a later user turn explicitly selects a
different project. When no explicit current-turn project exists and task
evidence becomes conflicting, discard the prior selection and ask rather than
guessing. Never persist the selection into files, profiles, or ambient
environment state.

## Carry the Selected Project

Before every Bash tool call, decide which segments need Nebius auth. Help,
version, and path-discovery probes are sensitive when they invoke or inspect a
Nebius CLI.

If a proposed payload mixes local-only and Nebius-sensitive segments,
split it into separate Bash calls before execution. Leave the local-only call
unprefixed and apply the selector only to the Nebius-sensitive call.

For every Nebius-sensitive Bash payload:

- make exactly one `CODEX_NEBIUS_PROJECT_ID=<project-id>` assignment the first
  raw shell token at byte zero;
- prefix the entire outer Bash payload once, before local variable
  declarations, wrappers such as `env`, `timeout`, or `bash -c`, shell guards,
  pipelines, separators between Nebius-sensitive segments, command
  substitutions, or nested shell commands;
- never add the selector to each segment, place it inside a wrapper or nested
  script, or duplicate it; and
- leave local-only commands such as `git`, `rg`, and unrelated help probes
  unprefixed.

## Diagnose Read-Only

Inspect only the selected project's local surfaces:

- `~/.nebius/codex-agent-authkey.<project-id>.json` is an owned, regular,
  non-symlink mode-`0600` file with canonical service-account identity;
- `codex-agent-<project-id>` exists in read-only profile output;
- Nebius-sensitive commands start with exactly one leading
  `CODEX_NEBIUS_PROJECT_ID=<project-id>` assignment;
- any explicit profile agrees with selector-derived auth;
- installed hook files and registration are present when relevant.

Prefer setup's `verify --project-id <project_id>` read-only verifier so
diagnosis and repair use the same parser.

`verify` needs no human/admin profile. Success proves token minting, exact
profile/credential identity for the project's fixed `codex-agent-sa`, project
access, authoritative project-to-tenant ancestry, and tenant quota-allowance
listing. It does not prove the canonical IAM shape or absence of external
grants.

When explicitly useful, token-test only with stdout discarded:

```bash
CODEX_NEBIUS_PROJECT_ID=<project-id> \
  nebius iam get-access-token \
  --no-browser \
  --profile codex-agent-<project-id> >/dev/null
```

Never print or persist a token.

## Correct Command-Contract Denials

Retry without setup when the failure is only command shape:

- add exactly one leading selector if missing, invalid, duplicate, nested, or
  non-leading;
- remove an explicit profile that conflicts with selector-derived auth;
- remove assignments or unsets of managed auth variables;
- replace a token-printing command with the intended operation or the exact
  redirected verification form.

The canonical retry is:

```bash
CODEX_NEBIUS_PROJECT_ID=<project-id> <command>
```

When an authoritative project is already selected, do not rediscover it or run
`verify` for a selector-shape denial. Reconstruct the original outer payload
with the canonical leading selector and retry the corrected payload once. If a
later explicit project replaced the selection, resolve that project first. If
the corrected payload is denied again, classify and report the persistent
failure instead of looping or invoking setup.

A raw-token child uses:

```bash
python3 "$CODEX_NEBIUS_TOKEN_HELPER" exec-token -- <command> [args...]
```

Use `retry-idempotent` only for an explicitly idempotent operation whose real
401 or `UNAUTHENTICATED` result was mapped to exit status `77`.

## Classify Persistent Failures

Keep these outcomes distinct:

- Runtime verification succeeds but the non-agent administrative profile is
  unavailable: report `blocked-admin-auth` only if an explicit setup invocation
  later needs IAM mutation. Do not call this agent credential failure.
- Token mint succeeds but project access fails: report project-authorization
  drift.
- Project access succeeds but tenant quota-allowance listing is denied: report
  missing tenant read authorization. The canonical setup grant is tenant
  `viewer`, which is broader read-only access than quota alone.
- A valid repair lease covers only local credential-mode or profile drift:
  route to `repair-local`; never rotate credentials or change IAM under it.
- Project evidence conflicts: ask the user rather than guessing.

Do not enumerate or manage tenant IAM with the agent profile merely because
tenant `viewer` permits broader reads.

If persistent setup or repair is required, report the evidence and tell the
user to invoke `$agent-nebius-auth-setup` explicitly with the selected project.
Do not invoke setup implicitly, prepare an implicit setup dry-run, or ask for a
second confirmation after the user invokes it. Explicit setup invocation is
the authorization for its bounded canonical convergence.

## Report

Return:

- the authoritative project source;
- checks performed and their pass/fail state;
- classification of the failure;
- the corrected retry command when no mutation is required;
- otherwise, the exact explicit setup invocation needed.

Never include credential contents, private keys, access tokens, or secret
environment values.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.
