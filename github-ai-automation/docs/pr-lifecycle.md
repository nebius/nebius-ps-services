# PR lifecycle implementation

The portable [skill contract](../../skills/review-pr/references/pr-lifecycle.md)
defines the shared decisions. This document describes the hosted owners and
observable limits. All deployment defaults remain disabled.

| Owner | Responsibility |
| --- | --- |
| `controller.py`, `stages.py` | Full-diff value/review, typed issue/applicability/diagnosis/adoption assessments, isolated repair and independent verification. |
| `execution.py` | Trusted issue snapshots, strict evaluation-input/output v2 ingestion, evidence cross-checks, idempotent attempt accounting and live-result gates. |
| `adoption.py` | Complete feature inventory, per-feature commits, one journaled replacement, inherited execution transfer and explicit manual campaign renewal. |
| `requests.py` | Original authority ceiling, three-hour deadline, two publication attempts, no automatic child enrollment or recursive adoption. |
| `lifecycle.py` | One status label, public-safe comment/readback and durable completion obligations under the canonical PR lease. |
| `issue_resolution.py` | Closing-link discovery, complete per-issue assessment, scope/event guards and separate rationale/closure journals. |
| Existing continuation | Model-free patch, adoption, labels and issue effects using its existing permissions. |
| Existing merge broker | Protected merge only; issue write permissions are unchanged. |

The label family is `under-review`, `testing`, `being-fixed`, `manual-merge`,
`blocked`, `needs-information`, `closed`, `merged`. Labels describe evidence and
never authorize effects. A review-only hosted request has no qualification or
merge capability; its completed review reports missing lifecycle evidence rather
than asserting manual readiness. Manual-ready candidates receive **Accepted for
manual merge.** with the remaining gate. Known failed/intervened trials and
correctness defects remain blocked.

Failed live evidence stays bound to source/scenario/execution. A bounded retry
invokes troubleshoot; proven product defects may produce independently reviewed
repairs. Design handles proven contract changes within accepted intent. Fixture,
infrastructure or unresolved causes without an authorized repair owner produce a
precise handoff. A new head re-enters review, CI and live qualification. Neither a
later waiver nor a fresh manual request erases a retained failure.

Large conflicts produce a complete private proposal: inventory of the entire
original diff, feature decisions/dependencies/criteria, independent inventory
review, per-feature patch/checks and final whole-result verification. The trusted
publisher verifies that applying the feature commits yields exactly the final
reviewed aggregate tree. One complete proposal consumes one attempt. Only then
does it publish a deterministic branch and one attributed replacement PR.
Unknown branch/PR creation outcomes are observed before any retry.

The parent stops execution before the child is dispatched. The child inherits
remaining time/attempts and originating fork merge restrictions. Its verified
repository-owned branch can be repaired under adoption authority; no permission
to write the original fork is created. Canary rollout cannot implicitly enroll a
replacement: automatic adoption requires general admission. A new explicit manual
process request on either parent or child renews the existing campaign and same
replacement, journals both state changes, and freezes the new deadline once.
Automatic callbacks never renew it. Bot-authored replacements still require
independent maintainer review when GitHub prohibits self-approval.

Completion records preserve labels/comments and independent issue obligations
without reopening implementation authority. Recovery dispatches their existing
continuation owner. Open terminal requests wait for a later manual merge/close;
a changed PR head, successor request or policy generation invalidates old effects.
Each pending record is reconciled independently, so one blocked record does not
prevent others from finishing.
Before each completion effect, intersect retained and current request capabilities
with the current mode and exact unexpired rollout permit. An expired execution
deadline does not prevent completion, but an expired canary permit does.

Issue discovery uses explicit closing links, with full numeric identity and
bounded pagination. Each issue receives a base/head-specific resolution decision
and scope/discussion/state-event snapshot. Rejected PRs leave valid unresolved
issues open. Replacement assessments include the original's linked issues.
Before merge, closing issues need complete current-head evidence. After merge,
verify the actual merged result tree equals the assessed tree and current default
still equals that result. Otherwise retain `reassessment_required`; do not infer
resolution from ancestry. This deliberately requires new semantic assessment if
main advanced or a possible revert occurred. The completion records
`needs_reassessment`; a fresh authorized issue review owns that follow-up, while
the merge outcome remains successful.

If native closure left a verified issue open, process authority permits a concise
rationale comment followed by explicit closure and readback. Comment and close
have separate journals; a lost comment response cannot duplicate the comment or
skip its readback. Later human reopening, scope or discussion changes block old
closure. A successful merge remains successful when an issue or original-PR
closure is pending. Cross-repository issues remain an explicit access/authority
blocker; no token expansion is attempted.
If a close succeeded but its response was lost, reconcile the pending journal
from the observed closed state without sending another close request.

## Deployment and evidence

Quiesce old writers and reconcile pending effects before deploying evaluation v2,
controller source, bundled skills and continuation workflow together. Old wire
records fail closed; no compatibility adapter is supplied. New source does not
activate installed skills or deploy workflows. Run the existing hosted canary,
CAS, isolation, permissions, actual Nebius trial, protected-merge and completion
qualification before enabling general effects. Local tests and mocked providers
cannot establish those results.

Official GitHub behavior is linked in the shared skill contract. In particular,
a closed unmerged PR does not cause native issue resolution, repository settings
can disable native auto-close, and bot-created PR events are not a reliable
substitute for explicit workflow dispatch.
