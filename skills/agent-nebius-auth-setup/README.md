# Agent Nebius Auth Setup

`agent-nebius-auth-setup` is the explicit-only mutation path for persistent
Codex Agent authentication. `agent-nebius-auth-diagnose` remains the implicit,
read-only entry point.

## Managed State

For one authoritative project, setup manages:

- one project-parented `codex-agent-sa` service account;
- one deterministic tenant-parented custom group;
- exactly `admin` on the project and `viewer` on its parent tenant;
- one service-account membership in that group;
- one project-bound credential and Nebius CLI profile.

Tenant `viewer` supplies the read-only tenant access needed to list quota
allowances, but it is broader than quota access alone. Setup therefore does not
create a quota-specific group. It rejects extra or duplicate permits on its
managed group, rejects extra or duplicate members, and never grants tenant
`editor` or `admin`.

The managed group name is derived only from the project ID hash, so project
renames do not change its identity. Old groups, direct grants, credentials, and
memberships created by other workflows are outside automatic cleanup. Review
or remove them separately.

## Setup

Explicit invocation itself authorizes the bounded convergence; there is no
second prompt or confirmation digest:

```bash
bash scripts/agent-nebius-auth-setup.sh ensure \
  --project-id <project_id>
```

Use `--tenant-id` only as an exact tenant assertion. The service-account name
is fixed to `codex-agent-sa`. Use `--dry-run` for a read-only preview, including
any conditional one-time credential replacement that live convergence could
need after profile rebuilding.

The live path locks first, validates the current non-agent human profile with
`nebius iam get-access-token` while discarding the token, resolves the project
once, and converges in one pass. Human-profile authentication owns project/IAM
discovery, service-account and group convergence, and authorized-key generation
until the agent profile is ready; setup never prints or persists the human
token.

If an existing credential's service-account ID returns the provider's exact
RPC/API `NotFound` classification, explicit setup treats that identity as
deleted. It creates or reuses a distinct fixed account, reconciles its exact IAM
shape, generates and checks one new authorized-key credential, then makes one
mode-`0600` backup and atomically replaces the stale file before rebinding the
profile. Permission, authentication, transport, parse, generic "not found",
and other unclassified failures remain fail-closed. If a current canonical
credential instead fails with a classified authentication error, setup backs
it up and replaces it at most once during that invocation. Profile write errors
and transient or unclassified token failures never trigger key replacement.
Setup never loops on key creation or automatically revokes credentials.

Read-only runtime verification requires no administrative profile:

```bash
bash scripts/agent-nebius-auth-setup.sh verify \
  --project-id <project_id>
```

It verifies credential safety, exact profile-to-credential service-account
identity, project access, project-to-tenant ancestry, and tenant
quota-allowance listing. It does not reconcile or enumerate IAM.

## Optional Repair Lease

An explicitly requested repair lease needs no additional confirmation:

```bash
bash scripts/agent-nebius-auth-setup.sh repair-lease \
  --project-id <project_id> \
  --ttl-seconds 43200
```

The lease can authorize only mode-`0600` correction of the exact credential
and rebuilding its exact profile. IAM, credential generation or rotation,
identity changes, and hook changes remain excluded.

## Runtime Hook

Hook installation is separate and explicit:

```bash
./install-skills.sh \
  --install-hooks agent-nebius-auth-setup/assets/hooks \
  --register-hooks
```

Selected commands use:

```bash
CODEX_NEBIUS_PROJECT_ID=<project-id> <command>
```

The hook injects renewable project/profile/credential context and clears
ambient bearer variables. Raw-token children use the injected protected helper
rather than printing or persisting tokens.

## Validation

```bash
python3 -B scripts/test_agent_nebius_auth_setup.py
python3 -B scripts/test_pre_tool_use_nebius_auth.py
python3 -B scripts/test_nebius_auth_runtime.py
python3 -B scripts/test_install_hook_registration.py
python3 -B scripts/test_agent_nebius_auth_contract.py
python3 -B ../align-skill/scripts/validate-skill-structure.py \
  . ../agent-nebius-auth-diagnose
```
