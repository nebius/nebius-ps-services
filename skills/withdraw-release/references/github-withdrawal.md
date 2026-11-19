# GitHub withdrawal protocol

Use authenticated `gh api` for exact release reads, metadata updates and asset
removal. The GitHub connector may be used for independent reads, but connectors
without release-edit and asset-delete operations cannot execute this workflow.
Never display CLI credentials or raw API failures.

GitHub locks immutable assets and tags while allowing title, notes, prerelease
and latest changes. Thus immutable withdrawal must remain visible and honest:
mark WITHDRAWN, say not to install, and state that protected downloads remain.
Do not delete the release to evade protection.

## Marker and publication boundary

The notice begins with `<!-- withdraw-release:v1 -->` and ends with
`<!-- /withdraw-release:v1 -->`. It precedes the byte-preserved original notes.
Recognized malformed or unknown-version `<!-- withdraw-release:` markers fail
closed. Both repository-owned publication consumers reject this marker family,
including in-progress withdrawals and immutable releases with intact assets.
The helper uses a single title prefix, `WITHDRAWN —` followed by a space.

Original tagged workflows may predate this marker check. Their existing asset
integrity checks can reject removed assets; they cannot all be retroactively
upgraded. Independent uploaders are not locked by local checkpoints. Stop
competing publication before retrying observed inventory drift.

## Evidence and limitations

Freeze numeric repository/release/asset identities, tag object/peeled commit and
asset metadata, including digest when GitHub supplies one. Do not download large
assets merely to establish a new digest. A private checkpoint holds no original
note bodies or withdrawal reason, only hashes and progress.

Recheck observed state before and after effects. GitHub has no transaction that
atomically edits a release and deletes its assets, so checks cannot eliminate
all cross-client races. Never claim universal prevention of republication.
API calls time out after 45 seconds; listings stop at 100 pages. Final checks
make at most four cache-busted attempts with two-second pauses. Private asset
absence requires readable authenticated repository/release/inventory plus
per-asset absence. Public assets also require unavailable original download
endpoints. Anonymous HEAD requests carry no authentication.

A published release becomes prerelease and receives `make_latest=false`.
Verification requires latest to differ or be absent, without choosing a
replacement across unrelated monorepo products. A repeat verifies live state;
private progress alone never proves completion. Keep an outstanding deletion
identity until that exact asset is reconciled. Process it before any other
deletion, regardless of persisted asset ordering; a timed-out request can
complete during a later invocation.

Short-version resolution accepts only a single literal APP_DIR/TAG_PREFIX pair
for the selected project. Duplicate environment blocks or fields and malformed
quoting require an exact tag; never choose the first conflicting declaration.

## Official sources

Verified 2026-11-18:

- [Immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)
- [Update a release and latest selection](https://docs.github.com/en/rest/releases/releases#update-a-release)
- [Delete an uploaded asset](https://docs.github.com/en/rest/releases/assets#delete-a-release-asset)
- [GitHub CLI API requests and JSON input](https://cli.github.com/manual/gh_api)

## Validation

Run `python3 -B -m unittest discover -s scripts -p 'test_*.py'` from this skill.
Tests use synthetic GitHub/state fixtures; they cannot certify live provider
permissions, immutable mutation acceptance or native agent discovery. Runtime
help and read-only dry run may use real targets. Live destructive qualification
requires an explicitly designated disposable release.
