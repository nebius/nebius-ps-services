# Test Live on Nebius

[The shared PR lifecycle](../review-pr/references/pr-lifecycle.md) aligns contribution assessment,
status labels, failed-test repair, one replacement PR and independent issue
completion. Plain review and report-only limits remain binding; issue closure
requires its own verified resolution and authority.

This skill turns an authorized project test into a disposable Nebius environment.
The agent derives the scenario; deterministic helpers preserve immutable scope,
operation receipts and cleanup ownership. Read [SKILL.md](SKILL.md) for all flags.
Run admission binds helper hashes and interpreter/package identity, retaining
private recovery copies. Resume detects changed code before further effects.

The default engine prefers cxcli's supported catalog. SDK adapters cover verified
gaps and explicit SDK trials. Neither path turns general readiness into workload
proof. Browser quota requests use a verified human administrator; runtime uses
the canonical agent account without tenant Admin.

Install the new skill and its `nebius`, `agent-nebius-auth-setup` and
`global-context-management` dependencies using
the repository's reviewed installer or native plugin. Skill-only copying does not
register hooks; this skill adds no hook or daemon.

```bash
python3 -B scripts/live_run.py plan --tenant TENANT_ID --project PROJECT_ID \
  --scenario /protected/path/scenario.json
python3 -B scripts/live_run.py run --tenant TENANT_ID --project PROJECT_ID \
  --scenario /protected/path/scenario.json
python3 -B scripts/live_run.py cleanup RUN_ID
python3 -B -m unittest discover -s tests -p 'test_*.py'
```

The helper requires a normalized scenario, unlike the skill's conversational
interface, which infers it. Plans do not import adapters or access the cloud.
Scenarios require a nonempty `source_identity`; the host and service adapter
verify that identity against the tested source or artifact. Recovery actions
accept only the run ID and reject execution overrides, including default values.
See [lifecycle](references/lifecycle.md), [cxcli](references/cxcli.md),
[SDK](references/sdk-adapters.md) and [quotas](references/quota.md).

Exit codes: 0 verified test plus cleanup; 1 failed/unexecuted test with complete
cleanup; 2 incomplete cleanup or invalid input; 3 awaiting quota. No cloud claims
follow from static tests, installation checks or adapter code review alone.

## Enrolled repository automation

Shared Actions authority: enrolled repositories route laptop and hosted requests through one merge/release controller; PR lifetime waivers are durable policy, not labels. See the skill contract and sibling merge-pr automation-authority reference.

UAT follows the [shared optional policy](../worktree/references/optional-uat.md).
Requested campaigns wait for a meaningful ready scope. An existing test environment
uses its project harness directly; container/Nebius campaigns require explicit
skill selection. Missing optional prerequisites leave a handoff without blocking
implementation or eligible publication. Actual failures and cleanup remain owned.
