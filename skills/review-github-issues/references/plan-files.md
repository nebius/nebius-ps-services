# Accepted issue plan files

Read this only for an accepted candidate. The artifact is the implementation
handoff; it is not permission to implement, publish or modify canonical specs.

## Resolve the destination

- Find the owning project root from manifests, instructions, documentation and
  code, including affected shared components. A repository-wide issue can use
  the repository root when it is the clear owner. If several projects are
  equally plausible owners, ask for the owning project and keep one plan that
  describes all affected projects. Do not scatter competing plans.
- Confirm the local project belongs to the selected GitHub repository. For a
  remote-only target, ask for its local checkout/project path before saving;
  do not create the plan in the unrelated current repository or clone silently.
- Use `docs/github-issues/` under that project. Create it if absent only when
  writes are permitted. Ensure all existing path components resolve within
  the project and the destination is a regular file, not a symlink or an
  unrelated object. Stop on unsafe paths or write restrictions.
- New filenames are `<number>-<title-slug>.md`: lowercase the title, replace
  non-ASCII-alphanumeric runs with a single hyphen, strip edge hyphens, and
  limit the slug to 80 characters, stripping a trailing hyphen after truncation.
  Use `issue` when the sanitized slug is empty. The numeric prefix is required.
  Example: `123-fix-connection-timeout.md`.

Before creating a file, inspect existing regular Markdown plans for the full
repository/issue identity. Reuse the matching plan even when its title or
filename differs. Never match on number alone. Duplicate matching files or an
occupied filename for a different identity require clarification, not overwrite.

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

- Repository and issue identity, title, URL where permitted, owning/affected
  projects, reviewed source revision, review date and evidence limitations.
- Problem, supported behavior, verified facts, disputed claims and confidence.
- Acceptance and priority rationale, scope, non-goals and resolved decisions.
- Chosen design, alternatives, affected interfaces and failure behavior.
- Ordered implementation work with concrete modules/paths and dependencies.
- Test scenarios/commands, acceptance criteria and unrun validation boundaries.
- Rollout/recovery risks and documentation/canonical-spec handoffs for the
  implementing maintainer. Do not claim tests or implementation already passed.

Do not copy confidential issue data, secrets, customer details, private URLs,
internal hostnames or exploit instructions into a repository artifact. Reference
sensitive evidence only through a permitted, sanitized identifier; if removing
it would make the plan unusable, stop and ask for the appropriate private route.
The ordinary plan artifact must be safe for the repository's publication scope.

## Preserve existing work

Read the entire existing plan before changing it. Its issue identity and content
must still match. Do not treat a generated heading or marker as proof that the
file has no maintainer edits. Leave human-written content intact. Update only
content whose ownership and intended change are clear from current evidence;
if that cannot be established, ask for direction before replacing anything.
Do not create a second plan to evade this conflict.

Capture the prior content identity and reread immediately before replacement.
If it changed, stop and reconcile. Use a safe file editor and compare the diff;
do not overwrite a concurrently changed file. Remove no existing plan and do
not rename it merely because the issue title changed.

## Verify before acceptance

Read the saved file back from its resolved project path. Check repository/issue
identity, filename, required content, absence of template placeholders,
consistency with the latest evidence and preservation of existing edits.
After the completeness check, set the planning status to complete, then reread
the final bytes. Do not announce acceptance until that complete artifact has
been saved and verified.

If interrupted with a partial artifact, retain it as incomplete; do not describe
it as ready. If saving is disallowed or unavailable, report the missing file
operation, never paste the plan into chat or GitHub. After a successful save,
report only the path and status in chat. GitHub receives a concise acceptance
decision, not plan text, local paths or a fabricated hosted-file link.
