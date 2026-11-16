---
name: test-live-on-nebius
description: Use only when the user explicitly asks for a disposable Nebius live-test campaign. Prefer cxcli components and reviewed Python SDK adapters for gaps, verify workloads and clean owned resources. Not for testing an existing environment, read-only quota comparisons or production changes.
disable-model-invocation: true
---

# Test Live on Nebius

## Help

For `$test-live-on-nebius --help` or `$test-live-on-nebius -h` (including native Claude forms), return concise help and stop before
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

## Usage

```text
$test-live-on-nebius [run|plan] --tenant ID --project ID
  [--region REGION] [--source PATH] [--scenario FILE]
  [--engine auto|cxcli|sdk] [--cxcli-source PATH]
  [--timeout DURATION] [--quota-mode request|fail]
$test-live-on-nebius resume RUN_ID
$test-live-on-nebius cleanup RUN_ID
$test-live-on-nebius --help
```

- `run`: infer, provision, test and clean; the default action.
- `plan`: inspect source and available authenticated inventory without mutation.
- `resume RUN_ID`: reconcile a protected existing run; never duplicate uncertain creates.
- `cleanup RUN_ID`: reconcile and remove only that run's owned resources.
- Recovery actions accept only `RUN_ID`; execution options cannot override a frozen run.
- `--tenant`, `--project`: required exact target; verify ancestry before writes.
- `--region`: optional assertion against the authoritative project region.
- `--source`: current project directory by default; freeze relevant source/build hashes.
- `--scenario`: optional existing scenario; otherwise derive one from the session.
- `--engine`: `auto` prefers supported cxcli; explicit values select a qualification path.
- `--cxcli-source`: optional checkout; otherwise locate `services/nebius-cxcli`.
- `--timeout`: active execution bound, default `60m`; accepts positive `s`, `m`, `h` up to `12h`.
- `--quota-mode`: default `fail`; use `request` when the task authorizes necessary increases.
- `-h`, `--help`: this help only. No additional public flags.

## Agent Compatibility

Use `$test-live-on-nebius` in Codex, `/test-live-on-nebius` in Claude Code,
or `/skills:test-live-on-nebius` through the Claude plugin. Explicit invocation
is required (`allow_implicit_invocation: false`); it does not authorize unrelated
cost, IAM, exposure or deletion.
Read `../global-context-management/references/agent-hosts.md` for host paths.
Required sibling owners are `nebius`, `agent-nebius-auth-setup` and
`global-context-management` (native-home resolution and host supervision). Missing
owners or tools are explicit blockers, not authority to recreate their workflows.

## Purpose

An existing test environment can be used directly with its project harness; this
provisioning skill is unnecessary unless explicitly requested. When selected for
UAT, this skill owns authorized disposable provisioning, testing and cleanup;
an already-created environment is not required. Follow the readiness and pending
handoff policy in `../worktree/references/optional-uat.md`.

Create a minimal live environment, verify the current project's declared behavior
and independently prove cleanup, with durable recovery for interrupted runs.

## When To Use

Use for authorized VM, MK8s or other Nebius live integration tests in an exact
tenant/project. Infer the environment from the current task.

## When Not To Use

Read-only quota comparisons, local container tests and production changes belong
to their own workflows. This skill does not grant new cloud authority.

## Required Reads

Read the matching cxcli or SDK reference before provisioning, the lifecycle
contract before helper execution, and the quota reference only when needed.
Read auth setup before delegating its exact-target ensure.

## Writes

Write only the protected run directory and authorized target resources. Update
reusable sources only when requested or an in-scope learning is authorized.
Do not store operational evidence in the repository.

## Inputs

Use the current session's objective, project code/docs and tests to identify
what must actually run. Ask only for unresolved acceptance criteria or a material
scope choice. Infer the smallest adequate environment, including dependencies,
peak/surge quota, network access, deadlines and deletion order. A VM example is
not a universal size cap. Never infer tenant/project from unrelated memory.

Keep one frozen scenario in protected runtime state outside Git. Record the
objective, exact target/region, source/build identity, resource plan with owner
and borrowed/created classifications, adapter hash, acceptance checks, budget
and cleanup plan. No tenant/project IDs or runtime evidence belong in this skill.

## Hosted autonomous callers

An enrolled controller may generate a small PR-specific scenario through this
skill. Return typed fixture requirements, product workload and independent
assertions; do not generate or execute privileged adapter code with cloud keys.
Only a reviewed, pinned capability from trusted default-branch code may provision,
observe and clean resources. A laptop-qualified adapter and its local flock journal
do not establish distributed runner qualification. The hosted owner must retain
CAS state, operation receipts, source/scenario hashes and cleanup recovery. Missing
capability, credentials or evidence means unavailable, never passed. A lifetime PR
waiver cannot waive cleanup or erase failed/intervened evidence.

## Process

1. **Discover and declare.** Inspect source and cxcli's actual catalog; don't
   infer capability from a name. Freeze product-owned steps separately from SDK
   fixtures and independent verification. Show the bounded plan; reuse existing
   task authority without another approval. `plan` never runs cxcli commands
   that can bootstrap IAM/backend state or creates a run journal.
2. **Authenticate.** For an authorized run, delegate one exact-target `ensure`
   to `agent-nebius-auth-setup`, then verify runtime identity and ancestry.
   Preserve its project Admin plus tenant Viewer shape. The exception does not
   authorize leases, hooks, unrelated credential replacement or tenant Admin.
   cxcli owns its separate runtime service account. Bind quota reads to the
   selected agent profile; do not fall back to human credentials.
3. **Admit resources.** Read effective tenant/project quotas, exact platform
   presets, image and network eligibility. Unknown quota is not available quota.
   Use [quota workflow](references/quota.md) only for authorized requests.
4. **Select the owner.** Read [cxcli workflow](references/cxcli.md) for supported
   components. For catalog gaps or a forced SDK trial, use
   [SDK adapters](references/sdk-adapters.md). A deployment failure does not
   make a component unsupported or transfer its resources to another owner.
5. **Execute.** Generate and review the normalized scenario and adapter, freeze
   their hashes, then use `scripts/live_run.py` with that scenario. This helper
   consumes the agent's resolved plan; it does not infer project intent itself.
   Its protected journal is the run authority. Use
   [lifecycle contract](references/lifecycle.md) for callbacks and recovery.
6. **Verify.** Check exact control-plane configuration, service readiness and
   real project-specific behavior. A RUNNING VM or healthy cluster alone is not
   a workload test. Keep raw output private and retain only sanitized assertions.
   An intervention that performs a product-owned step invalidates that trial;
   preserve it and start a new declared trial after cleanup.
7. **Clean and report.** Always attempt owner-native cleanup, even after failure,
   cancellation or timeout. Stop owned writers, reconcile accepted operations,
   delete in dependency order and independently read back absence. Preserve
   borrowed resources, reusable auth and approved quotas. Never report success
   with `cleanup_pending`; report exact residual ownership and a resume command.

## Security and resource safety

Use `global-context-management` host-memory supervision before and during work.
Use private networking first. If authorized and necessary, use a temporary
public IP and TCP/22 restricted to the verified runner address. Pin the guest
public host key from authenticated serial logs; never disable host checking or
put private host keys/cloud credentials in cloud-init. Remove temporary access.

Use a private run directory (0700), credential/evidence files (0600), bounded
reads and sanitized errors. Do not log tokens, provider stderr, Terraform state,
raw user-data or browser cookies. Generated adapters execute as trusted reviewed
code; hashes and journal checks do not provide an adversarial sandbox.

## Idempotency

An unchanged completed run is a no-op. Single-writer locks, frozen hashes and
persisted intents prevent silent duplicate effects. Reconcile before retrying.

## Failure Handling

Attempt cleanup on every interrupted or failed path. Unknown operation outcomes
stop forward progress and leave cleanup_pending until reconciliation succeeds.
Missing browser access blocks quota submission; it does not change IAM authority.

## Must Not

Never adopt by name, delete borrowed resources, bypass the native resource owner,
embed credentials in user-data, or claim cleanup from a deletion request alone.

## Completion Criteria

All declared assertions pass and owned resources are independently absent. Retain
explicit incomplete outcomes when either condition is unproven.

## Output Contract

Report run ID, chosen owner and scope, source/adapter identity, resources created,
quota requested/pending/effective, control-plane and workload assertions, cleanup
proof and retained reusable identities. Distinguish PASS, FAIL, awaiting quota
and cleanup pending. An adapter or service is live-qualified only by its own
observed trial. Host loss cannot guarantee automatic cleanup; the journal enables
explicit recovery without a persistent cleanup daemon.

## PR lifecycle handoff

For a PR-owned trial, read `review-pr/references/pr-lifecycle.md` from the installed sibling skill.
Return the exact candidate/scenario identity, outcome, independent assertions,
intervention classification and cleanup evidence to the parent. Preserve failed
trials. A failure invokes troubleshoot at its causal owner and design for a proven
contract change, then a new reviewed candidate and fresh trial. This skill never
labels, closes or merges PRs/issues. An inapplicable or unavailable trial is not a
pass; the parent chooses manual-merge only without known correctness blockers.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.
