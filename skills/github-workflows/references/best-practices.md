# GitHub Actions Best Practices

Use these defaults unless the repository already has a stronger constraint.

## Repo patterns in this monorepo

- PR and merge CI:
  - `.github/workflows/nebius-cxcli-ci.yml`
  - `.github/workflows/vpngw-ci.yml`
- Tag-driven release publish:
  - `.github/workflows/nebius-cxcli-release.yml`
  - `.github/workflows/vpngw-release.yml`
- Container image publish:
  - `.github/workflows/mysterybox-bridge-image.yml`
- Bot-only merge automation:
  - `.github/workflows/dependabot-auto-merge.yml`

## Baseline rules

- Use project-prefixed workflow and job names in monorepos.
- Use `permissions` explicitly and start from least privilege.
- Add `concurrency` for cancelable CI flows.
- Use `paths` filters so unrelated services do not trigger the workflow.
- Include related workflow files in `paths` so workflow changes test themselves.
- Set `defaults.run.working-directory` for service-local commands.
- Use `set -euo pipefail` in shell blocks.
- Prefer `$GITHUB_OUTPUT` over deprecated output syntaxes.
- Emit a short step summary for publish workflows.

## Fetch depth

- Use `fetch-depth: 0` whenever the workflow needs tags, commit ancestry, or version metadata.
- Shallow checkouts are fine only for workflows that do not inspect git history.

## Action majors preferred in this repo pattern

As of March 10, 2026:

- `actions/checkout@v6`
- `actions/setup-python@v6`
- `actions/upload-artifact@v7`
- `actions/github-script@v8`
- `docker/setup-buildx-action@v4`
- `docker/login-action@v4`
- `docker/build-push-action@v7`

Node 24 based actions require GitHub runner `v2.327.1+` on self-hosted runners. GitHub-hosted runners already satisfy this.

## Validation

- Parse workflow YAML locally after edits.
- If the workflow drives a build or release, run the same lint/test/build steps locally when practical.
- For scripts paired with workflows, run `bash -n` before shipping changes.
