# Project grouping and remediation batches

Read after target collection and before ordering review work. These are rules
for the existing host agent, not a separate executable scheduler.

## Project ownership and counts

Keep repositories distinct and finish them in `--repo` order. Within one repo,
read titles, bodies and relevant discussions, then use paths, project manifests,
instructions, accepted docs and source to identify project roots. Labels and
keywords are evidence leads, not authoritative ownership or validity.

Assign one primary owner per selected issue and record other affected projects
as references. Count each distinct selected issue once, under its primary
owner. Use the project that owns the remediation boundary; a verified shared
component can itself be that project. Do not group by the startup subdirectory
or treat a title path as proof. Repository-wide work with proven root ownership
uses `.`; unclear ownership uses `Uncertain project`, never `.` by default.
Uncertainty blocks owner-dependent planning, not independent groups.

For explicit `--issue`, count only selected existing issues, including selected
closed issues assessed without reopening. Do not count unselected related
issues, PRs, missing numbers, or duplicate observations. Counts describe the
frozen collection, not a live counter that shrinks after every closure.

## Project order

Use the priority paths already validated and normalized by the
[local argument preflight](github-operations.md#validate-the-invocation-locally).
This phase resolves project-group matches; it is not the first validation of
user arguments. `.` selects verified repository-root ownership.

Apply the same `--priority` list independently in each repository:

1. Matching project groups, in first-occurrence priority-list order.
2. Remaining known project groups, selected-issue count descending.
3. Break equal counts with repository-relative project path ascending.
4. `Uncertain project` last.

Report priority paths with no selected group in that repo; do not invent a
group, silently substitute a similar path, or discard other projects. A path
may match one selected repo and be unmatched in another. Project review order
does not change issue severity, disposition, labels or delivery commitments.

Show the ordered project/count overview before detailed review. If later
evidence changes ownership, correct the counts and remaining queue explicitly;
do not repeat completed effects or conceal the earlier classification limit.

## Remediation batches

Investigate one project at a time, reading complete relevant discussions and
source evidence before finalizing its batches. Every selected issue belongs to
one batch, including singleton or unresolved batches. Record the common problem,
shared change boundary, member identities, evidence and why the acceptance
criteria fit together. These classification notes are not implementation steps
and may appear in the concise review overview.

Combine issues only when one coherent remediation can address their supported
problems without incompatible requirements. A shared word such as BGP does not
justify combining routing, documentation and unrelated authentication defects.
Do not combine across primary projects or repositories; record dependencies
between their plans instead. True duplicate status requires its own evidence
and canonical issue identity under the triage rubric.

Assess each member's validity, severity and disposition independently. Rank
batches by their highest supported member severity: critical, high, normal,
low, then unknown. Translate any project-specific severity scale explicitly
to that order; absence of evidence is unknown, not low. Break severity ties by
selected member count descending, then lowest member issue number ascending.
Recompute a batch's evidence-based rank if investigation disproves its serious
claim; do not let an unsupported alarming title set severity.

After assessment, one plan covers the accepted subset of each compatible
batch; record deferred, duplicate, rejected and unresolved members separately.
If a missing decision or member dependency is material to the shared change,
leave the affected planning pending. Do not accept a dependent subset merely
to bypass unresolved evidence. Independent accepted members may proceed.

Example: three BGP issues sharing one route-reconciliation defect and two SSH
issues sharing one access-policy defect form two batches and, when accepted,
two plans. A high-severity SSH batch precedes a normal-severity BGP batch even
though BGP has more issues. If the BGP claims require different remediations,
split them instead of forcing one plan.

## Progress and completion

Keep a compact issue registry in working context: full identity, observed state,
primary/affected projects, evidence limits, batch, disposition, plan path and
verified/pending effect status. Do not introduce a committed backlog ledger,
private runtime or separate state machine. Existing permitted task-state support
may hold sanitized progress, never sensitive issue contents or effect proof.

Report discovery completeness separately from review completion. A saved shared
plan can coexist with a published acceptance for one member and a failed comment
for another. Preserve those distinct outcomes. On rerun, refresh targets and
read existing artifacts/comments before effects; summaries are not receipts.
