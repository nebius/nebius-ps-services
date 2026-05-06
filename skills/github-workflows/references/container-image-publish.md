# Container Image Publish Workflows

Use this reference for workflows that build and push OCI images.

## Trigger pattern

Recommended pattern in this repo:

- `push` on the main branch for `latest` and commit-sha image tags
- `push` on release tags for SemVer image tags
- `workflow_dispatch` for controlled manual publishes

## Required controls

- Scope monorepo triggers with `paths`.
- Validate `workflow_dispatch` inputs before any privileged step.
- Restrict manual publish workflows to admins or another explicit actor policy.
- Resolve registry credentials from secrets and optional variables, not hardcoded strings.
- Publish immutable tags such as `sha-<shortsha>` and `X.Y.Z-g<shortsha>`.
- Record the pushed digest in a manifest and step summary.

## Build rules

- Use Buildx with GitHub Actions cache.
- Keep `provenance` setting explicit.
- Use registry login actions rather than shelling out to `docker login` unless there is a strong reason.
- Keep the Docker build context and Dockerfile path explicit.

## Repo example

- `.github/workflows/sample-service-image.yml`

## Current action majors used in the skill asset

As of March 10, 2026:

- `actions/github-script@v8`
- `docker/setup-buildx-action@v4`
- `docker/login-action@v4`
- `docker/build-push-action@v7`
