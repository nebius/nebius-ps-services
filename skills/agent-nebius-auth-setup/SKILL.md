---
name: agent-nebius-auth-setup
description: Explicitly bootstrap or repair Codex Agent Nebius authentication for one current-session project. Use only when the user invokes this skill directly. It manages the fixed codex-agent-sa account, one exact-permit tenant group, a project-bound credential and profile, optional bounded local-repair leases, and separately requested hook installation.
---

# Agent Nebius Auth Setup

## Help

For `$agent-nebius-auth-setup --help` or `$agent-nebius-auth-setup -h`, return concise help and stop before
any workflow step. State the purpose and invocation policy. Show exact usage
for every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

## Purpose

Set up or repair persistent Codex Agent authentication for one authoritative
Nebius project. Keep setup, read-only diagnosis, runtime token injection, repair
leases, and hook installation as separate boundaries.

## Invocation Policy

This skill is explicit-only. Direct invocation authorizes one bounded
convergence for the resolved target. Do not ask for another confirmation and do
not require a dry-run or plan digest before executing `ensure`.

Use `--dry-run` only when the user asks to preview, inspect, or review the
changes. A dry-run is read-only and does not create authority for a later run;
the later direct invocation is the authority.

Do not mutate IAM, credentials, profiles, selectors, repair leases, or hooks
when this skill was not explicitly invoked. Use
`$agent-nebius-auth-diagnose` for implicit read-only diagnosis.

## Discover the Current Session Project

Resolve exactly one project ID in this order:

1. An explicit project ID in the current user turn.
2. A project ID labeled for this task in the current conversation or injected
   task state.
3. Workspace configuration explicitly tied to this task.
4. The config-owned default Nebius CLI profile's configured `parent-id`,
   resolved with sanitized `nebius profile current` followed by
   `nebius config get parent-id --profile <profile>`.
5. Persistent memory only as a hint corroborated by current-session evidence.
6. Ask the user if no single authoritative candidate remains.

Explicit task evidence wins over the default-profile fallback. Clear ambient
Nebius selectors, profiles, credentials, tokens, and impersonation while
reading the default profile and accept only one valid configured project ID.
When equally authoritative task sources conflict, stop and ask. Never infer the
project from credential filenames, ambient profiles, inherited environment,
working-directory names, a legacy default selector, or unrelated task state.

## Canonical IAM Shape

Manage exactly one deterministic tenant-parented custom group for the selected
project. That group must contain exactly two permits and one membership:

- `admin` on the selected project;
- `viewer` on the authoritative parent tenant;
- membership of the fixed `codex-agent-sa` service account.

The group name is
`codex-agent-<first-20-hex-of-project-id-sha256>`. It depends only on the
immutable project ID, so a project rename cannot create a second managed group.
A tenant-created group can hold both the tenant and child-project permits;
using one group avoids a separate quota-specific group. The tenant `viewer`
role supplies the read-only tenant access needed for quota-allowance listing,
but is broader than quota access alone. Do not describe it as quota-only.

The project and tenant roles are fixed. Do not accept a role override, grant a
tenant write role, add the account to a default group, or preserve extra
permits on the managed group. If the managed group contains any other permit or
duplicate, fail before membership or permit mutation. Do not automatically
delete old groups, revoke external/direct grants, or claim those unrelated
grants are absent; report them as a separate IAM cleanup or audit when they are
in current-task scope.

The managed group must have exactly one member: the fixed `codex-agent-sa`.
Reject any extra or duplicate member across all membership pages before
mutation. This exactness applies only to the managed group; external groups and
direct grants remain outside automatic cleanup.

## Run Setup

From this skill directory:

```bash
bash scripts/agent-nebius-auth-setup.sh ensure \
  --project-id <project_id>
```

Optional arguments:

- `--tenant-id <expected_tenant_id>` asserts the resolved tenant; it is not a
  selector.
- `--dry-run` displays the resolved target, required actions, and any bounded
  conditional credential replacement without mutation.

The live path takes its local locks first, validates the current non-agent
administrative profile by minting a human-user access token with
`nebius iam get-access-token` while discarding stdout, resolves authoritative
project metadata once, and performs one convergence pass. It never prints or
persists that human token. All bootstrap IAM and authorized-key operations use
the validated human profile until the agent service account is ready. The path
creates or reuses the project-parented service account, reconciles the one
exact-permit group, ensures one membership, maintains the canonical credential
and profile, and verifies project access plus tenant quota-allowance listing.

An existing credential normally must resolve to `codex-agent-sa` under the
selected project before mutation. One exception is a failed get-by-ID whose
nonzero CLI result contains the provider-classified RPC/API `NotFound` marker:
explicit `ensure` treats that credential identity as deleted, creates or reuses
a distinct fixed service account through the human profile, reconciles its IAM
shape, generates and identity-checks one new authorized-key credential, then
creates one mode-`0600` backup and atomically replaces the stale file before
rebinding the profile. Authentication, authorization, transient, generic
"not found" text, malformed output, and unclassified lookup failures remain
fatal and never trigger this recovery. Credential files must be owned regular
non-symlink files. Canonical credential identity requires JWT/RS256 with equal
valid service-account issuer and subject values.

If a matching credential cannot mint a token with a classified credential
authentication error, `ensure` creates one mode-`0600` backup, replaces the
canonical credential at most once, rebuilds the profile, and tests again.
Profile create/update failures and unclassified or transient token failures
stop without replacing a credential. If the one replacement also fails, stop
without generating a second key or revoking anything automatically. New and
replacement credentials are generated in a private same-directory temporary
location, identity-checked, flushed, and atomically moved into place.

Except for the exact provider-classified deleted-identity case above, read,
authentication, and parse failures are not absence. Group membership lookup
follows all page tokens. Create conflicts count as convergence only after a
successful read proves the desired state. A working repeated `ensure` does not
rewrite IAM, credentials, or profiles.

## Verify Runtime Authentication Read-Only

`verify` requires no human/admin profile and never repairs:

```bash
bash scripts/agent-nebius-auth-setup.sh verify \
  --project-id <project_id>
```

It checks canonical credential safety and identity, binds the working profile's
reported service-account identity and the project lookup for the fixed
`codex-agent-sa` to that credential, verifies project access and authoritative
project-to-tenant ancestry, and performs a read-only tenant quota-allowance
list call. It does not enumerate IAM or prove that another workflow has not
granted additional access.

## Optional Local-Repair Lease

Only when explicitly requested, issue a repair lease for an already working
setup:

```bash
bash scripts/agent-nebius-auth-setup.sh repair-lease \
  --project-id <project_id> \
  --ttl-seconds 43200
```

The default is 12 hours and the maximum is 24 hours. Direct explicit invocation
authorizes issuance; no confirmation digest is required. The lease may
authorize only mode-`0600` correction of the exact credential and rebuilding
its exact profile. It cannot authorize IAM, credential generation or rotation,
identity changes, or hooks. Use `repair-local --lease-file <path>` only with a
valid exact-bound lease.

## Runtime Hook Boundary

Hook installation remains separate and explicit:

```bash
./install-skills.sh \
  --install-hooks agent-nebius-auth-setup/assets/hooks \
  --register-hooks
```

Runtime Nebius commands with an explicit task project opt in with exactly one
leading selector:

```bash
CODEX_NEBIUS_PROJECT_ID=<project-id> <command>
```

Without an explicit selector, the hook resolves the config-owned default
profile name and its configured `parent-id` under a sanitized environment,
then injects the matching renewable profile, project, credential-file, and
token-helper context. It does not persist a global selector or inject one
universal bearer token. Malformed or conflicting selectors fail closed, and a
raw-token child still requires an explicit selector:

```bash
python3 "$CODEX_NEBIUS_TOKEN_HELPER" exec-token -- <command> [args...]
```

Use `retry-idempotent` only for an explicitly idempotent operation whose real
401 or `UNAUTHENTICATED` result was mapped to exit status `77`.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Local Validation

```bash
python3 -B scripts/test_agent_nebius_auth_setup.py
python3 -B scripts/test_pre_tool_use_nebius_auth.py
python3 -B scripts/test_nebius_auth_runtime.py
python3 -B scripts/test_install_hook_registration.py
python3 -B scripts/test_agent_nebius_auth_contract.py
```
