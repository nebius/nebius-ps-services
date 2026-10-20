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
- Before creating a release, upload immutable `publication-release/v1` evidence
  with tag object/commit, workflow run/attempt and each asset basename, size and
  SHA-256. Existing releases must match downloaded bytes against that original
  evidence; missing, expired, partial or conflicting evidence blocks recovery.
- A complete authenticated release listing establishes absence. API failures do
  not. Rebuilt assets must match any retained pre-publication manifest before
  a retry can create a release. Never overwrite assets or backfill expected
  hashes from remote bytes.
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
- Approval gates remain on GitHub, with visible 10800-second phase budgets and resumable
  timeouts. No admin bypass or implicit rerun/re-upload.

Existing project helper examples below have their own interfaces; migrating them
requires explicit scope. Do not silently rewrite project copies during skill work.

## Repo examples

- `.github/workflows/nebius-cxcli-release.yml`
- `.github/workflows/vpngw-release.yml`
- `services/nebius-cxcli/publish-release.sh`
- `services/vpngw/publish-release.sh`

## Shared implementation

`github-workflows` is the sole owner of the release YAML template. Copy its
`scripts/publication.py` to `.github/scripts/publication.py` when adopting the
standalone template and replace `__WORKFLOW_FILE__` with the selected filename.
Keep project-specific tests, package checks and asset sets in their workflows.
The helper validates metadata and observes remote state; it cannot create a
release or push a registry tag. Preserve existing publisher workflow filenames
because local release observers use them to identify the exact tag run.

Retain original evidence for the configured artifact lifetime (90 days here).
Older releases without sufficient evidence require maintainer reconciliation;
a green existence-only run is not a successful verification.
