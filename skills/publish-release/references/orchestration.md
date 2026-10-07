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
   delegated release task covers its necessary validated repairs. Close the PR
   continuation grant when that owner is complete; human review is separate.
3. Bind the final pushed head: `release_session.py bind-pr --project-dir <project>
   --tag <tag> --pr <number>`. This verifies local branch, remote PR head, same
   repository and base. Never freeze an earlier head before create-pr repairs.

Do not append a raw changelog commit after a separate commit-push task. The shell
helper intentionally no longer offers commit/push preparation or `--no-push`.
Normal partial prep mode also uses these owners and stops at the prepared PR.

## Approval and merge

Use `release_session.py wait --project-dir <project> --tag <tag> --phase pr`.
The helper emits approval links and minute progress while polling every 15 seconds.
Run it with a yielding tool session so progress remains visible. Alternatively
call `observe` repeatedly, respecting returned `poll_seconds`; an observation
persists the same deadlines. Never use `resume` inside a polling loop.

A ready PR observation is a handoff to merge-pr, not a replacement for its required
checks/review/mergeability queries. Use the guarded merge and preserve queue rules.
For confirmed queue admission, observe `--phase merge` until the PR actually merges;
do not re-enqueue. A removed queue entry or declined merge must be reported through
merge-pr rather than blindly waiting or attempting another merge.

Observe the merge with `--phase merge` to freeze its resulting SHA. Have merge-pr
verify that result's destination and applicable CI. Squash/rebase results can differ
from the original head; never substitute feature-head ancestry for result proof.
The existing merge-pr observer owns exact-result CI and its 3600-second deadline.

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
pending environment approvals with one fixed 600-second budget. Workflow failure
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
