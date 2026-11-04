# Bound PR intake and publication

Use the trusted installed commit transaction, including its sibling
`pr_publication.py`, for ordinary PR publication. Resolve and pin that installation
before contributor checkout; never run a candidate copy with operator credentials.
This is a private owner contract, with no new public flags or merge authority.

For an existing PR, pass private `begin --pr-bindings <private-json>` alongside
the usual original receipt, target list and base. The JSON maps full local refs
to selectors, for example:

```json
{"refs/heads/pr/101/7/review01": {"repository": "owner/repository", "number": 7}}
```

Same-repository targets use their actual head branch. Fork targets require an
unused `pr/<base-repository-id>/<pr-number>/<operation-id>` local alias; same-named local or
origin branches are not the contributor branch. Use a fresh 8–64 character alphanumeric, underscore or hyphen operation ID,
so a later review preserves the earlier local branch. Resolve the numeric base ID
from GitHub before selecting the alias. Intake reads the PR, independently
binds base/head names, numeric IDs and refs, freezes effective fetch/push URLs,
and fetches only the exact head object. Origin remains the base repository.
Metadata-only review needs no checkout. Use private `checkout-target` for local
validation or repair. Missing/deleted forks, collisions and identity drift stop.

After the owner has committed and synchronized the clean target and the root
has reviewed the complete final diff, invoke:

```text
python3 <commit>/scripts/commit_transaction.py publish-target \
  <original-private-task-arguments> --branch <local-target> \
  --reviewed-head <full-head-sha> --reviewed-base <full-base-sha>
```

The helper checks the target checkpoint, base ancestry/current base, destination
and PR identity, and fresh direct write permission or maintainer-edit permission.
It journals the intended transition before one explicit non-force branch push.
A trusted pre-push guard compares the server-advertised old head with the frozen
expected head. It replaces candidate push hooks only; normal trusted commit
hooks remain part of commit validation. No force, tags, submodule pushes,
replacement PR or credential substitution is permitted. New ordinary PR branches
use the same operation without a PR binding, with an initially absent remote head.

Read back both the exact remote branch and, for bound PRs, GitHub PR head. A lost
acknowledgement is not permission to blindly push again. Resume the same operation:
an intended head already present settles without another push; a proven old head
may retry only after prior Git children are quiescent under the repository lock.
A trusted pre-submission rejection witness clears pending state while preserving
the rejected attempt, allowing cancellation and fresh intake. Proven publication
followed by contributor advancement also clears pending state but invalidates
readiness; cancel and review fresh intake without adopting unreviewed history.
Unknown or divergent effects retain the journal and block further repairs and
closure until reconciled. Do not delete private state or mint a new grant to
bypass that condition. Permission denial returns a blocker and the local patch.

Every later repair invalidates review and publication evidence. Re-review,
publish and refresh CI on the new head before readiness. Completion records the
base repository's authoritative merged result even for forks; branch deletion
remains separately authorized and must never target a same-named base branch.

Task, checkpoint and closure schemas are v3. Drain old transactions with their
old trusted owner before installing this version. Do not translate grants or
reopen closures. Coordinate this cutover with merge-pr's review/intent v2 rollout.
