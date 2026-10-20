# Container Image Publish Workflows

Use this reference for workflows that build and push OCI images.

## Trigger pattern

Recommended pattern in this repo:

- `push` on component release tags for SemVer image tags
- `workflow_dispatch` for controlled manual SHA-tag publishes and optional main-only `latest`

## Required controls

- Use component-specific release tag prefixes in monorepos.
- Validate `workflow_dispatch` inputs before any privileged step.
- Restrict manual publish workflows to admins or another explicit actor policy.
- Resolve registry credentials from secrets and optional variables, not hardcoded strings.
- Tag-triggered releases own `X.Y.Z` and `X.Y.Z-g<shortsha>`. Manual runs
  own `sha-<shortsha>` and optional main-only `latest`. These lanes must not
  share an immutable alias, because version labels and mutable inputs can
  produce different digests for the same source commit.
- Record the pushed digest in a manifest and step summary.

## Build rules

- Use Buildx with GitHub Actions cache.
- Keep `provenance` setting explicit.
- Use registry login actions rather than shelling out to `docker login` unless there is a strong reason.
- Keep the Docker build context and Dockerfile path explicit.

## Repo example

- [NCCL image publication](../../../.github/workflows/nccl-test-image-publish.yml)

## Current action majors used in the skill asset

As of May 13, 2026:

- `actions/github-script@v9`
- `docker/setup-buildx-action@v4`
- `docker/login-action@v4`
- `docker/build-push-action@v7`

## Retry and concurrency contract

Use one destination-image concurrency group across preflight, push, verification
and immutable evidence upload, with `cancel-in-progress: false` and `queue: max`.
GitHub currently permits up to 100 pending runs in that queue; excess runs are
canceled. See [GitHub concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).
Do not scope the lock by source ref or release version.

Inspect every immutable alias before a push. Reuse only a complete alias set
whose digests match a trusted original `publication-image/v1` manifest bound to
repository, source, workflow and run/attempt (plus tag object for releases).
Partial, conflicting or missing evidence blocks automatic repair. Transport and
authentication errors are unverifiable, never absence. After a push, compare
every remote tag digest with the build digest. Existing remote tags remain
untouched by this source change.

Manual publication has no `release_version` input. It uses a helper frozen from
the workflow revision while building the selected main-history source commit.
Copy `scripts/publication.py` to `.github/scripts/publication.py` when adopting
the standalone template. Keep image design, platforms and attestation choices
with the container owner.

Image evidence is uploaded after push and digest verification. If publication
succeeds but that upload is interrupted, the aliases are unverifiable and the
next run blocks for maintainer reconciliation. It never rebuilds over them.

An explicit main-only manual request for `latest` may reuse an already verified
manual digest. It copies that digest to the mutable alias under the same lock
and verifies equality afterward; immutable aliases are never rebuilt or changed.
