# Issue label management

Apply scoped review-status, type, finding and relationship labels and missing definitions
below only within the core Invocation Policy's effect authority. Implicit
assessment alone grants none; skip unrequested labels without blocking the
assessment or requested plans. PR-caller mode returns read-only
recommendations to its outer owner; it never creates labels or inherits these
effects. Host Plan Mode, report-only, repository policy and actual permissions
remain binding.

## Establish semantics and safe effects

Read repository label descriptions and accepted workflow/automation policy.
Reuse existing labels only for exact documented semantic matches. An existing generic
`enhancement` definition may include new capabilities; reuse it only when its
documented meaning agrees with improvements to existing capabilities.
Names alone are insufficient. `--filter` requires all literal labels and is selection, never a decision; do not
create a missing selection label to manufacture a target set.

Assessment labels such as already-fixed and duplicate are valid
on open issues. They do not mean actual closure. Do not reuse a lifecycle label
whose definition requires closure for an assessment. When advising an authorized
PR owner, actual terminal lifecycle labels still require observed closed state.

Inspect relevant repository automation and documented comment commands before
reusing mappings. Do not intentionally trigger closure, assignment, implementation
or another forbidden effect through a label/comment. Even close must use its
verified explicit state transition, never automation that bypasses its checks.
If a canonical label or comment triggers a forbidden effect, leave that effect
pending while continuing independent safe effects. Never invent or reuse a
prefixed fallback label, change automation or route around its effects. Do not install automation or claim unknown external automation
was exhaustively audited. An unexpected downstream effect is reported, never
silently compensated by reopening or expanding authority.

Preserve unrelated labels. Remove only superseded labels in an explicitly
managed/documented family when evidence supports replacement. Types can coexist
for distinct claims. At most one whole-issue finding applies; `reviewed` is
independent and must not be removed when replacing a finding. Evidence-supported
unresolved work needs no finding label. Remove a superseded managed finding
when reassessment supports that transition, including a transition to no finding.
Ambiguous
existing meanings or name collisions stay unchanged and pending, not renamed,
overwritten or silently assigned a different meaning.

## Assessment status and findings

`reviewed` records a substantive assessment with a documented result or precise
information gap; it does not mean accepted, feasible, designed, planned or
scheduled. An assessment may conclude `needs-information` with no certain type.
Merely listing, opening or skipping an issue never warrants `reviewed`. Required
research that could not be performed must not be presented as completed review;
report that gap without adding the marker. Existing markers are historical
metadata, not proof that current evidence was reassessed.

Types describe supported claims; findings describe whole-issue dispositions.
For example, `reviewed` + `bug` records an assessed unresolved bug;
`reviewed` + `bug` + `already-fixed` records verified full resolution.
`reviewed` + `needs-information` can stand without a type. None grants planning,
implementation or closure authority. Deferred/not-planned findings require an
explicit maintainer decision or accepted policy, not an agent's priority choice.

## Missing definitions allowed

Create only a needed definition in this table, the relationship family below,
or the explicitly requested progress label below, with its stated description
and six-digit color. Reuse matching definitions;
do not recolor, rename, delete or archive existing labels. Descriptions are
public-safe and under GitHub's 100-character limit.

| Label | Description | Color |
| --- | --- | --- |
| `bug` | Supported implemented behavior violates its accepted contract. | `d73a4a` |
| `gap` | An accepted capability or required path is missing or incomplete. | `d93f0b` |
| `feature-request` | Requests a new capability outside the existing supported contract. | `a2eeef` |
| `enhancement` | Extends or improves an existing capability beyond its current contract. | `84b6eb` |
| `documentation` | Supported behavior needs missing, corrected or clearer documentation. | `0075ca` |
| `question` | Requests usage guidance, explanation or clarification. | `d876e3` |
| `reviewed` | Assessment recorded; no approval, feasibility or delivery commitment. | `0e8a16` |
| `needs-information` | Material information, evidence or a maintainer decision is needed. | `fbca04` |
| `already-supported` | Every material requested outcome is already supported. | `c5def5` |
| `already-fixed` | Every material reported defect or gap has verified resolution. | `c5def5` |
| `duplicate` | Every material claim is covered by a verified canonical issue or pull request. | `cfd3d7` |
| `out-of-scope` | Evidence places the request outside the supported product boundary. | `cfd3d7` |
| `deferred` | Maintainer decision or accepted policy postpones work with reconsideration conditions. | `fef2c0` |
| `not-planned` | Explicit maintainer decision or accepted policy excludes the requested work. | `cfd3d7` |

A missing severity, priority or ownership label remains a recommendation.
No arbitrary new label families are authorized. This table is the canonical
catalog for this skill; other workflows can own additional repository labels.
Shared `needs-information` and `duplicate` definitions support issue and PR use,
but issue duplicate findings still require a canonical issue under [closure](closure.md).
Do not apply PR lifecycle transitions to issues or recolor shared definitions
through routine triage. Ambiguous semantics remain pending.

Repository-wide catalog reconciliation, rename, merge, recolor and deletion
require separately requested maintenance; none is a public action of this skill.
Such maintenance must refresh definitions, memberships on open/closed issues and
PRs, and known automation consumers before effects. Snapshot metadata privately,
preserve unrelated labels/state, verify destination membership before removing
obsolete definitions, and reconcile partial/uncertain effects before retry.
Backfill reviewed only from prior assessment evidence; migrating historical
findings is not fresh review and closed state never proves already-fixed.
The canonical names have no legacy aliases; literal filters do not translate
retired names. Preserve unrelated PR, automation, community, progress and
relationship labels rather than treating this catalog as the repository allowlist.

## Separately authorized implementation progress

`being-fixed` describes work already admitted by a separate implementation owner;
review, create-plan, eligibility and a saved plan never imply that admission.
Apply it only when the user explicitly requests this progress label and the
selected issue's repair is actually underway, or an authorized lifecycle owner
requests it. Do not implement work from this skill or require another approval
when the same task already authorized implementation and labeling.

Reuse a definition that means an authorized repair is underway and the issue
stays open until the fix is merged and verified on the default branch. If absent,
that explicit progress-label request permits creating `being-fixed` with color
`D4C5F9` and description `Authorized repair underway; keep open until merged and verified on the default branch.`
A conflicting definition remains pending. Check automation, add without replacing
other labels, and independently read back the result. This label never proves
resolution or grants implementation, publication or closure authority.

Leave it while admitted work remains active. Remove only this progress label
when the authorized owner verifies merged resolution and closure, or explicitly
ends the repair; preserve unrelated labels. A stopped or blocked run must report
its actual progress rather than claiming completion.

## Relationship groups

Use `related:<anchor>-<topic>` on selected members of an evidenced group, with
color `bfd4f2` and description `Related issue group: <topic>; anchor #<anchor>.`
Normalize topic to lowercase ASCII alphanumerics separated by single hyphens,
strip edge hyphens, cap at 24 characters and strip again; use `issue` if empty.
Anchor is the lowest initial selected member number and stays fixed. A group
requires at least two evidenced members; a singleton report needs no group label.

Before creation, search existing related definitions and membership/comments for
the same repository, relationship basis and member identities. Reuse an existing
matching group regardless of changed wording or new lower-numbered members.
Never infer identity from a name or anchor alone. If the base name belongs to a
different group, use the first unused suffix `-2`, `-3`, and so on. Reconcile an
ambiguous creation by readback before choosing a different name or retrying.
Keep names stable after creation; put full exact membership links and rationale
in comments, not the bounded label description.

Distinct overlapping groups may coexist. For example, {10,11} sharing routing
behavior and {10,12} sharing diagnostics use different labels. Do not merge them
transitively or treat shared keywords as evidence. Relationship membership is
not duplicate status, native dependency/hierarchy or plan membership.

Refresh all material member evidence and live label membership before changes.
Only selected issues may receive additions/removals. Unselected related issues
can be linked as context, but their missing label application remains explicitly
pending/outside scope. Do not claim all members are linked when only a subset
was labeled. Preserve unrelated existing memberships; disputed splits/merges
remain pending instead of silently rewriting a group. Never delete a repository
label as group cleanup.

## Apply and verify

1. Bind repository/issue/API identity, evidence, mapping, exact additions and
   permitted removals. Refresh labels/discussion/state and material dependencies.
2. Prefer connector capabilities. If label creation or additive/per-label
   operations are absent, disclose authenticated `gh api` REST fallback using
   existing access. Do not use a whole-label replacement tool as an additive
   substitute. Missing safe transport/access leaves the effect pending.
3. Read existing definitions first. Create only missing authorized definitions;
   independently read back exact meaning/color before use. A concurrent matching
   creation can be reused; an incompatible definition remains pending.
4. Add justified labels, verify additions, then remove only proven superseded
   managed/mapped labels individually. An intermediate dual outcome is partial,
   not success. Never replace the whole label set from a stale snapshot.
5. Verify final labels and observed issue state independently. Report label and
   comment outcomes separately. Do not require a new comment solely for a
   cosmetic correction; changed findings need a concise evidence-backed comment.

After an ambiguous response, observe before retrying. Do not switch transports
to blindly repeat a write, repair credentials or expand permissions. Existing
closed issues are assessed without reopening or redundant closure notices.

## Vendor basis

Checked 2026-11-22: [GitHub label operations](https://docs.github.com/rest/issues/labels)
distinguish creation, additive issue labels, individual removal and whole-set
replacement. [Managing labels](https://docs.github.com/en/issues/using-labels-and-milestones-to-track-work/managing-labels)
confirms deletion removes issue and PR associations. Discover actual tool schemas; do not assume an update API is
additive. Shared related labels are this skill's convention, not native GitHub
relationships. [Sub-issues](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues)
represent hierarchy and are not created by this workflow.
