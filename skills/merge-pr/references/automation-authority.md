# Enrolled Actions authority

Read the live default branch's `.github/ai-automation.json` and
`.github/merge-policy.json` before effects. When `automation_required` is true,
this profile replaces local merge/release effects for that repository. Failure
to read authoritative enrollment is a blocker, never a fallback to local effects.
Repositories without enrollment retain their independently configured workflow.

The provider-neutral client is `merge-pr/scripts/automation_client.py`. It reads
live enrollment, derives the default branch and dispatches the configured
workflow using the existing `gh` identity. It receives no inference or cloud key.
It never approves, merges, closes PRs, creates tags or uploads release assets.

```text
python3 <merge-pr>/scripts/automation_client.py --repo OWNER/REPO request PR --operation review
python3 <merge-pr>/scripts/automation_client.py --repo OWNER/REPO request PR --operation process
python3 <merge-pr>/scripts/automation_client.py --repo OWNER/REPO waiver PR --reason REASON --wait
python3 <merge-pr>/scripts/automation_client.py --repo OWNER/REPO waiver PR --reason REASON --revoke --wait
python3 <merge-pr>/scripts/automation_client.py --repo OWNER/REPO release PROJECT --pr PR --completion-run RUN
```

Review-only requests use `review`. An authorized complete lifecycle uses
`process`; repository policy controls bounded repairs, evidence-backed closure and
merge. Preserve report-only and active SDLC boundaries. Skill invocation and
request acceptance are not evidence of completion. Observe the controller and
protected completion records, retaining their exact PR/head/result/run identity.
Never submit a second merge request using a local review attestation.

For a requested lifetime waiver, create/reuse the PR as **draft**. Dispatch the
waiver with the creator's human identity and wait for `WAIVER_REGISTERED`; only
then make it ready and request processing. A failed/pending waiver leaves it
draft. The controller stores repository/PR numeric identities, actor, reason and
revision in private durable state. New commits and reopenings retain it; explicit
revocation changes the revision and invalidates earlier admission. Labels, PR text
and model output cannot create or revoke it. It waives eligible missing or
inapplicable live validation; failed/intervened trials and known defects remain
blocking. It never waives cleanup, CI, exact-head review, protection or the
trusted-source policy.

For releases, add an immutable semver fragment to the original change PR using
the repository's registry and schema. The largest declared bump since the last
verified release determines the version. Never silently choose patch for unknown
compatibility. A supplied tag is an assertion, not a competing version allocator.
After exact-result merge completion, the Actions coordinator owns version
reservation, annotated tag, isolated build and verified publication. Manual
laptop requests and automatic release intake use that same workflow and durable
reservation. Do not run local prep/tag/push primitives, roll over a changelog or
create an extra release-prep PR in this profile. Resume the original reservation
and artifact hashes; remote file existence is not verification.

Generated agent sessions use these skills as analysis/planning capabilities only.
They return the host's closed contracts and never invoke this publisher client.
Only the credential-separated trusted controller can authorize effects. The
`merge-pr` broker remains the sole merge API owner.

Enrolled controllers admit newly opened PRs once; old or terminal PRs need a new
manual dispatch. Workflow reruns preserve request identity and original budgets.
Recovery and ordinary CI callbacks must not admit or mutate an unrequested backlog.
Candidate CI v2 and review-v2 evidence bind request, both repositories, branch,
head/base/tree and trusted producer. Legacy attestations cannot grant new authority.

Fork merge eligibility uses the repository variable
`AI_AUTOMATION_MERGE_FORK_CONTRIBUTION`, default false. True permits qualified
forks through the existing broker without granting fork branch writes. The active
request ceiling prevents false-to-true escalation. Each privileged run applies
current restrictions; immediate stop also requires cancelling already-running jobs.

The [shared PR lifecycle](../../review-pr/references/pr-lifecycle.md) owns status
labels, causal live-test repair, one replacement PR with inherited authority and
budgets, and independent issue completion. The hosted controller journals these
effects; labels never authorize them. Terminal completion may verify a later
manual merge without reopening execution authority.
