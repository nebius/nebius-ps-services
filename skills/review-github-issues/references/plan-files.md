# Accepted batch plan files

Read this for a batch with accepted candidates. The artifact is the implementation
handoff; it is not permission to implement, publish or modify canonical specs.

## Resolve the destination

- Find the owning project root from manifests, instructions, documentation and
  code, including affected shared components. A repository-wide issue can use
  the repository root when it is the clear owner. If several projects are
  equally plausible owners, ask for the primary owning project and keep planning
  pending until resolved.
  One plan may describe affected shared components, but does not merge batches
  with different primary project or repository owners.
- Confirm the local project belongs to the selected GitHub repository. For a
  remote-only target, ask for its local checkout/project path before saving;
  do not create the plan in the unrelated current repository or clone silently.
- Use `docs/github-issues/` under that project. Create it if absent only when
  writes are permitted. Ensure all existing path components resolve within
  the project and the destination is a regular file, not a symlink or an
  unrelated object. Stop on unsafe paths or write restrictions.
- New filenames are `batch-<anchor-number>-<topic-slug>.md`, also for singleton
  batches. The anchor is the lowest initial accepted member issue number. Use
  the shared topic as the slug: lowercase, replace non-ASCII-alphanumeric runs
  with one hyphen, strip edge hyphens, truncate to 80 characters and strip a
  trailing hyphen again. Use `issue` if empty. Example: `batch-123-bgp-routes.md`.
  Keep the filename stable when titles, wording or membership later change.

Before creating a file, inspect existing regular Markdown plans for full
repository, project, member and remediation identity. Never match on number,
title, topic slug or filename alone. Reuse a matching artifact even when it has
an earlier per-issue filename. This preserves existing work, not an alternate
public selector interface.

Exact matching membership and remediation reuse one existing plan. An overlap,
changed membership, proposed split/merge, multiple matches, or occupied filename
for another identity requires reconciliation before writing. Read all affected
plans and compare scope, evidence and maintainer edits. If one plan clearly owns
this remediation and a safe scoped update is supported, update it in place;
otherwise ask only for the unresolved artifact-ownership decision. Do not
create a competing plan, automatically merge/delete/rename files or discard
human content. Never add an unselected issue to accepted work or change its
GitHub state/comment merely because a related plan mentions it. Existing
unselected member content stays intact; a material conflicting dependency
keeps the selected planning pending.

## Produce a complete handoff

Use `research` and `design` in their non-implementing roles. Supply the accepted
problem, evidence and constraints and load their actual required instructions.
These are advisory consultations within the existing outer execution mode,
not standalone planning sessions. Their default user-facing plan delivery is
replaced by an internal content handoff to this skill. Do not request host
Plan Mode, invoke a user-visible plan tool, or return plan text to the user.
This skill alone performs the authorized file save and GitHub decision after
the advisers return. Any host Plan Mode already in force still prevents those
writes; never switch out of it or bypass it. Do not invoke implementation owners.
Missing required skills or material unresolved decisions leave planning pending.

Adapt [the template](../assets/issue-plan.md.template) with:

- Repository identity, stable batch anchor/topic, every accepted member identity
  and permitted issue URL, owning/affected projects, source revision, review
  date and evidence limitations. Separate related nonaccepted issues from members.
- Shared remediation boundary and why the members belong together; per-member
  problems, supported behavior, verified facts, disputed claims and confidence.
- Acceptance and priority rationale, scope, non-goals and resolved decisions.
- Chosen design, alternatives, affected interfaces and failure behavior.
- Ordered implementation work with concrete modules/paths and dependencies.
- Shared test scenarios/commands plus acceptance criteria for every member,
  dependencies and unrun validation boundaries. Record which sibling discussions,
  source state and other material evidence support each member's acceptance so
  publication can refresh those dependencies, not just the target issue.
- Rollout/recovery risks and documentation/canonical-spec handoffs for the
  implementing maintainer. Do not claim tests or implementation already passed.

Do not copy confidential issue data, secrets, customer details, private URLs,
internal hostnames or exploit instructions into a repository artifact. Reference
sensitive evidence only through a permitted, sanitized identifier; if removing
it would make the plan unusable, stop and ask for the appropriate private route.
The ordinary plan artifact must be safe for the repository's publication scope.

## Preserve existing work

Read the entire existing plan before changing it. Its batch/member identities
and content must still match. Do not treat a generated heading or marker as proof that the
file has no maintainer edits. Leave human-written content intact. Update only
content whose ownership and intended change are clear from current evidence;
if that cannot be established, ask for direction before replacing anything.
Do not create a second plan to evade this conflict.

Capture the prior content identity and reread immediately before replacement.
If it changed, stop and reconcile. Use a safe file editor and compare the diff;
do not overwrite a concurrently changed file. Remove no existing plan and do
not rename it merely because the issue title changed.

## Verify before acceptance

Read the saved file back from its resolved project path. Check full
repository/project/member identities, shared-remediation rationale, each member's acceptance criteria,
filename, required content, absence of template placeholders,
consistency with the latest evidence and preservation of existing edits.
After the completeness check, set the planning status to complete, then reread
the final bytes. Do not announce acceptance until that complete artifact has
been saved and verified.

If interrupted with a partial artifact, retain it as incomplete; do not describe
it as ready. If saving is disallowed or unavailable, report the missing file
operation, never paste the plan into chat or GitHub. After a successful save,
report only the path and status in chat. GitHub receives a concise acceptance
decision, not plan text, absolute local paths or a fabricated hosted-file link.
A repository-relative plan path may be named explicitly as saved locally; follow
[comment guidance](comments.md).

Track acceptance publication independently for every member. A verified plan
can cover several issues even if one comment fails. Preserve the file and
verified comments; retry only the unresolved authorized effect after readback.
Before each acceptance, refresh its target and recorded supporting evidence,
including siblings and relevant source state. If independence is not established,
refresh every accepted member. Unavailable material evidence keeps dependent
acceptance pending. Material drift in a member invalidates its dependent plan
coverage; revise and reverify before acceptance, while independent batches can
continue. Never defer a required sibling read until that sibling's own turn.
