# GitHub Issues

Research and classify one product's GitHub issues, save shared remediation plans,
or close issues with verified justification. Selection is independent from the
action. Default review publishes concise findings and scoped labels without
creating plans or changing issue state.

When implementation is separately authorized and underway, an explicit request
to mark the selected issues `being-fixed` may create/reuse that progress label.
Review and planning alone never apply it or authorize implementation. The label
remains until its lifecycle owner verifies merged resolution or ends the repair.

## Usage

```text
$github-issues
$github-issues --action review --project api
$github-issues --filter "bug,reviewed"
$github-issues --project api --issues 10,11 --filter bug
$github-issues --filter "reviewed" --action create-plan
$github-issues --filter "already-fixed" --action close
$github-issues --filter bug --issue-batch 10
$github-issues --repo example-org/alpha,example-org/beta --project api
$github-issues --project . --priority services/api,libraries/shared
$github-issues --help
```

Use `/github-issues` in Claude Code or `/skills:github-issues` through its plugin.
Matching natural-language requests can also select this skill. For example,
"Assess issue 17 in example-org/widget" selects review, "Save a shared plan for
issues 10 and 11" selects create-plan, and "Close issue 17 if it is fully
resolved" selects close. Natural-language assessment alone is read-only;
request comments or labels when those effects are wanted. Planning or closure
does not itself request comments/labels, and closure still requires an
authorized, independently verified explanation. Named standalone invocations
retain their documented effects. Refactoring or discussing this agent skill
does not authorize issue actions; it is not a standalone executable.

Implicit selection and effect authority are separate. The description,
Invocation Policy and host metadata must agree: Codex uses
`allow_implicit_invocation: true`; Claude uses default model invocation without
`disable-model-invocation: true`. The shared validator checks this agreement.
See [Codex metadata](https://learn.chatgpt.com/docs/build-skills#optional-metadata)
and [Claude invocation controls](https://code.claude.com/docs/en/skills).

| Option | Meaning |
| --- | --- |
| `--action review` | Research, classify, label and comment; default when omitted. |
| `--action create-plan` | Review, then save eligible shared plans/overview and offer separate implementation approval. |
| `--action close` | Review, then close only freshly justified selected issues. |
| `--repo` | Comma-separated repository names/URLs, processed in supplied order. |
| `--issues` | Positive issue numbers checked in every selected repository, intersected with other selectors. |
| `--project` | Exact recognized project root or unique name; explicit `.` selects all projects. |
| `--filter` | Comma-separated literal existing labels; every label must match (AND); open issues only. |
| `--issue-batch` | Optional invocation-wide oldest-first cap after filtering; requires --filter; no implicit limit. |
| `--priority` | Exact project roots ordered first; changes order, not selection or severity. |
| `-h`, `--help` | Explain every action/option and stop before further tools or writes. |

All syntax is validated before discovery. Reject unknown/repeated flags,
invalid/empty values and positional selectors. There is one action per invocation;
no combined review/plan/close flags. The old skill name and --label, --create-plan,
--review, --close and --issue spellings are retired without aliases.

From a project or its subfolder, infer that recognized owner. At repository root,
infer a sole product or ask when several exist. Use --project . for intentional
whole-repository selection. Ambiguous folders never widen scope. Resolve project
identities independently in each repository without substituting another checkout.

Filters trim surrounding whitespace and deduplicate literal names. All labels,
explicit numbers and project scope intersect. Labels with commas cannot be
expressed by the CSV filter; use explicit numbers without a filter instead.
Without an explicit cap, scan all matching open issues, including every page.
The previous five-issue default no longer applies. With a cap, admit oldest
matches after project filtering in repository order; freeze identities before
any effects and never refill skipped slots. Always check every explicit
repository/number pair even with a filter/cap. Missing labels never cause creation
or unfiltered fallback. Report incomplete and unvisited coverage honestly.

## Research and classification

Every selected issue is researched, including requests that may be deferred or
already supported. Review complete material discussions, supported contracts and
versions, implemented paths, tests/docs, related issues and PR outcomes. Verify
relevant external technical claims against current authoritative sources and
state unavailable evidence. Research depth follows unresolved material questions;
it is not a fixed query quota or an unrelated full-web crawl.

Separate assessment status, type and optional finding. `reviewed` is green
(`0e8a16`) and means an assessment was recorded; it makes no acceptance,
feasibility, design or delivery promise. A substantive assessment identifying a
precise missing fact can be reviewed; merely opening/skipping an issue or being
unable to perform required research cannot earn that marker.

| Type | Meaning |
| --- | --- |
| `bug` | Incorrect implemented supported behavior; red (`d73a4a`). |
| `gap` | Missing or incomplete behavior already promised by an accepted contract. |
| `feature-request` | New capability outside the existing supported contract. |
| `enhancement` | Extension or improvement of an existing capability. |
| `documentation` | Missing, incorrect or unclear documentation. |
| `question` | Usage guidance or clarification, distinct from missing review evidence. |

Optional findings are `needs-information`, `already-supported`, `already-fixed`,
`duplicate`, `out-of-scope`, `deferred` and `not-planned`. At most one applies to
the whole issue; it coexists with reviewed and the supported types. Unresolved
work needs no special finding: `reviewed` + `bug` is sufficient. Uncertain type
may remain unset with `reviewed` + `needs-information`. Deferral/rejection requires
an explicit maintainer decision or accepted policy, not an agent's priority choice.
Whole-issue supported/fixed/duplicate findings require every material claim to be
covered; a mixed report retains outstanding work and explicit uncertainty.

The [label policy](references/labels.md) owns names, descriptions and colors.
Routine triage creates only needed missing definitions and preserves unrelated
labels. Repository-wide rename, merge, recolor or deletion is separate explicitly
requested maintenance; PR, automation and community labels remain independently
owned. Canonical names have no legacy aliases or filter translation.

Severity, validity, confidence and priority remain separate. Rare serious defects
remain actionable. Age, popularity or failure to reproduce does not invalidate a
report. Sensitive evidence follows the repository's private reporting policy.

## Related issues and review order

Keep repository order, then explicit project priorities, remaining owners by
selected count/path, and uncertain owners last. Preserve primary ownership for
shared-component issues affecting the selected product. Review/remediation
batches order by supported severity, count and lowest number.

Related groups record evidenced shared problems, workflows or dependencies.
Use a stable common label such as `related:10-routing`, with direct member links
and rationale in review comments. Labels distinguish overlapping groups and
remain stable on reruns; shared keywords or transitive links alone do not prove
a group. This is grouping metadata, not native hierarchy or duplicate status.

Related issues can need separate plans. With `--action create-plan`, combine only
eligible issues sharing a coherent remediation under the same primary owner and
repository, preserving each issue's acceptance criteria. For example, issues #10
and #11 can share a routing label and one plan when one change addresses both;
related diagnostics work may require its own plan.

Complete pagination is required for unbounded discovery. AND label selection
filters by product before any explicit cap, freezes identities before effects
and never refills skipped slots. Explicit selectors never silently add related
issues to writes or planning. Report exclusions and incomplete coverage.

## Review and requested plans

Default invocation authorizes concise review comments and justified
review-status, type, finding and relationship labels. Reuse exact documented equivalents or
create only the defined missing labels. Preserve unrelated labels, inspect known
automation triggers and independently verify each effect. Review and create-plan
preserve state, including duplicate/already-fixed findings; no action reopens. Label failure does
not invalidate an independently completed review comment.

Without `--action create-plan`, do not invoke design, write/update plans or overviews,
or offer implementation approval. Existing plans do not enable that path.

With it, research feeds design as an internal adviser. Plan evidenced actionable,
non-deferred work after resolving material product decisions and
dependencies. Save each compatible batch under its real owner at
`docs/github-issues/batch-<anchor>-<topic>.md`, including singleton batches.
Reuse matching files with stable names, preserve human edits and reconcile
conflicting/overlapping artifacts. Verify complete files before claiming readiness.
A save failure affects planning, not completed review findings.

**Detailed issue implementation plans appear only in files, never chat or
GitHub comments.** Chat may show verified local file links, scope, owners,
dependencies and readiness. Repository-relative locators in GitHub must say
saved locally until the file is actually published.

Only requested ready planning produces `docs/github-issues/project-plan.md`,
with exact batch digests, membership and dependencies. Offer the exact verified
set for separate approval. Natural approval of that proposal starts ordinary
local implementation; explicit `$implement` uses its existing per-batch workflow.
Refresh checkout/project bindings, instructions, plan bytes and material evidence
before either route. No implicit commit, publication or closure authority follows.

## Justified closure

Use --action close to request closure within the selected scope; no second
routine approval is needed. Research and verify every material claim first.
A label, dirty local fix or unmerged PR alone is not proof. Fresh evidence maps:

| Outcome | Closure reason |
| --- | --- |
| Fully resolved or already supported | completed |
| Verified duplicate covering every claim | duplicate with the canonical issue |
| Out-of-scope/not-planned under accepted maintainer policy | not_planned |
| Deferred, unresolved, mixed or uncertain work | Keep open |

Publish or reuse and independently read back a concise explanation, refresh
human dispositions/source evidence, then close and verify state/reason/canonical
target. Already-closed issues remain unchanged. Preserve human holds, avoid
blind retries and never reopen as compensation. Report successful review effects
separately from failed/pending closure. Close creates no plans and performs no
implementation. See [closure policy](references/closure.md).

## Boundaries and validation

Use GitHub connectors where capable; disclose authenticated `gh` fallback for
missing operations. Missing safe access stays pending without authentication
repair or broader permissions. Label changes require additive/per-label semantics,
not replacement of the whole set from a stale snapshot.

Host Plan Mode and report-only requests prohibit GitHub and file writes. Missing
matching checkouts block requested plan saving, not remote assessment. No product
edits, canonical-spec writes, issue creation/body edits, native hierarchy writes,
label rename/deletion, assignments, milestones, Git or PR actions occur in review.

The [shared PR lifecycle](../review-pr/references/pr-lifecycle.md) remains
independent. In PR-caller mode, return evidence and label recommendations read-only;
only the outer authorized owner performs and verifies linked-issue completion.
Standalone actions confer no effects on this adviser and do not revoke the
outer owner's explicit authority.

[SKILL.md](SKILL.md) owns the workflow. References define
[research](references/research.md), [classification](references/triage.md),
[labels](references/labels.md), [selection](references/project-selection.md),
[grouping](references/batching.md), [operations](references/github-operations.md),
[plans](references/plan-files.md) and [handoff](references/implementation-handoff.md).
Trigger and behavior cases live in `evals/`. Use synthetic repositories, mocked
GitHub effects and actual adviser instructions; no live issues for validation.
Static definitions, installation parity, fresh-session loading and behavioral
quality are separate evidence claims.
