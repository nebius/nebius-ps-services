# Release orchestration

These are private helper calls, not additional public skill flags. Resolve paths
from this skill's installation. The agent orchestrates existing owners; the
checkpoint helper does not create grants, commit, merge or approve anything.
Use the shell helper from its canonical skill location; the generated project
helper has the same primitive contract and project-specific defaults.

## Intake and preparation

1. Resolve the live default with `git ls-remote --symref origin HEAD`, effective
   fetch/push destinations and the existing workflow's tag prefix. Respect the
   Worktree publication guard and active workflow owners through create-pr.
2. Infer the next version only from explicit intent or unambiguous prepared
   metadata, not the latest published version. Ask once when missing.
3. Resolve the existing checkpoint first. For a new release, use the canonical
   commit helper's `begin --requested-action create-pr` with the release root
   prompt receipt before switching/creating branches. Freeze the selected
   feature or planned `release/<tag>` branch and base using create-pr's protocol.
4. Open the release checkpoint with normalized tag, selected branch/base,
   exact workflow filename and one `--asset` basename pattern per required
   artifact family. Missing required families must not be hidden by one broad
   wildcard. A source checkout permits one unfinished preparation at a time.

```text
python3 <skill>/scripts/release_session.py open --project-dir <project> \
  --tag <prefix>-vX.Y.Z --branch <feature> --base <default> \
  --workflow <release.yml> --asset '*.whl'
```

1. Select/create the frozen branch through create-pr's owner flow. Generate
   missing setup assets and prepare non-empty Unreleased notes from the reviewed
   changes when necessary. Update explicit package-version metadata if required.
   Call `release_session.py prepare --project-dir <project> --tag <tag>` for
   serialized changelog-only preparation. It preserves the index and existing
   dirty work. Direct shell prep requires an already-selected non-default branch.
2. Complete create-pr's review, hygiene/tests, canonical complete-tree commit,
   owner-controlled base synchronization, push and PR creation/reuse. This
   delegated release task covers its necessary validated repairs. Use create-pr's
   private preparation handoff; keep the grant open across local review-pr
   safe repairs. Revalidate and re-review until the final head passes.
3. Bind the final pushed head: `release_session.py bind-pr --project-dir <project>
   --tag <tag> --pr <number>`. This verifies local branch, remote PR head, same
   repository and base. Never freeze an earlier head before local review-pr and create-pr repairs.
   Head drift after binding blocks until explicitly reconciled through the guarded
   transition below; ordinary bind-pr never replaces the frozen head.

Do not append a raw changelog commit after a separate commit-push task. The shell
helper intentionally no longer offers commit/push preparation or `--no-push`.
Normal partial prep mode also uses these owners and stops at the prepared PR.

## Approval and merge

Invoke merge-pr with the exact local review attestation before waiting for PR
approval. The built-in Actions identity supplies routine approval through the protected broker; there
is no human-approval wait ahead of dispatch. Missing setup, unsafe findings and
unresolved human objections stop. The local agent remains the review/fix owner.

Use the completion reference to observe actual merge and the explicit result
CI/Pages evidence. Required merge queues and token-denied workflow-file merges
stop for a maintainer; never enqueue or change credentials as a fallback. `release_session.py observe --phase pr` remains
a read-only readiness view, not the ordinary flow's pre-dispatch approval gate.

Observe the merge with `--phase merge` to freeze its resulting SHA. Have merge-pr
verify that result's destination and applicable CI. Squash/rebase results can differ
from the original head; never substitute feature-head ancestry for result proof.
An open PR with no queue entry remains pending direct async settlement, not removed
from a queue. The checkpoint records an observed external entry before treating its
later disappearance as removal. Queue observation is read-only and never re-enqueues.
The existing merge-pr observer owns exact-result CI and its 3600-second deadline.

## Reviewed head reconciliation

If the base advances after binding or a safe branch repair is needed, reconcile
any earlier broker result first. If already merged, retain the frozen head and
verify that operation. Otherwise use create-pr's canonical preparation handoff,
validate, push and run a fresh full review-pr attestation. Preserve unrelated work;
do not interpret remote head movement alone as an authorized repair.

Before the next broker dispatch, use the publication owner's private transition:

```text
python3 <skill>/scripts/release_session.py reconcile-pr --project-dir <project> \
  --tag <tag> --expected-head <previously-bound-sha> --head <reviewed-new-sha> \
  --review-id <fresh-comment-review-id>
```

This requires a clean active checkout at the exact reviewed descendant, the same
open PR/branch/repository/default, current base inclusion, no merge/tag/publication
evidence and no remote tag. The installed merge-pr sibling verifies the actual
COMMENT review against live `MERGE_OPERATOR_IDS` and the current head/base. Missing
review or variable-read access blocks; never substitute credentials or invent a
review. The transition records prior/new head and review identities while keeping
wait deadlines. It provides no commit, approval or merge authorization; the broker
independently repeats all admission gates. After an interrupted reconciliation,
read status first and reuse the exact recorded transition if already applied.

Unexpected head movement, rewritten history, dirty work, stale review or any
post-merge/tag change cannot use this transition. Explicit resume still only
renews wait budgets. Never edit checkpoints manually or recreate the release to
bypass a rejected reconciliation.

## Isolated tag publication

Create a task-owned private temporary clone with full history from the frozen
origin, and detach at the verified merged SHA. Use the effective origin fetch
and push destination already verified at intake; reject multiple/different push
URLs. Confirm identity/auth/signing prerequisites before creating the tag, without
printing tokens or copying secret-bearing configuration. Reuse the host's existing
credential/signing stores. Copy only required non-secret identity/config values.

Use the selected project path inside that clone, not the source project's cwd:

```text
bash <skill>/scripts/publish-release-doer.sh --mode tag \
  --project-dir <clone-project> --tag <tag> --tag-prefix <prefix> \
  --main-branch <default> --release-commit <merged-sha> \
  --package-import-name <package>
python3 <skill>/scripts/release_session.py record-tag --project-dir <source-project> \
  --tag <tag> --checkout <clone-root>
bash <skill>/scripts/publish-release-doer.sh --mode push \
  --project-dir <clone-project> --tag <tag> --tag-prefix <prefix> \
  --main-branch <default> --release-commit <merged-sha> --tag-object <recorded-oid> \
  --origin-digest <checkpoint-origin-digest>
```

`tag` creates an annotated local tag before importing SCM-versioned packages.
`record-tag` saves the exact public Git tag object metadata so a lost temporary
clone is recoverable without minting a timestamp-different replacement tag.
`push` rechecks local object, peeled commit, remote default ancestry and any
existing remote tag. It pushes only `object:refs/tags/tag`, with tag following and submodule pushes
disabled, never branches.

If interrupted before record-tag, inspect the surviving clone and repeat runtime
verification before recording it; do not assume local tag existence proves it.
If interrupted after record-tag, inspect remote object/peeled SHA first. An exact
remote match continues to workflow observation. If absent and the clone was lost,
recreate a clean isolated clone at the same merged SHA and call `restore-tag` with
source project/tag and `--checkout <clone-root>`. It verifies the saved object's
hash and creates only that exact local ref. Repeat runtime verification before
pushing. Conflicting remote/local tags block; no force, deletion or replacement.

## Workflow and assets

Observe `--phase release`. The helper identifies the exact push workflow by
workflow filename, tag and merged SHA, and freezes its run ID. It handles a run's
pending environment approvals within the release phase's fixed 3600-second budget.
There is no separate approval timer. Gate changes and partial approvals do not
renew that deadline. Workflow failure
or missing expected release assets is a blocker; no implicit rerun/re-upload.

When ready, download every expected asset from the frozen repository/tag into a
private task-owned directory using `gh release download --repo <host/owner/repo>`.
Call `release_session.py complete --project-dir <project> --tag <tag>
--checkout <download-directory>`. It re-observes GitHub and verifies actual
artifact sizes, available SHA-256 digests and all wheel METADATA versions. Other
artifact types require digest evidence; retain project-owned version checks too.
An asset without a digest must not silently skip a required integrity check.
Only complete emits `published` and marks the checkpoint complete.

## Resume and failure

`resume --project-dir <project> [--tag <normalized-tag>]` selects one pending
project release and opens a new wait attempt. `status` reads without extending
budgets. An ordinary repeated invocation uses status/open, preserving deadlines.
`--mode publish` continues the merged checkpoint; if missing, recover the exact
existing release PR/head via open/bind/merge observation before tagging.

Checkpoints bind root/project, effective origin, branch/base, version, PR/head,
merge result, annotated tag object, workflow/run, asset families and deadlines.
Re-read remote evidence before each next effect. New dirty work or an unexpected
PR head/base blocks reconciliation; never absorb it into a frozen release. A new
session uses fresh canonical intake only for required local commit work, never
replays the old native-session grant. Reconcile any uncertain old effect through
its existing owner first. Checkpoint edits do not authorize effects.

Timeout returns a precise resume command and preserves GitHub work. No background
local publisher remains. API errors are unverified evidence, not absent objects,
missing checks or approval. Keep existing public effects intact. Cleanup applies
only to exact task-created temporary paths; checkpoints remain for recovery.

## GitHub behavior

GitHub's [asynchronous merge API](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request-asynchronously)
can accept a direct merge while it settles in the background. An accepted request
does not prove actual merge or queue membership. Additional
[deployment review gates](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/review-deployments)
remain enforced; local waiting policy cannot satisfy them.
