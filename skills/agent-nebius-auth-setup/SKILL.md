---
name: agent-nebius-auth-setup
description: Discover the Nebius project for the current Codex session, prepare a reviewed setup plan, and bootstrap or repair project-scoped Codex Agent authentication with a service account, group permit, credential file, CLI profile, optional bounded local-repair lease, and runtime hook installation. Use when agent authentication is missing or broken and setup may be needed; implicit use is diagnosis and planning only. Ask once to confirm service-account creation and its bounded same-target convergence envelope; do not ask again while that confirmed bootstrap is still converging.
---

# Agent Nebius Auth Setup

Discover the current session's Nebius project before preparing or running auth
setup. Keep project discovery, persistent setup mutation, renewable runtime
context, and short-lived bearer execution as separate boundaries.

## Invocation Policy

Allow implicit invocation for read-only project discovery, diagnosis, and plan
preparation only. Never mutate IAM, credentials, Nebius CLI profiles, local
selectors, or Codex hooks during implicit use.

The displayed plan must precede explicit confirmation.
Every IAM, credential, profile, or hook mutation requires explicit current-turn
confirmation; the service-account bootstrap confirmation is asked once and then
reused only for its matching convergence envelope.

For first-time bootstrap, ask exactly once to confirm creation of the resolved
service account and the complete bounded convergence envelope:

1. Display the resolved tenant ID, project ID, project name, service-account
   name, group, additive role, local credential/profile paths, every authorized
   conditional convergence action, and the actions currently required.
2. Ask once for explicit current-turn confirmation to create that service
   account and complete its displayed same-target auth bootstrap.
3. Record the confirmation digest and non-secret target fields in the injected
   private task state, including the observed service-account ID, group ID,
   credential SHA-256, and whether the one conditional credential replacement
   was attempted, until read-only `verify` succeeds.
4. Do not ask again when a partial run needs another state-bound attempt for the
   same tenant, project, service-account name and identity, group, role, paths,
   profile, endpoint, and administrative profile. Re-run the read-only plan and
   continue automatically with its fresh digest only when those fields and the
   authorized convergence actions still match.

Treat identity transitions explicitly. An observed `absent` service-account or
group ID may transition once to a created ID only when the original displayed
plan authorized creating it. After an ID is observed, any change requires a new
user confirmation, even if the resource name is unchanged. The credential
fingerprint may transition from `absent` to the first generated credential. It
may change once more only through the separate replacement phase below. Any
other fingerprint change fails closed.

The setup script hashes a versioned, state-bound plan containing the resolved
resource IDs, credential fingerprint, authorized convergence actions, current
mutations, and plan notes. Use `--dry-run` for the reviewed plan, then pass its
digest to `--confirm <plan_digest>` after the one user confirmation. After
partial progress, discard the stale digest, re-run the read-only plan, compare
the target and full authorization envelope with the private confirmation
record, and pass the fresh digest automatically without another user prompt.
Target/configuration drift, credential identity mismatch, or any action outside
the recorded envelope fails closed and requires a new plan; never reinterpret
an old confirmation as authority for another state, project, or account.

If a matching service account already exists and no confirmed bootstrap is
active, a standalone persistent repair still requires one reviewed
confirmation. Optional repair-lease issuance and hook installation are separate
operations only when the user explicitly requests them; do not interrupt normal
bootstrap to propose or confirm them.

## Discover the Current Session Project

Resolve exactly one project ID in this order:

1. An explicit project ID in the user's current turn.
2. A project ID labeled for this task in the current conversation or the
   current session's injected durable task-state file.
3. Active workspace configuration explicitly tied to the current task.
4. Persistent memory only as a bounded hint. Corroborate it with current-session
   evidence or ask the user to confirm it.
5. Ask the user for the project ID when no single authoritative candidate
   remains.

When sources at the same authoritative level conflict, stop and ask. Prefer
current-session evidence over stale memory. Never infer the project from:

- credential filenames or the number of credential files;
- an active Nebius profile or inherited environment variable;
- the working-directory name;
- `~/.nebius/codex-agent-default-project-id`;
- unrelated or older task-state.

Do not search broadly through unrelated session history. Keep state and memory
lookups bounded to the current task and directly referenced entries. See
[`evals/trigger-prompts.md`](evals/trigger-prompts.md) when validating trigger
and discovery behavior.

## Review the Setup Plan

Run from this skill directory:

```bash
bash scripts/agent-nebius-auth-setup.sh ensure \
  --project-id <project_id> \
  --dry-run
```

Optional assertions and overrides:

- `--tenant-id <expected_tenant_id>`: assert an exact tenant match; it is not a
  discovery selector.
- `--service-account-name <name>`: default `codex-agent-sa`.
- `--role <role>`: default `admin`.

The confirmation envelope always discloses one conditional credential
backup-and-replacement action. It covers the newly generated credential and a
matching existing credential without a second prompt, while identity and target
validation remain mandatory. `ensure` never performs that replacement
internally. When it reports `credential-replacement-required`, first set
`credential_replacement_attempted: true` in injected private task state, then
run the separate state-bound replacement phase. Mark the attempt before
invocation, so interruption cannot authorize a second key.

The dry-run performs real read-only authentication and
`nebius iam project get --id ... --format json` resolution. It derives the
project name and tenant from project metadata and performs no filesystem,
profile, credential, or IAM mutation. Never substitute invented dry-run IDs or
names.

Role reconciliation is additive: the requested permit is ensured, but broader
roles already attached to the dedicated group for the same project are
preserved. Report reconciliation factually; do not add broader- or
narrower-role advice unless the user asks for it.

## Project Scope Invariant

Keep every managed identity and grant structurally inside the selected project:

- create and look up both `codex-agent-sa` and its dedicated group with
  `--parent-id <project_id>`;
- create the access permit under that group with
  `--resource-id <project_id>`;
- use the resolved tenant ID only as an exact project-parent assertion and CLI
  profile field, never as the group parent or access-permit resource;
- never add the service account to a tenant-scoped default or custom group;
- never use the Codex service-account profile to enumerate or manage
  tenant-wide IAM.

Fail closed if a reviewed plan would parent the managed group outside the
selected project or target a permit at any other resource. A prior
tenant-parented Codex group is outside this workflow: do not discover, reuse,
modify, or delete it automatically. Report separate operator cleanup only when
the operator supplies that resource as current-task evidence.

The runtime selector chooses project-bound credentials; it is not a token
sandbox. This skill guarantees only that IAM it creates is project-parented and
project-targeted. It does not remove or prove the absence of direct permits or
memberships granted by another workflow. Treat such external grants as a
separate IAM audit rather than broadening this setup workflow.

## Run Confirmed Setup

After the user confirms service-account creation and the displayed convergence
envelope, run:

```bash
bash scripts/agent-nebius-auth-setup.sh ensure \
  --project-id <project_id> \
  --confirm <plan_digest>
```

Include the same optional tenant assertion, service-account name, and role that
the user reviewed. The live command rebuilds the plan before mutation and again
after acquiring its local locks. It fails without changing IAM, credentials, or
profiles if the current state-bound plan differs from the supplied digest. The
digest binds the resolved tenant/project/name, service-account name and
observed identity, deterministic group and observed identity, additive role,
canonical paths/profile, credential fingerprint, administrative profile,
endpoint, complete bounded action set, current mutations, and plan notes.
Partial progress produces a fresh digest. The skill revalidates it against the
recorded one-time bootstrap authorization and continues without another prompt
only while every target and authorized action still matches.

The script validates authoritative project metadata before creating a
directory, lock, credential, profile, service account, group, membership, or
permit. For an existing credential, it resolves the embedded service-account
ID and verifies that the account has the requested name and selected project as
its parent before changing permissions or profiles. Fail closed on missing,
ambiguous, or mismatched identity.

Read failures are not absence: failed or malformed service-account, group,
permit, membership, or local-profile lookups stop planning and reconciliation.
Membership reconciliation follows every returned page token. A create conflict
counts as convergence only after a successful read proves the desired resource
exists. Credential generation uses a private same-directory temporary file,
validates canonical identity and mode, flushes it, and atomically replaces the
destination. A failed new or matching credential gets at most one explicit
mode-`0600` backup-and-replacement phase within the confirmed envelope. On
`credential-replacement-required`, re-run a read-only replacement plan:

```bash
bash scripts/agent-nebius-auth-setup.sh replace-credential \
  --project-id <project_id> \
  --dry-run
```

Validate its displayed observed IDs and credential SHA-256 against the private
bootstrap record, mark the replacement attempt, and then run it with its fresh
digest. If that bounded replacement still cannot mint a token, stop and report
the blocker without asking for repeated confirmations, generating another key,
or revoking keys automatically. A failed or interrupted replacement ends that
bootstrap attempt and must not be retried under a newly computed digest.

Defaults:

- credential: `~/.nebius/codex-agent-authkey.<project_id>.json`;
- CLI profile: `codex-agent-<project_id>`;
- group: `codex-agent-<project-name-prefix>-<project-id-hash>`;
- role: `admin` on the selected project.

The project-ID hash prevents projects with the same normalized name from
sharing a group name. If the read-only plan finds the older slug-only group, it
reports that group as a separate operator cleanup follow-up; setup does not
delete it.

An active `codex-agent-*` profile is not a human/admin IAM session. Require a
valid non-agent Nebius profile, including an explicitly selected
`NEBIUS_PROFILE`, for project validation and IAM reconciliation.

`ensure` is convergent. It creates the project-scoped service account, group,
membership, additive permit, credential, or profile only when the reviewed
current state requires that mutation. A working matching profile is a no-op.
Credential identity has one accepted format: `subject-credentials.type` is
`JWT`, `alg` is `RS256`, and `iss` and `sub` are the same valid
`serviceaccount-*` ID.
Legacy aliases, duplicate JSON keys, ambiguous identity, and conflicting
issuer/subject values fail closed. Setup never deletes service accounts,
revokes keys, or removes broader permits automatically.

## Verify Runtime Authentication Read-Only

Runtime diagnosis must not depend on an administrative session. To validate
the matching canonical credential, profile token mint, and basic project
access without changing local or cloud state, run:

```bash
bash scripts/agent-nebius-auth-setup.sh verify \
  --project-id <project_id>
```

`verify` never activates or requires a human/admin profile and does not claim
that IAM resources match the desired setup or that no external workflow granted
broader access. It must not probe tenant-wide IAM with the agent profile. Exact
IAM reconciliation is a separate `ensure --dry-run` operation. If its required
non-agent profile cannot authenticate without a browser, report
`blocked-admin-auth`; do not attempt interactive login or misclassify the
working agent credential as unusable.

## Preauthorize Bounded Local Repair

Issue a lease only when the user explicitly requests unattended local repair,
after first-time setup or confirmed persistent repair is complete, the exact
credential and profile can mint a token, and basic project access succeeds:

```bash
bash scripts/agent-nebius-auth-setup.sh repair-lease \
  --project-id <project_id> \
  --ttl-seconds 43200 \
  --dry-run
```

After its one explicit confirmation, repeat with
`--confirm <plan_digest>`. The default lifetime is 12 hours and the maximum is
24 hours. The script creates a random,
mode-`0600` record below the owned mode-`0700`
`~/.nebius/codex-agent-repair-leases/` directory. It binds the tenant, project,
service-account identity, canonical credential and its SHA-256 fingerprint,
profile, endpoint, fixed actions, issuance, and expiry.

The record is workflow preauthorization under the trusted same-user Codex
model, not a cryptographic boundary against a process with the same filesystem
access. Keep its path in private task state and never commit it. Its integrity
digest detects drift; it does not make a same-user file unforgeable.

When diagnose finds only bound credential-mode or local-profile drift, run:

```bash
bash scripts/agent-nebius-auth-setup.sh repair-local \
  --lease-file <canonical_lease_path>
```

`repair-local` revalidates the lease and credential before and after acquiring
the project/profile locks. It may use fd-based no-follow permission correction
and rebuild the exact profile, then token-test and verify basic project access.
It never enters the human/admin, IAM, credential generation/rotation, identity,
or hook paths. Missing, changed, malformed, mismatched, non-owned, symlinked,
expired, future, overlong, or noncanonical state fails closed. A credential
found broader than `0600` is corrected, but possible prior disclosure still
requires a separately confirmed rotation decision.

## Runtime Hook Boundary

The setup skill and script do not inject runtime tokens and do not write a
global default-project selector. The installed `PreToolUse` hook owns runtime
injection and never invokes this skill, searches memory, or repairs auth.

Any Bash command that needs Nebius authentication opts in by starting with
exactly one selector, regardless of whether the executable is Bash, Python,
Nebius CLI, `nebius-cxcli`, Terraform, or another API/SDK client:

```bash
CODEX_NEBIUS_PROJECT_ID=<project-id> <command>
```

The hook strips that leading assignment, validates it, and injects only
renewable context: `NEBIUS_PROFILE`, `NEBIUS_PROJECT_ID`,
`NEBIUS_AUTH_CREDENTIALS_FILE`, and `CODEX_NEBIUS_TOKEN_HELPER`. It clears
`TOKEN` and `NEBIUS_IAM_TOKEN`; it does not mint a universal bearer token. This
same path applies to every selected executable without a command allowlist.
The hook fails closed for missing or malformed selectors on recognized Nebius
operations, conflicting managed auth variables or profiles, environment
clearing/splitting, disclosure-prone commands, and credentials that are not an
owned regular non-symlink file at mode `0600` with the canonical matching
identity. Legacy default-selector files and inherited auth variables are
inert.
On denial, use `$agent-nebius-auth-diagnose`, then retry with the exact prefix.

The hook is a policy guard, not a shell sandbox. Prefer direct commands and
narrowly scoped API scripts. A command must not clear its environment after
selection or dump inherited auth context.

Token-renewal ownership is explicit:

- normal Nebius CLI commands use `NEBIUS_PROFILE`;
- supported SDKs use the injected credential file or profile and own their
  refresh cycle;
- a raw-token child runs through the generic helper, which mints immediately
  before execution and injects `NEBIUS_IAM_TOKEN` only into that child:

  ```bash
  python3 "$CODEX_NEBIUS_TOKEN_HELPER" exec-token -- <command> [args...]
  ```

- a shell-controlled idempotent raw operation may map only a real HTTP 401 or
  gRPC `UNAUTHENTICATED` response to exit `77`, then use one bounded refresh and
  retry:

  ```bash
  python3 "$CODEX_NEBIUS_TOKEN_HELPER" retry-idempotent -- <adapter> [args...]
  ```

- an already-running process cannot receive a replaced environment. Long-lived
  code must use provider-native renewable credentials or invoke the helper for
  each bounded request/retry; otherwise restart it.

The helper executes argv directly, never places a token in argv, never parses
stderr, and retries only status `77`. It clears ambient bearer variables before
minting and never writes the token to a file or parent shell. Do not use retry
mode for non-idempotent operations. Transport tracing such as verbose/trace
`curl` or verbose `grpcurl` is denied for selected commands because it can
disclose authorization headers. Manual
`get-access-token >/dev/null` remains verification only, not a refresh workflow.

Hook installation is outside the service-account bootstrap envelope. Run it
only when the user explicitly requests hook installation or refresh; do not
propose another confirmation during normal bootstrap. From the skills repo
root:

```bash
./install-skills.sh \
  --install-hooks agent-nebius-auth-setup/assets/hooks \
  --register-hooks
```

The root installer exclusively owns hook payload sync and `hooks.json`
registration. Do not edit `$CODEX_HOME/config.toml` or invoke the installer from
the setup script.

## Verification

Use the matching leading selector for normal Nebius commands. The only direct
token check allowed by the hook is the exact matching agent profile with stdout
discarded:

```bash
CODEX_NEBIUS_PROJECT_ID=<project-id> \
  nebius iam get-access-token \
  --no-browser \
  --profile codex-agent-<project-id> >/dev/null
```

Never print or persist tokens in output, task state, docs, logs, or responses.

When changing this skill, verify current Codex hook behavior and current Nebius
CLI help, then run:

```bash
python3 -B scripts/test_pre_tool_use_nebius_auth.py
python3 -B scripts/test_nebius_auth_runtime.py
python3 -B scripts/test_agent_nebius_auth_setup.py
python3 -B scripts/test_install_hook_registration.py
python3 -B scripts/test_agent_nebius_auth_contract.py
```

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.
