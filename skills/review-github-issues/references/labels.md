# Issue label management

Read before any issue-label effect, including linked-issue completion. Ordinary
standalone invocation authorizes justified use of existing repository labels;
PR-caller mode remains read-only and returns recommendations to its outer owner.
Plan Mode, report-only, repository policy and actual permissions still apply.

## Establish meaning

Read repository label definitions and accepted workflow documentation. Use an
existing label only when its documented meaning matches independently supported
issue evidence. A definition may be in the label description or repository
policy; the name alone is insufficient. `--label` is selection, not a decision.

Preserve unrelated type, severity, ownership and custom labels. Replace statuses
only within a documented mutually exclusive workflow family. Do not infer that
all status-looking labels are exclusive. Do not copy PR `merged` onto an issue,
create/rename/delete repository labels, or expand permissions. Missing or
ambiguous mappings remain recommendations; they do not block independent
authorized comments or closure. Report labeling as pending when unsupported.

## Apply and verify

1. Bind issue/repository/API identity, supported disposition, mapping evidence,
   exact additions and permitted removals. Refresh issue labels, discussion and
   close/reopen events immediately before each effect; reassess material drift.
2. Add the supported existing status, then read it back before removing only
   the explicitly superseded statuses. Prefer additive/per-label operations;
   never replace the entire label set from a stale snapshot. If the connector
   cannot preserve unrelated labels, leave that effect pending.
3. Verify final labels independently. Terminal issue labels require observed
   actual closure, not intended closure. An intermediate dual-status result
   after failed removal is partial, not success. Preserve human labels and do
   not roll back successful unrelated effects.
4. Keep label, rationale-comment and closure outcomes separate. An equivalent
   existing comment can be reused. Before closing, verify the rationale and
   fresh independent closure criteria; never replay an old close over human
   reopening. After ambiguous responses, observe before retrying; uncertain
   observation leaves that effect pending, without switching transports to
   blindly repeat it.

Do not require a comment solely for every cosmetic label correction. A changed
disposition or closure still needs the existing concise evidence-backed rationale.
Reconcile closed issues without reopening or redundant closure notices.

## Vendor basis

Checked 2026-11-16: [GitHub label operations](https://docs.github.com/en/rest/issues/labels)
distinguish additive labels, individual removal and replacement of the whole
set. Discover the actual tool schema and preserve this operation distinction.
