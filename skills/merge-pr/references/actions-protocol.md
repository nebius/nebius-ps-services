# Protected Actions merge protocol

Ordinary create-pr, review-pr and publication skills use this GitHub.com
protocol. Agentic SDLC is excluded. All agent review and repairs run locally;
Actions runs deterministic helpers with its built-in `GITHUB_TOKEN`. No custom
App, PAT, agent runtime or credential copied from the laptop is required.
GitHub Enterprise needs an explicitly reviewed deployment adaptation because
the broker pins github.com and numeric bot identities.

## Local review evidence

After reviewing the complete current base-to-head diff, fixing safe findings,
validating and re-reviewing, write a private JSON file with observed evidence:

```json
{
  "schema": "skills-review/v1",
  "repository": "owner/repository",
  "pr": 123,
  "head": "<full-reviewed-head-sha>",
  "base": "<live-default-branch>",
  "base_sha": "<full-reviewed-base-sha>",
  "verdict": "passed",
  "unresolved_findings": 0,
  "validation": ["Relevant command and its observed passing result"]
}
```

Do not record a passing verdict with unsafe findings, unresolved human
objections/threads, failed checks or incomplete review. Keep secrets, private
data and raw logs out. This is an authenticated operator attestation, not
independent proof of model execution. Post a COMMENT review using the existing
operator's CLI identity; this invoked workflow authorizes that publication:

```text
python3 <merge-pr>/scripts/merge_gate.py attest --repo owner/repository \
  --pr 123 --evidence <private-json-file>
```

The broker independently validates the review ID, COMMENTED state, numeric
operator allowlist, commit ID, repository/PR/head/base, verdict and validation.
Only configured operators can request any new merge, including Dependabot updates. Actions cannot approve
its own PR, so Actions-authored PRs require maintainer handling.

## Dispatch and merge authority

Resolve the live default and dispatch on that ref after applicable CI passes:

```text
gh workflow run skills-merge-pr.yml --repo owner/repository --ref <default> \
  -f pr=123 -f head=<reviewed-head> -f review_id=<review-id> -f method=squash
```

Bind the broker run by event, inputs, PR/head and time; never assume the latest
run is yours. `MERGE_OPERATOR_IDS` is the sole merge-specific repository
variable, a JSON list of trusted numeric operator IDs. Every new merge requires
explicit workflow_dispatch and a positive actual review ID. Missing, zero,
malformed, forged or stale review evidence fails closed. PR/CI events and
schedules produce no merge candidates; they continue updating Required CI.

The trusted default workflow separates permissions by job: CI status writes,
read-only admission, then Contents/PR write for approval and merge. Before any
approval it uploads an immutable exact-head/base `merge-intent/v1` artifact.
The effect job requires that receipt, independently repeats CI/review gates,
reuses an existing exact-head Actions approval, then rechecks before requesting
`PUT /pulls/{number}/merge-async` with `sha`, `merge_action: direct_merge`,
`bypass_rules: false`, and the chosen method.

The built-in token cannot enqueue a merge queue: stop before approval when a
queue rule applies. Workflow-file merges can also be rejected by GitHub token
permissions; reconcile the PR and report maintainer action. Never substitute
an App/PAT, direct CLI merge, `--auto`, `--admin` or default-branch push.
The API has no base-SHA compare-and-swap. Current base inclusion, repeated
checks and GitHub strict required CI protect against a moving base.

`Required CI` is a CI-only status, available to human and fork PRs independently
of merge dispatch or local review evidence. It checks the explicit
workflow/path inventory and other checks/statuses. Broker admission remains a
separate gate. All write-enabled workflows share `github-actions[bot]`; this
design does not provide custom-App isolation. Keep each job least-privileged.

## Result CI, Pages and recovery

An accepted request is pending. Confirm actual merge and the authoritative
result SHA through identity-bound GraphQL `mergeCommit.oid`, then remote
default-branch containment. REST `2026-03-10` omits the old result field;
missing GraphQL identity or result evidence blocks completion without effects.
Token-generated pushes do
not trigger ordinary CI or legacy Pages builds. A trusted completion workflow,
triggered by workflow completion and a schedule, resumes from the uploaded
operator-dispatched positive-review intent even if the broker failed after merge.

The dispatcher uploads its attempted-work journal before explicitly dispatching
applicable CI. Each dispatch binds repository, PR, result SHA, original intent,
workflow and stable correlation. Each CI job first checks out `github.sha` with
credentials persistence disabled. Before setup, cache restoration or project
execution, the trusted `ci-checkout` helper independently validates all five
inputs, the broker receipt, merged PR identity and default-branch containment.
It fetches only the authoritative merge result using process-local authentication,
then verifies HEAD. These jobs need Contents, Pull requests and Actions read
permissions. A job output or raw dispatch input is never checkout authority;
read-only tokens alone do not prevent poisoning the default-branch cache.
CI asserts checkout identity and records successful required
push-equivalent jobs in a result artifact. The workflow revision may be a newer
default commit; its run `head_sha` alone does not identify the tested revision.
Only the trusted latest run/attempt with matching artifact can pass. A previous
uncertain dispatch without a discoverable run stops; it is never blindly retried.

When configured, a separate Pages-write job requests a legacy default/root build
after exact-result CI passes. It journals the request first and accepts a built
commit containing the result. Failed or uncertain requests stop for inspection.
Completion receipts are scoped per PR and successful verification job, so one
failed matrix sibling does not invalidate another's completion. Recovery scans
the retained 30-day receipt window; expired evidence requires maintainer
reconciliation. Disabling merge effects does not abandon completed merges.

Use [completion verification](completion-verification.md) for local read-only
observation and bounded waits. Never recreate a merge, dispatch or publication
because observation timed out. Release/tag publication remains owned by the
local publication skill; this broker does not create release tags.

## Installation and bootstrap

In this source repository use the [deployment guide](../../../.github/merge-automation.md).
Elsewhere, github-workflows owns its protected-merge templates, helper copies,
explicit CI inventory and CI input/evidence integration. Standalone installs
should read that skill's template README because source-relative links may not
exist. Missing setup is a blocker.

A normal maintainer-reviewed bootstrap must place trusted workflows on default
before dispatch. Keep required review, stale-approval dismissal, strict CI
and conversation resolution. A push restriction that cannot admit the built-in
identity requires an explicitly authorized protection redesign; never remove
it opportunistically. Additional human/CODEOWNER/environment requirements remain
blockers. Do not delete old credentials as part of migration.

## Reviewed dependency updates

Dependabot uses the same complete local review and safe repair path as ordinary
PRs, including Docker updates. Review breaking changes and dependency/security
information, route Python, Actions and Docker to the matching specialists and
run meaningful validation. Safe source repairs are allowed through create-pr;
every changed head or base invalidates the prior review. Unsafe findings,
missing validation and human objections stop merging. No metadata producer,
bot identity, label or green CI can replace the COMMENT review. Schedules,
groups, labels, limits, target branch and configured ecosystems stay unchanged.
Dependabot waits for a local operation; there is no scheduled local agent.

Before retiring earlier admission, reconcile outstanding effects and prove old
broker runs quiescent. Completion and CI checkout accept only trusted operator
dispatch with a positive review ID. Preserve recovery for authorized completed
merges when stopping new broker runs; never undo merges/releases automatically.

Official contracts: [built-in token](https://docs.github.com/en/actions/concepts/security/github_token),
[async merge](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request-asynchronously),
[workflow dispatch](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event),
[Pages build](https://docs.github.com/en/rest/pages/pages#request-a-github-pages-build),
[Dependabot Actions](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-on-actions).
