# Justified issue closure

Read only for standalone --action close. This action includes fresh research
and review but never planning or implementation. PR-caller mode remains
read-only; the outer PR owner has its own independent lifecycle contract.
Explicit close supplies authority for justified selected issues without another
routine approval; it does not override access, host limits or repository policy.
Resolve natural-language closure under the core Invocation Policy. Its comment
and label authority remains independent; reuse an equivalent verified explanation
when available. If no authorized explanation can be published or reused, leave
closure pending after assessment rather than bypassing that prerequisite.

## Evidence and eligibility

A filter label is a candidate selector, never a closure decision or receipt.
Research every material claim and accepted criterion, full relevant discussion,
related evidence, supported versions and current accepted source. A local dirty
fix or unmerged PR alone cannot prove resolution. Verify the applicable current
default/release basis and criteria; do not invent release or live-test gates.
A source ancestor without checking a later revert/regression is insufficient.

| Fresh whole-issue outcome | State reason | Additional requirement |
| --- | --- | --- |
| already-resolved / already-supported | completed | Every material defect or requested outcome has applicable verified coverage. |
| duplicate | duplicate | Every material claim is covered by an accessible verified canonical issue. |
| out-of-scope / not-planned | not_planned | Accepted maintainer policy or explicit disposition supports excluding the work. |
| confirmed / deferred / needs-information / mixed unresolved claims | None | Keep open; explain the outstanding claim or evidence gap. |

A label or the agent's unsupported preference is not accepted maintainer policy.
Do not close a valid deferred request merely because it is low priority. Project
filter exclusion is not an out-of-scope disposition. Do not override a later
human objection/reopening or changed accepted criteria on unchanged evidence;
leave closure pending unless material new evidence and authority resolve it.
General close authority does not silently overrule a specific maintainer hold.

For a duplicate, bind the canonical host, repository, issue number and API issue
ID. Read its material claims and current state; it may be open or closed, but
must still cover the report. Exclude self-reference, PRs, inaccessible targets
and cycles. Never use the issue number as the REST API issue ID. Read the
canonical issue only; do not alter its state, comments or labels unless it is
independently selected and has its own justified action. Only the selected
duplicate's native canonical association is authorized here; no hierarchy edits.

## Execute and independently verify

1. Refresh selected identity, repository, project/filter membership, issue state,
   material comments/events and source/sibling dependencies. Preserve the frozen
   set. If it is already closed, report observed/already-closed and leave its
   reason/association unchanged; do not claim this invocation closed it.
2. Determine the fresh outcome and exact reason. Ineligible cases keep the
   independently justified review finding; no state mutation or plan follows.
3. Publish or reuse one concise explanation with decisive evidence and, for
   duplicates, the canonical link. Independently retrieve the matching comment
   by acting author, issue and content. Do not claim closure before it occurs.
   If the explanation cannot be verified, closure stays pending. Label failure
   alone does not block closure when identity, evidence and explanation verify.
4. Immediately before the state mutation, recheck material changes, human holds,
   open state and exact canonical target. This is read-before-write, not atomic
   compare-and-swap; a race remains possible and must be reported if observed.
   Own justified review label changes do not cancel admission, but new external
   changes require reassessment and may leave the item skipped/pending.
5. Prefer a structured connector operation with the exact supported state,
   reason and duplicate target. Discover its current schema. If it cannot express
   the required effect, disclose authenticated gh REST fallback using structured
   input; no installation/auth repair. REST PATCH sends only state=closed,
   state_reason=completed|not_planned|duplicate, and duplicate_issue_id only for
   a duplicate. Do not send labels or other issue fields in this patch.
6. Independently read issue state, reason and, for duplicates, canonical target
   through the issue/event/relationship data available on that host. A successful
   update response is insufficient. If association readback is unsupported or
   ambiguous, report observed closed state separately and duplicate verification
   pending. Never downgrade duplicate to completed/not_planned for convenience.

The current GitHub API supports all three reasons plus duplicate_issue_id.
Actual host/tool support still controls execution. If the required write is
unsupported, leave closure pending; do not choose another semantic reason or
simulate native association with a comment alone. If ordinary reads cannot
verify the effect, do not claim it verified.

## Uncertain responses and partial effects

On timeout or uncertain response, read authoritative state/events before retrying.

- Correct reason/target is observed closed: reuse that result, no second update.
  Attribute only what evidence supports; a concurrent actor may have closed it.
- Complete readback proves no closure and no intervening close/reopen/hold:
  refresh evidence/explanation and permit one bounded retry, respecting provider
  rate limits. A current open state alone does not prove the prior write absent.
- Conflicting reason/target, later human reopening/hold, unavailable event history
  or ambiguous readback: stop the mutation and report uncertainty. Never reopen,
  overwrite another actor's disposition or blindly switch transports to retry.

Comments/labels may succeed while closure fails. Keep each effect's evidence
separate and continue independent eligible members. Never delete an explanation,
remove labels or reopen as compensation. On cancellation, stop new effects and
report exact completed/pending identities without starting another batch.

## Sources

Verified 2026-11-19:

- [GitHub update issue API](https://docs.github.com/en/rest/issues/issues#update-an-issue)
  documents closed state, completed/not_planned/duplicate reasons and the canonical
  duplicate API issue ID. It also distinguishes label replacement from state edits.
- [GitHub CLI issue close](https://cli.github.com/manual/gh_issue_close)
  documents reason and duplicate-target options. Inspect the installed CLI before
  using version-specific flags; REST fallback must retain the same semantics.
