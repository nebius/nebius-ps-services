---
name: publish-helm
description: Generate a Nebius OCI Helm chart publication flow by creating a chart-local CHANGELOG.md and publish-helm.sh, then registering the chart in the shared .github/workflows/helm-chart-publish.yml workflow with tag-driven releases and public pull verification.
---

# Publish Helm

Create a repeatable Helm chart publication setup for charts released to Nebius
Container Registry as OCI artifacts.

## Use This Skill For

- Adding a release process to a new Helm chart.
- Standardizing Helm chart publication across chart directories in this repo.
- Enforcing a two-step release flow:
  - `--prep` on branch
  - merge the prep branch to `main`
  - `--publish` on clean synced `main`

## Output Contract

Generate or update exactly these artifacts for the target chart:

1. `<chart_dir>/CHANGELOG.md`
2. `<chart_dir>/publish-helm.sh`
3. `.github/helm-chart-publish.json`
4. `.github/workflows/helm-chart-publish.yml`

## Inputs to Collect

- `project_tag_prefix` (for example `nccl-test-chart`)
- `main_branch` (default `main`)
- `chart_dir` (for example `helm-charts/nccl-test`)
- `chart_name` (the name from `Chart.yaml`)
- `publish_environment` (default `nb-image-chart-publish`)

The templates assume the GitHub Actions environment exposes these Nebius
variables and secret:

- Variables:
  `NB_REGION_ID`, `NB_REGISTRY_ID`, `NB_PROJECT_ID`,
  `NB_SERVICE_ACCOUNT_ID`, `NB_SERVICE_ACCOUNT_PUBLIC_KEY_ID`
- Optional variables:
  `NB_TENANT_ID`, `NB_REGISTRY_NAME`
- Secret:
  `NB_SERVICE_ACCOUNT_PRIVATE_KEY`

## Workflow

1. Copy chart-local templates from `assets/` into the target chart paths.
2. Replace placeholders:
   - `__PROJECT_TAG_PREFIX__`
   - `__MAIN_BRANCH__`
   - `__CHART_DIR__`
   - `__CHART_NAME__`
3. Register the chart tag pattern, chart metadata, and optional extra render
   smoke arguments in `.github/helm-chart-publish.json`.
4. Keep the shared
   `.github/workflows/helm-chart-publish.yml` workflow.
5. Keep `publish-helm.sh` executable.
6. Validate:
   - `bash -n <chart_dir>/publish-helm.sh`
   - JSON parse for `.github/helm-chart-publish.json`
   - YAML parse for `.github/workflows/helm-chart-publish.yml`
   - `helm lint <chart_dir>`
   - `helm template smoke <chart_dir> --namespace <chart_name> >/dev/null`
7. Document runtime usage in the chart README:
   - `./publish-helm.sh --prep X.Y.Z`
   - `./publish-helm.sh --publish X.Y.Z`
   - note that the prep step updates both the chart-local changelog and
     `Chart.yaml`
   - note that the publish step only tags; CI does the OCI package/push work

## Guardrails

- Keep one canonical release path. Do not add a chart-specific publish workflow
  beside `publish-helm.sh` plus the shared tag-driven workflow.
- `--prep` should start from a strictly clean worktree, including untracked
  files, so the release-prep commit stays isolated.
- `--prep` should fail before editing files if the target tag already exists
  locally or on `origin`.
- `--prep` should update the chart-local `CHANGELOG.md` and `Chart.yaml`
  together, then validate the chart before committing.
- `--publish` only creates and pushes the tag; no content edits.
- `--publish` must fail if `Chart.yaml` does not already declare version
  `X.Y.Z`, or if the target release section is missing or empty.
- The workflow should publish from the pushed tag only. Do not keep a separate
  workflow-dispatch release version override unless the user explicitly asks
  for one.
- The workflow should verify the published chart is anonymously pullable when
  the target registry is intended to be public.
- Artifact names and step summaries should include `chart_name` to avoid
  ambiguous publish output across monorepos.

## Resources

- `assets/CHANGELOG.md.template`
- `assets/publish-helm.sh.template`
