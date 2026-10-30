# Protected merge setup

Review/intent v2 binds numeric base/head repository identities, head ref,
contribution/compatibility evidence and per-project spec outcomes. It admits
eligible forks while retaining trusted default-branch execution and protected
merge. Before deploying this revision, drain old grants/pushes/intents using
their old trusted owners, prove old runs quiescent, and coordinate helper,
workflow and operator installation updates through protected maintainer review
with independently verified CI. No old-schema compatibility or authority
translation is provided. See the [cutover contract](../skills/merge-pr/references/actions-protocol.md).

Every ordinary PR and configured Dependabot update, including majors and Docker,
uses the same operator-initiated review path. Plain create-pr performs complete
code-review before publication and stops at the reviewed PR. Plain review-pr
reviews/repairs without merging. Explicit combined merge intent or complete
publication delegates merge-pr, which always refreshes readiness through a
parent-owned review-pr call before protected merge. The trusted
GitHub Actions broker uses GITHUB_TOKEN for protected approval and merge.
Dependabot waits until a local operation is started. Agents run locally;
Agentic SDLC retains its separate authorization and workflow.

## Retiring the github-report workflow

`github-report` and its release workflow are retired. The existing
`github-report-ci.yml` name temporarily runs a read-only retirement check so
the policy on `main` can validate this transition. Once the policy change is
merged, delete that workflow and `test_github_report_retirement_transition`
in a follow-up PR. This transition does not change branch protection.

## Actions event policies

Repository administrators own three active Actions event policies in
`nebius/nebius-ps-services`. They preserve the two reviewed uses of
`pull_request_target` and explicitly prohibit that event for every other
workflow path. GitHub's default restriction is scheduled for enforcement on
December 2, 2026; these explicit policies retain the selected behavior beyond
that change. See [GitHub's event policy guidance](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target#default-policy-for-pull_request_target).

Each policy has `enforcement: active`, one `restrict_action_events` rule and
no actor rule. Configure the rule's `parameters.allowed_events` as follows:

| Policy | ID | Allowed events |
| --- | --- | --- |
| Other workflow events | `10050` | The frozen inventory below |
| Reviewer routing events | `10051` | `pull_request_target` |
| Protected merge events | `10052` | `pull_request_target`, `workflow_run`, `workflow_dispatch` |

The two exceptions use exact `conditions.workflow_path.include` entries:

- Reviewer routing: `.github/workflows/pr-reviewer-trimmer.yml`.
- Protected merge: `.github/workflows/skills-merge-pr.yml`.

Both exceptions have an empty `exclude` array. Other workflow events uses
`include: ["~ALL"]` and excludes exactly those two paths. These scopes are
disjoint and exhaustive, including workflows added later. An exception grants
permission to start on the listed event; it does not grant token permissions,
approve a PR, or establish that future code at the same path is safe.
Inherited organization or enterprise restrictions still apply.

The remaining-workflows policy allows these 35 event types, frozen from the
official API's 36 supported types on 2026-10-30. Newly introduced event types
need an intentional policy update; `pull_request_target` remains excluded.

```json
[
  "branch_protection_rule", "check_run", "check_suite", "create", "delete",
  "deployment", "deployment_status", "discussion", "discussion_comment",
  "fork", "gollum", "image_version", "issue_comment", "issues", "label",
  "merge_group", "milestone", "page_build", "project", "project_card",
  "project_column", "public", "pull_request", "pull_request_review",
  "pull_request_review_comment", "push", "registry_package", "release",
  "repository_dispatch", "schedule", "status", "watch", "workflow_call",
  "workflow_dispatch", "workflow_run"
]
```

Manage these settings through **Settings → Actions → Policies** or the
[repository Actions policy API](https://docs.github.com/en/rest/actions/policies#create-a-repository-actions-policy).
Use API version `2026-03-10` and the recorded IDs for subsequent updates.
Enumerate effective policies with `has_parents=true` before changing one.
Capture its complete current body, compare it immediately before the update,
and stop on concurrent drift. Independently GET the result after every write.
If a create request has an uncertain outcome, reconcile the returned ID or
exact intended repository/name/body before another POST; do not create a
duplicate or delete a policy merely because its name matches.

### Ownership and maintenance

The repository administrators own configuration changes and their readback;
workflow maintainers review the privileged code and its helpers. Review this
configuration before the December 2 enforcement date and whenever a workflow
path, trigger, helper, permission, or applicable organization policy changes.
Before a rename, review the new exact path and its trusted code. Remove the
old path from the baseline exclusions and verify that both paths are covered
by the baseline. Move the exception to the new path and read it back, then
exclude only the new path from the baseline. Verify all three policies; the
old path must remain restricted throughout and after the rename.

Keep contributor tests under `pull_request`. Privileged PR metadata automation
must execute only trusted code, retain minimal job permissions, and never
execute contributor code. Preserve existing branch protection, token defaults,
fork approval requirements and the explicit operator-dispatch merge gate.
Do not replace this trigger with `pull_request` without redesigning and
validating the broker's checkout and default-ref trust boundary.

### Activation and verification evidence

All three policies were created on 2026-10-30, with activation complete at
17:03:36 UTC. Independent GET requests matched the intended names, IDs,
enforcement, path conditions and event lists. Effective-policy enumeration
returned exactly these three repository policies and no inherited policies.
Normalized before/after readbacks of branch protection, rulesets, Actions
permissions, default workflow token/PR permissions and fork-contributor
approval settings were identical.

Validation used trusted default commit
`644b5a17515d30786a226a069c68d2eba9e0ee40`. Five focused merge-admission tests
and two workflow-contract tests passed. Static inspection covered every
trusted checkout, job permissions, and both reviewer same-repository/draft
checks. All 16 deployed workflows matched exactly one policy and retained
every configured trigger. The 684 policy partition cases covered those
paths plus three hypothetical new paths across all 36 documented events;
unknown event types were also rejected by the configured allowlists.

Live-event acceptance remains pending: no new workflow run was observed after
activation during this change. Observe the next ordinary PR and fork PR/CI
events and record their run IDs, event, source revision, outcome and policy
insights when available. Reviewer routing should run only for eligible
same-repository nondraft PRs; CI-status updates must continue for ordinary
and fork PRs without initiating approval or merge. Historical runs and static
matching do not prove GitHub's live enforcement. Do not dispatch the merge
broker merely to test admission or approve a fork run as part of this check.

### Fail-closed rollback

1. Reread the effective policy list and the affected policies by their recorded
   IDs. Stop if ownership or bodies differ from the expected configuration.
2. Remove the affected workflow path from policy `10050`'s exclusions, then
   independently verify that it is now covered by the baseline restriction.
3. Disable the corresponding exception (`10051` or `10052`) and read it back.
   Keep the other exception and all unrelated settings unchanged.

The affected workflow's `pull_request_target` runs are intentionally blocked
after this rollback; other declared triggers remain permitted. Rollback does
not claim restored PR-event operation. Disabling or deleting an exception
without first extending baseline coverage is not the rollback procedure.

## Bootstrap and protection

1. Capture the repository settings and inspect old broker runs and retained
   intent/effect receipts. Before retiring an earlier admission path, prove its
   writers are quiescent and reconcile any outstanding effects. Do not replay
   obsolete receipts through the new admission contract.
2. Deploy through a feature PR under existing protection. The trusted broker
   must exist on default; a workflow-changing bootstrap may need a maintainer.
   Report the exact approval/merge action if token permissions reject it.
3. Keep the default workflow token read-only and Actions PR approvals enabled.
   Individual jobs request only needed permissions. Create no Environment or
   new secret and leave existing credentials untouched.
4. Set the sole merge-specific repository variable, MERGE_OPERATOR_IDS, to a
   JSON list of verified trusted operator numeric IDs, such as `[12345]`.
   Remove retired rollout variables; there is no replacement activation toggle.
5. Capture and review the protection payload before applying it, then read it
   back. For this accepted deployment remove the empty push-actor restriction
   and require strict `Required CI`, bound to the verified GitHub Actions
   publisher App ID `15368`. Retain one approval, stale dismissal, conversation
   resolution, force-push/deletion prohibitions and every other existing rule.
   Preserve repository merge-method settings; squash remains the skill default.
6. Verify ordinary and fork `Required CI` without a local review attestation.
   PR events, CI starts/completions and schedules report CI but produce no merge
   candidates. Only trusted explicit dispatch can initiate a new merge.
7. Keep `.github/merge-policy.json` aligned with all configured CI/path mappings, the
   unfiltered CI workflow, broker/completion filenames and Pages configuration.

All write-enabled workflows share github-actions[bot]. Removing the empty push
restriction is an explicitly accepted policy change; least-privilege jobs and
trusted default code remain essential. Queues, additional human/CODEOWNER
requirements and unsupported workflow-file permissions stop for a maintainer.
Never substitute credentials or bypass protection.

GitHub clears `block_creations` when push restrictions are removed, including
when an update explicitly requests `block_creations: true` with
`restrictions: null`. That field extends push-actor restrictions to branch
creation; it is not an independent prohibition. Record this normalization in
the settings readback and compare every independent protection field with the
captured configuration.

The reviewed broker bootstrap landed in
[PR #216](https://github.com/nebius/nebius-ps-services/pull/216).
Strict `Required CI` is bound to App ID `15368`, the numeric operator allowlist
is configured, and the empty push restriction is removed. The default token
remains read-only with Actions approvals enabled. Legacy main/root Pages built
the bootstrap result. These deployment observations do not establish a live
broker merge or live Dependabot acceptance; record those separately below.

## Review and acceptance

Review the complete diff, breaking changes, dependency/security information and
meaningful validation. Route Python, GitHub Actions and Docker changes to their
matching specialists. Safe fixes may change source files; synchronize and
re-review each changed head or base. Stop for unsafe findings, missing meaningful
validation or unresolved human objections. Plain review-pr never merges; a root
review-and-merge request delegates once to merge-pr without routine reconfirmation.
Its review-pr child returns to the parent and never re-delegates.

For a small ordinary PR and a real eligible Dependabot PR, record:

- local review and passing validation/CI on the exact head and base;
- a trusted positive COMMENT review ID independently fetched by the broker;
- immutable intent before exact-head Actions approval and protected merge;
- authoritative result and remote default-branch containment;
- explicit CI checkout and verification of that exact result;
- successful configured Pages publication containing the result.

There is no permanent trial mode. Test Python, Actions and Docker admission,
safe source repairs, stale/malformed/zero/forged reviews, unauthorized dispatch,
objections, missing/failed CI and automatic-event non-initiation offline. Cover
Docker live when an eligible update exists and workflow-file success or the
expected maintainer handoff separately. A simulated bot fixture is not live
Dependabot evidence. No scheduled local agent is introduced.

`.github/dependabot.yml` preserves schedules, groups, labels, limits, target
branch and ecosystems. There is no separate dependency approval/merge producer.
Bot identity, labels, metadata and green CI never substitute for local review.

Required CI excludes orchestration checks by GitHub Actions app, repository,
check-suite/run identity, configured merge/completion workflow and trusted event
provenance. Display names (including matrix suffixes) never authorize an exclusion.
Unknown or ambiguous producers remain gating checks; required validation workflows
are independently mandatory. A pull_request_target run needs its default-base
association: GitHub may omit that association for forks, in which case a pending
or failed orchestration check remains a blocker. Current PR state alone cannot
prove the historical event base. Workflow tests accept full immutable setup-uv
SHA updates while preserving tool-version, cache, condition and ordering checks.

## Completion and recovery

Candidate discovery failures display the helper's sanitized diagnostic and
preserve its nonzero exit status. Empty failure output still reports the exit
status. Global discovery failure publishes no targets, so downstream CI dispatch and
Pages jobs cannot proceed. Inspect the diagnostic before considering recovery;
a discovery failure alone does not invalidate an already verified merge.

The shared helper reads the authoritative result from GraphQL `mergeCommit.oid`
and verifies the merged PR number, head and target identity. REST API
`2026-03-10` removed the old result field. Missing result evidence stops before
CI or Pages effects; recovery keeps the original immutable intent.

Token-generated pushes do not start ordinary CI or legacy Pages builds. The
completion workflow dispatches the applicable push-equivalent CI explicitly,
with a frozen result SHA, stable correlation and pre-effect dispatch journal.
Each CI job loads its validator from `github.sha`, then independently validates
the complete input identity, operator-dispatched positive-review broker receipt and merged result before selecting
that commit. Checkout happens before setup or cache restoration, with read-only
job permissions and process-local fetch credentials. Every required job asserts
HEAD; the run uploads exact-result evidence. Manual-only integration/deployment
jobs are excluded. A newer workflow revision is distinct from the tested SHA.
For example, recovery may run the current validator while testing an earlier
merged result. Verify the result artifact and checkout identity instead of
requiring the Actions run's `head_sha` to equal that result.

After CI passes, a separate Pages-write job preserves legacy main/root hosting
and explicitly requests a build. It verifies a built commit containing the
result. Pre-effect journals make unknown dispatch/Pages outcomes stop for
inspection rather than repeat effects. Successful per-PR completion receipts
survive unrelated matrix failures. Scheduled reconciliation runs hourly at minute 17 UTC, subject to GitHub scheduling delays and the 30-day receipt window.
A timed-out local observer can resume read-only verification; it must not infer
that another merge or publication is needed. Local release skills still own
exact-result tags and downstream release verification.

To roll back, stop new broker runs, reconcile in-flight effects and restore
captured settings when necessary. Keep completion recovery for already-merged
changes. Never undo merges/releases automatically or revoke existing credentials.
Source, installed, CI and live acceptance evidence must be reported separately.

## Portability

The canonical helpers are `skills/merge-pr/scripts/merge_gate.py` and
`merge_completion.py`. Other repositories install them together under
`.github/scripts` using the matching github-workflows templates. Adapt every CI
workflow with the input/checkout/evidence contract and explicit path policy.
Public examples use placeholders; repository-specific settings stay local.

Official contracts: [built-in token](https://docs.github.com/en/actions/concepts/security/github_token),
[branch protection](https://docs.github.com/en/rest/branches/branch-protection#update-branch-protection),
[async merge](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request-asynchronously).

## Targeted events and hourly recovery

Protected merge has no schedule. PR events and trusted PR CI `in_progress` and
`completed` events update only affected current heads. Starts must remain wired
so a same-SHA rerun cannot leave stale green Required CI. Push and result-CI
runs do not enter that aggregate path.

Merge completion has the sole hourly recovery schedule (`17 * * * *`) and manual
recovery. A separate default-branch-only status-write job repairs Required CI
for all open default-base PRs. Completion event discovery reads the producer
run's artifacts; ordinary CI and CI-only broker runs are filtered before runner
allocation. GitHub may still display skipped workflow-run rows.

Validated result inputs produce an immutable `merge-result-route/v1` receipt
before tests. It binds repository, workflow/run/attempt, broker/PR/result and
correlation; a failed CI run can route recovery but cannot prove successful CI.
Latest-attempt exact-result success evidence remains mandatory. Recovery retains
bounded journal scans for uncertain effects. Target-specific malformed receipts
produce blocked matrix entries and failing run status while valid independent
targets proceed. Global API, authentication and incomplete-listing errors stop
all candidate effects and preserve sanitized diagnostics.
