# Project grouping and remediation batches

Read after target collection and before ordering review work. These are rules
for the existing host agent, not a separate executable scheduler.

`--issue-batch` is an explicit cap on filtered selection; omission means all
matches. A remediation batch groups compatible selected issues. Freeze the
selected set after number/AND-label/project intersection and before grouping or
effects. Preserve repository order; priorities never admit unselected issues.

## Project ownership and counts

Keep repositories distinct and finish them in `--repo` order. Within one repo,
read titles, bodies and relevant discussions, then use paths, project manifests,
instructions, accepted docs and source to identify project roots. Labels and
keywords are evidence leads, not authoritative ownership or validity.

Assign one primary owner per selected issue and record other affected projects
as references. Count each distinct selected issue once, under its primary
owner. Use the project that owns the remediation boundary; a verified shared
component can itself be that project. The invocation folder determines default
selection scope, not every selected issue's owner. Do not treat a title path as proof. Repository-wide work with proven root ownership
uses `.`; unclear ownership uses `Uncertain project`, never `.` by default.
Uncertainty blocks owner-dependent planning, not independent groups.

For explicit `--issues`, count only project-matching selected existing issues, including selected
closed issues assessed without reopening. Do not count unselected related
issues, PRs, missing numbers, or duplicate observations. Counts describe the
frozen collection, not a live counter that changes with later issue state.

## Project order

Use the priority paths already validated and normalized by the
[local argument preflight](github-operations.md#validate-the-invocation-locally).
Unlike whole-repository `--project .`, priority `.` orders only the group with
verified root ownership. This phase resolves project-group matches; it is not the first validation of
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

## Related groups

After research, record evidenced relationships: shared underlying problems,
workflow boundaries or material dependencies. Read related discussions together
and preserve each issue's review status, type, finding, confidence and evidence. Mere keywords
or product membership do not establish relatedness. No transitive merge is
implied by an overlapping issue. Cross-repository evidence uses links only;
repository-local labels never represent a cross-repository group.

For authorized label/comment effects, use stable shared labels under
[label management](labels.md), with comments
linking exact members and explaining the relationship. Related groups may span
primary owners inside the selected product's proven affected scope, but cannot
expand the selected write set or imply shared implementation. Unselected related
issues remain context. Every reported group identifies any incomplete labeling.

## Remediation batches

Investigate one project at a time, reading complete relevant discussions and
source evidence before finalizing its batches. Every selected issue belongs to
one review/remediation batch, including singleton or unresolved batches. Related
groups are independent and may overlap; remediation batches do not. Record the common problem,
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

Without `--action create-plan`, do not write plans or an implementation
overview. Review stops at findings; close proceeds to the separate
[closure](closure.md) checks. With it, one plan covers eligible evidenced actionable
members of each compatible batch under [triage](triage.md); record deferred,
duplicate, rejected and unresolved members separately.
If a missing decision or member dependency is material to the shared change,
leave the affected planning pending. Do not mark a dependent subset plan-ready merely
to bypass unresolved evidence. Independent eligible members may proceed to requested planning.

Example: three BGP issues sharing one route-reconciliation defect and two SSH
issues sharing one access-policy defect form two batches and, when accepted,
two plans only when the selected action is `create-plan`. A high-severity SSH batch precedes a normal-severity BGP batch even
though BGP has more issues. If the BGP claims require different remediations,
split them instead of forcing one plan.

## Progress and completion

Keep a compact issue registry in working context: full identity, observed state,
primary/affected projects, evidence limits, batch, review status, type, finding, relationships, optional plan path and
verified/pending effect status. The project overview is a review/approval snapshot, not a live backlog ledger.
Do not introduce a committed delivery ledger,
private runtime or separate state machine. Existing permitted task-state support
may hold sanitized progress, never sensitive issue contents or effect proof.

Report discovery completeness separately from review completion. A completed review can coexist with failed requested planning or closure. A saved shared
plan can coexist with a verified plan-ready comment for one member and a failed
comment for another. Preserve those distinct outcomes. On rerun, refresh targets and
read existing artifacts/comments before effects; summaries are not receipts.
