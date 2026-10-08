# Protected merge setup

Ordinary PR and publication skills run local review-pr and safe repairs, then
merge-pr delegates to deterministic GitHub Actions using `GITHUB_TOKEN`.
Dependabot uses the same merge broker after policy admission. No agent runs on
Actions. Agentic SDLC retains its separate authorization and workflow.

## Bootstrap and protection

1. Merge the bootstrap PR through normal maintainer review before enabling
   automation. The trusted broker must exist on the default branch. The initial
   workflow-file PR cannot bootstrap its own approval or merge authority.
2. Keep Actions' default token read-only and allow Actions to approve PRs in the
   repository settings. Individual jobs request only their required permissions.
   No custom App, Environment secret or PAT is used. Leave old secrets untouched.
3. Set `MERGE_OPERATOR_IDS` to a JSON list of trusted local operator numeric IDs,
   such as `[12345]`. Set `MERGE_AUTOMATION_MODE=disabled` initially and
   `MERGE_CANARY_PR_NUMBERS=[]`. These are repository variables, not secrets.
4. Capture the existing main protection configuration for rollback. For this
   accepted deployment, replace its empty push restriction with required PR
   approval plus strict `Required CI`. Keep one approval, stale dismissal,
   conversation resolution and force-push/deletion restrictions. Preserve any
   additional requirements. Bind the required status to GitHub Actions when the
   repository API supports the observed publisher. Never add bypass actors.
5. Verify `Required CI` reports applicable native CI for ordinary human and fork
   PRs while merging is disabled. Local review attestations are broker admission,
   not prerequisites for a maintainer's native PR workflow. The aggregate listens
   for CI starts/completions and reconciles on a schedule; native status updates
   are asynchronous, while the broker always rechecks live runs before effects.
6. Review `.github/merge-policy.json` whenever CI changes. It contains all six
   workflow/path mappings, an unfiltered CI workflow, the merge/completion
   workflow filenames and legacy Pages publication setting.

All write-enabled workflows share `github-actions[bot]`. Removing the empty
push restriction therefore changes repository authority beyond one workflow;
job permissions and trusted default code provide the boundaries here. This was
an explicit deployment decision, not a general permission-repair fallback.
Required queues, extra human/CODEOWNER approvals and unsupported workflow-file
merges stop for a maintainer. Do not weaken protection to make a canary pass.

## Controlled activation

Use `MERGE_AUTOMATION_MODE=canary` with only the intended PR numbers in
`MERGE_CANARY_PR_NUMBERS`. First use a small feature-branch documentation PR and
record its allowed operator's exact-head/base COMMENT review. Verify:

- local review-pr and safe repairs finish before dispatch;
- CI-only `Required CI` and independent admission both pass;
- the pre-effect intent artifact exists before exact-head Actions approval;
- the protected async API reports actual merge with no bypass/default push;
- the authoritative result belongs to remote main history;
- explicit post-merge CI checks out and verifies that exact result;
- Pages has a successful build at the result or a descendant containing it.

Exercise a workflow-file PR separately. A token permission rejection is a
maintainer handoff for that PR, not permission to introduce another credential
or a reason to invalidate a successful supported canary. Test stale review,
unsafe findings, human objections, failed CI, recovery and duplicate suppression
with the offline suite before live activation. Observe an eligible Dependabot
PR through the same outcome without a laptop agent, then set mode to `enabled`
only after recording the canary evidence.

`.github/dependabot.yml` keeps schedules, groups, limits, branch and ecosystems.
Major/minor/patch github_actions and pip/uv updates keep their path eligibility;
Docker PRs remain ineligible for automatic merge. Existing Dependabot PRs need a
fresh metadata producer run after bootstrap. The broker never rebases branches:
a stale branch waits for Dependabot or an authorized maintainer update.

## Completion and recovery

Token-generated pushes do not start ordinary CI or legacy Pages builds. The
completion workflow dispatches the applicable push-equivalent CI explicitly,
with a frozen result SHA, stable correlation and pre-effect dispatch journal.
Each CI job loads its validator from `github.sha`, then independently validates
the complete input identity, broker receipt and merged result before selecting
that commit. Checkout happens before setup or cache restoration, with read-only
job permissions and process-local fetch credentials. Every required job asserts
HEAD; the run uploads exact-result evidence. Manual-only integration/deployment
jobs are excluded. A newer workflow revision is distinct from the tested SHA.

After CI passes, a separate Pages-write job preserves legacy main/root hosting
and explicitly requests a build. It verifies a built commit containing the
result. Pre-effect journals make unknown dispatch/Pages outcomes stop for
inspection rather than repeat effects. Successful per-PR completion receipts
survive unrelated matrix failures. Scheduled reconciliation runs every 15
minutes, subject to GitHub scheduling delays and the 30-day receipt window.
A timed-out local observer can resume read-only verification; it must not infer
that another merge or publication is needed. Local release skills still own
exact-result tags and downstream release verification.

To roll back, set mode to `disabled`, inspect in-flight requests and restore the
captured protection if necessary. Keep completion verification running for
already-merged changes. Do not revoke/delete old credentials during rollback.
Source tests prove contracts, not deployed token permissions or live completion.

## Portability

The canonical helpers are `skills/merge-pr/scripts/merge_gate.py` and
`merge_completion.py`. Other repositories install them together under
`.github/scripts` using the matching templates in github-workflows. Adapt every
CI workflow with the input/checkout/evidence contract and explicit path policy.
Pages is opt-in in generic templates. There is one Actions implementation and
no compatibility fallback to the previous credential path.

See the [Actions protocol](../skills/merge-pr/references/actions-protocol.md),
[built-in token](https://docs.github.com/en/actions/concepts/security/github_token)
and [Pages build API](https://docs.github.com/en/rest/pages/pages#request-a-github-pages-build).
