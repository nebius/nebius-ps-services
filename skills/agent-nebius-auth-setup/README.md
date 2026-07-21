# Agent Nebius Auth Setup

`agent-nebius-auth-setup` discovers the Nebius project for the current Codex
session, validates its authoritative metadata, and prepares or explicitly
applies project-scoped agent authentication. `agent-nebius-auth-diagnose`
handles read-only runtime triage.

## Safety Contract

- Implicit invocation may discover, diagnose, and dry-run only.
- Current-session project evidence outranks workspace config and persistent
  memory; memory alone requires user confirmation.
- Active profiles, credential filenames, cwd, legacy default selectors, and
  unrelated task-state are never project authority.
- Project metadata is validated before any filesystem, profile, credential, or
  IAM mutation.
- First-time setup asks once to confirm service-account creation and the full
  bounded same-target convergence envelope. Partial convergence reuses that
  confirmation without another prompt until read-only verification succeeds.
- Existing credential identity must resolve to `codex-agent-sa` in the selected
  project before permissions or profiles can change.
- The service account and dedicated group are project-parented, and the only
  permit setup creates targets that same project. The tenant ID is metadata and
  profile context, never a managed group or permit scope.
- Role reconciliation is additive within that project.
- A separately requested and confirmed, expiring repair lease may authorize
  only the exact bound credential-mode correction and profile rebuild. It cannot authorize
  IAM, credential generation or rotation, identity, or hook changes.

## Setup

From this directory, review the real read-only plan:

```bash
bash scripts/agent-nebius-auth-setup.sh ensure \
  --project-id <project_id> \
  --dry-run
```

After the one explicit confirmation of service-account creation and the
displayed envelope, apply the plan:

```bash
bash scripts/agent-nebius-auth-setup.sh ensure \
  --project-id <project_id> \
  --confirm <plan_digest>
```

Use the digest printed by the reviewed dry-run. The live setup recomputes the
state-bound plan before mutation and after taking its local locks. The digest
binds the tenant/project/name, service-account and group identities, additive
role, canonical paths/profile, credential fingerprint, administrative profile,
endpoint, complete bounded action set, current mutations, and plan notes.
Partial progress invalidates the stale digest. Re-run the read-only plan,
compare its target and authorization envelope with the one-time confirmation
record, and use the fresh digest automatically without another user prompt.
Target/configuration, credential-identity, or authorized-action drift fails
closed. Keep only the non-secret confirmation record and whether the one
credential replacement was attempted in injected private task state until
`verify` succeeds, then discard the bootstrap authorization.

The plan displays the observed service-account ID, group ID, and credential
SHA-256. An originally absent account or group may acquire one created ID; once
an ID is observed, a different ID requires new user confirmation even when its
name is unchanged. An absent credential may acquire its first fingerprint. A
fingerprint may change once more only after the replacement-attempt flag is set.

`--tenant-id` is an optional exact-match assertion. `--project-name` is not
supported; the name and tenant are derived from `nebius iam project get`.
Project group names include a project-ID hash to avoid normalized-name
collisions. A legacy slug-only group is reported for separate operator cleanup
and is never deleted by setup. Tenant-parented groups are outside this workflow
and are never discovered, reused, modified, or deleted automatically.
The reviewed envelope always includes one conditional credential backup and
replacement, so initial key failure or a matching broken credential can
converge without a second user confirmation.

`ensure` does not replace a credential internally. If it reports
`credential-replacement-required`, first mark the replacement attempt in
private task state, then review the separate state-bound phase:

```bash
bash scripts/agent-nebius-auth-setup.sh replace-credential \
  --project-id <project_id> \
  --dry-run
```

Validate its displayed identities and fingerprint against the recorded
bootstrap, then apply its fresh digest automatically. Because the attempt is
marked before invocation, interruption cannot authorize a second key.

Runtime verification is separately read-only and does not require a
human/admin profile:

```bash
bash scripts/agent-nebius-auth-setup.sh verify \
  --project-id <project_id>
```

It validates the canonical credential, matching profile token mint, and basic
project access. It does not reconcile IAM, enumerate tenant IAM, or prove the
absence of grants created by another workflow. `ensure --dry-run` still requires
a non-agent administrative profile for an exact IAM plan and reports
`blocked-admin-auth` instead of launching an interactive login.

`ensure` is convergent: a working matching profile is unchanged, and missing
resources are created only when the reviewed plan requires them. Credential
identity is accepted only when `subject-credentials.type` is `JWT`, `alg` is
`RS256`, and `iss` and `sub` are equal valid `serviceaccount-*` IDs. Setup never
deletes service accounts or automatically revokes credentials.
Cloud and local-profile lookup, authentication, and parse failures are never
interpreted as missing resources. Membership reconciliation follows every
returned page token. New and replacement credentials are generated in a
private same-directory temporary location, validated before atomic replacement,
and cleaned up on failure. A failed new or matching credential gets at most one
explicit mode-`0600` backup-and-replacement phase inside the confirmed envelope.
A failed or interrupted replacement stops without another prompt, fresh digest,
further key generation, or automatic revocation.

After setup, token verification, and basic project-access verification succeed,
a long-running task may review and confirm a 12-hour local-repair lease only
when the user explicitly requests it (24-hour maximum):

```bash
bash scripts/agent-nebius-auth-setup.sh repair-lease \
  --project-id <project_id> \
  --ttl-seconds 43200 \
  --dry-run
```

Repeat with `--confirm <plan_digest>`, retain the reported path only in private
task state, and use `repair-local --lease-file <canonical_lease_path>` if
diagnose later proves only bound permission/profile drift. The lease is a
same-user workflow preauthorization record, not an unforgeable security token.
It binds the exact identity, paths, credential fingerprint, actions, and expiry
and fails closed on drift. A future standalone persistent key/IAM repair, after
the original bootstrap is complete, requires one new reviewed confirmation.

Hook installation is outside the bootstrap envelope and runs only when the user
explicitly requests it; normal setup does not ask another confirmation:

```bash
./install-skills.sh \
  --install-hooks agent-nebius-auth-setup/assets/hooks \
  --register-hooks
```

## Runtime Contract

Any Bash command that needs Nebius auth opts in with exactly one selector. The
executable may be Bash, Python, Nebius CLI, `nebius-cxcli`, Terraform, or any
other API/SDK client:

```bash
CODEX_NEBIUS_PROJECT_ID=<project-id> <command>
```

The hook does not use inherited environment, a legacy default-selector file,
or a single credential filename as fallback. It strips the selector and injects
the same renewable context for every selected executable:
`NEBIUS_PROFILE`, `NEBIUS_PROJECT_ID`, `NEBIUS_AUTH_CREDENTIALS_FILE`, and
`CODEX_NEBIUS_TOKEN_HELPER`. It clears `TOKEN` and `NEBIUS_IAM_TOKEN` rather
than injecting one expiring bearer token universally. Conflicting managed auth,
environment isolation/splitting, disclosure-prone commands, unsafe credentials,
and noncanonical credential identities fail closed. Hook failures route to
`$agent-nebius-auth-diagnose`.

Normal CLI commands use the selected profile. Supported SDKs use the credential
file or profile and own renewal. A raw-token Bash, Python, or API child uses:

```bash
python3 "$CODEX_NEBIUS_TOKEN_HELPER" exec-token -- <command> [args...]
```

An explicitly idempotent adapter may map only an actual
401/`UNAUTHENTICATED` response to status `77` and use `retry-idempotent` for one
refresh and retry. The helper places `NEBIUS_IAM_TOKEN` only in the child, never
in argv or the parent shell. Already-running processes cannot receive a changed
environment; they must use provider-native renewable credentials, invoke the
helper per bounded request, or restart.

## Local Validation

```bash
python3 -B agent-nebius-auth-setup/scripts/test_pre_tool_use_nebius_auth.py
python3 -B agent-nebius-auth-setup/scripts/test_nebius_auth_runtime.py
python3 -B agent-nebius-auth-setup/scripts/test_agent_nebius_auth_setup.py
python3 -B agent-nebius-auth-setup/scripts/test_install_hook_registration.py
python3 -B agent-nebius-auth-setup/scripts/test_agent_nebius_auth_contract.py
python3 -B align-skill/scripts/validate-skill-structure.py \
  agent-nebius-auth-setup agent-nebius-auth-diagnose
```
