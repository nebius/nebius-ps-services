# Protected merge setup

Every ordinary PR and configured Dependabot update, including Docker, follows
local review-pr, safe repair, fresh review and CI, then merge-pr. The trusted
GitHub Actions broker uses GITHUB_TOKEN for protected approval and merge.
Dependabot waits until a local operation is started. Agents run locally;
Agentic SDLC retains its separate authorization and workflow.

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
7. Keep `.github/merge-policy.json` aligned with all six CI/path mappings, the
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
validation or unresolved human objections. Standalone review-pr never merges;
review-and-merge authorizes the complete sequence without routine reconfirmation.

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

## Completion and recovery

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
survive unrelated matrix failures. Scheduled reconciliation runs every 15
minutes, subject to GitHub scheduling delays and the 30-day receipt window.
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
