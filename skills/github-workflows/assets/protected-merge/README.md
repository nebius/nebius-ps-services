# Protected merge templates

Install the workflow templates under `.github/workflows` and the policy as
`.github/merge-policy.json`. Copy both `merge-pr/scripts/merge_gate.py` and
`merge_completion.py` from the same skill installation into `.github/scripts`.
Keep this pair aligned with the templates. No agent runtime runs on Actions.

Use `ci.yml.template` as the complete input/checkout/evidence example. Replace
`__CI_COMMAND__` with the repository's actual push-equivalent checks. For each
existing CI workflow, preserve ordinary PR/push/manual behavior, add the five
atomic post-merge inputs and trusted identity job. Every execution job first
checks out `github.sha` without credential persistence, then runs `ci-checkout`
from that trusted revision before setup, cache restoration or project commands.
Give these jobs Contents, Pull requests and Actions read permissions. The helper
revalidates the receipt and authoritative merged result immediately before
checkout, with process-local fetch authentication. Never pass a dispatch input
or job output directly to checkout. Assert HEAD in every required job and emit
the result artifact only after all
push-equivalent jobs succeed. Manual-only deployment/integration tasks must not
run on post-merge dispatch. Include the result SHA in concurrency groups; do not
cancel result verification when a newer main or manual run starts.

Customize both workflow-run trigger lists and the complete CI/path policy
inventory together. Retain at least one unfiltered PR CI workflow. The native
`Required CI` aggregate runs independently of merge dispatch and review
attestations. Expected workflow evidence, checks/statuses and fork association
must all be readable. Merge queues are unsupported by this built-in-token path.

Read merge-pr's `references/actions-protocol.md` for the authority and evidence
contract. Set the sole merge-specific variable `MERGE_OPERATOR_IDS` to a JSON
list of verified operator numeric IDs. Every new merge, including Dependabot
and Docker updates, requires explicit operator dispatch and a positive exact
head/base COMMENT review. Automatic PR/CI events and schedules only report CI;
completion recovery accepts previously authorized positive-review intents.
Keep the default token read-only and
allow Actions PR approvals. Only effect jobs request write permissions. There
are no App credentials or PAT fallbacks, and all write-enabled workflows share
the Actions identity. Changes to branch protection require explicit authority;
keep native required reviews, strict CI and conversation resolution.

Pages is disabled in this generic policy. Enable it only after confirming the
repository uses legacy default-branch/root publication and adapting that
contract when needed; remove the unused Pages-write permission if disabled.
Do not copy this publisher into an Actions-deployment Pages site unchanged.

Reconcile old broker effects and prove writers quiescent before retiring an
earlier admission path. Use a protected bootstrap, then a small ordinary PR and
a real locally reviewed Dependabot acceptance PR, including Docker when eligible. Workflow-file token denial stops for a maintainer.
The pre-effect journals and completion receipts are retained for 30 days;
unknown effects and expired evidence require reconciliation, not blind retries.
Stopping new broker runs must leave recovery for completed merges operational.
Local release skills retain tag ownership. Agentic SDLC is excluded.
