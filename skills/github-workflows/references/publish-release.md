# Publish Release Workflows

Use this reference for tag-driven GitHub Releases that publish built artifacts.

## Required invariants

- Trigger only from tags matching `<project>-vMAJOR.MINOR.PATCH`.
- Check out full git history.
- Resolve the tagged commit explicitly with `git rev-list -n 1 <tag>`.
- Verify that tagged commit belongs to the intended release branch.
- Verify the tagged source checkout resolves the package runtime version to the tag version before project dependencies are installed.
- Rebuild the artifact from the tagged commit.
- Verify the built artifact version matches the tag version.
- Generate release notes from `CHANGELOG.md`.
- Fail if the changelog section for the tag is missing or empty.
- Skip duplicate release creation if the GitHub Release already exists.
- Upload a release manifest artifact and publish a short run summary.

## Local helper alignment

The source-owned publish-release skill uses one invocation with resumable owner
handoffs. Keep newly generated helpers aligned with that contract:

- Content preparation updates only the selected changelog on the already
  selected feature branch; it preserves existing dirty work and the index.
- create-pr owns complete reviewed repository staging, commits, synchronization,
  branch pushes and PR creation/reuse. Do not add raw helper commits.
- The exact verified merged commit must belong to remote default-branch history;
  it need not remain the newest default-branch commit. Verify in an isolated clone.
- Create the local annotated tag, check SCM runtime version, checkpoint the exact
  object, then push that object. Never move or overwrite an existing remote tag.
- Missing/empty release notes and conflicting tags fail before publication.
- Resume rechecks exact PR/tag/workflow identities and verifies downloaded assets.
  Existing release existence alone is not evidence that its assets are complete.
- Approval gates remain on GitHub, with visible 600-second waits and resumable
  timeouts. No admin bypass or implicit rerun/re-upload.

Existing project helper examples below have their own interfaces; migrating them
requires explicit scope. Do not silently rewrite project copies during skill work.

## Repo examples

- `.github/workflows/nebius-cxcli-release.yml`
- `.github/workflows/vpngw-release.yml`
- `services/nebius-cxcli/publish-release.sh`
- `services/vpngw/publish-release.sh`
