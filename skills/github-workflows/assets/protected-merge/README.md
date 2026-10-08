# Protected merge templates

Install the workflow templates under `.github/workflows` and the policy as
`.github/merge-policy.json`. Copy both `merge-pr/scripts/merge_gate.py` and
`merge_completion.py` from the same skill installation into `.github/scripts`.
Keep this pair aligned with the templates. No agent runtime runs on Actions.

Use `ci.yml.template` as the complete input/checkout/evidence example. Replace
`__CI_COMMAND__` with the repository's actual push-equivalent checks. For each
existing CI workflow, preserve ordinary PR/push/manual behavior, add the five
atomic post-merge inputs and trusted identity job, check out its validated SHA,
assert HEAD in every required job and emit the result artifact only after all
push-equivalent jobs succeed. Manual-only deployment/integration tasks must not
run on post-merge dispatch. Include the result SHA in concurrency groups; do not
cancel result verification when a newer main or manual run starts.

Customize both workflow-run trigger lists and the complete CI/path policy
inventory together. Retain at least one unfiltered PR CI workflow. The native
`Required CI` aggregate runs independently of merge activation and review
attestations. Expected workflow evidence, checks/statuses and fork association
must all be readable. Merge queues are unsupported by this built-in-token path.

Read merge-pr's `references/actions-protocol.md` for the authority and evidence
contract. Set repository numeric `MERGE_OPERATOR_IDS`, begin with
`MERGE_AUTOMATION_MODE=disabled`, then canary PR numbers in
`MERGE_CANARY_PR_NUMBERS` before enabling. Keep the default token read-only and
allow Actions PR approvals. Only effect jobs request write permissions. There
are no App credentials or PAT fallbacks, and all write-enabled workflows share
the Actions identity. Changes to branch protection require explicit authority;
keep native required reviews, strict CI and conversation resolution.

Pages is disabled in this generic policy. Enable it only after confirming the
repository uses legacy default-branch/root publication and adapting that
contract when needed; remove the unused Pages-write permission if disabled.
Do not copy this publisher into an Actions-deployment Pages site unchanged.

Use a normal maintainer-reviewed bootstrap, then a small ordinary PR canary and
an eligible Dependabot canary. Workflow-file token denial stops for a maintainer.
The pre-effect journals and completion receipts are retained for 30 days;
unknown effects and expired evidence require reconciliation, not blind retries.
Disabling new merges must leave recovery for completed merges operational.
Local release skills retain tag ownership. Agentic SDLC is excluded.
