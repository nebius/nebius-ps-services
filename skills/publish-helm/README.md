# Publish Helm

`publish-helm` publishes Helm charts to OCI registries end to end from the
current project folder. It can still set up chart release assets, but its
primary job is to execute a release and return a completion report.

## What It Does

- Collects chart, explicit tag, branch, and publish/verify destination inputs.
- Runs chart release prep only from a clean synced default branch, creating and
  pushing a `release/<tag>` branch that includes the `Chart.yaml` version bump
  the tag workflow will package.
- Validates dependencies, strict lint, and template smoke rendering.
- Uses `create-pr` and `merge-pr` for the release-prep PR path.
- Tags only from a clean synced default branch.
- Waits for the tag-triggered chart workflow when requested.
- Verifies the published OCI chart with `helm pull`.

## Architecture

```text
Helm chart
  |
  +--> setup assets when missing or requested
  +--> skill-owned publish-helm-doer.sh
  +--> create-pr -> merge-pr
  `--> tag-triggered chart workflow
        |
        v
Published OCI chart
```

## Workflow

1. Resolve release inputs and normalize the release tag.
2. Run setup mode only when requested or required assets are missing.
3. Prep from the clean synced default branch; the helper creates and pushes
   `release/<tag>`.
4. Create and merge the release-prep PR in complete mode.
5. Publish the tag from the default branch.
6. Wait for the workflow and verify the OCI chart pull.
7. Return the final report.

## Core Concepts

- Doer mode does not depend on a project-local `publish-helm.sh`.
- The default branch is the release source of truth. If work is still on a
  feature branch, merge that branch to the default branch before prep or
  publish.
- Release version/tag is required for release execution. The skill should ask
  for it when missing instead of inferring it from chart files, Git history, or
  changelog text.
- `--oci-repository` is the OCI repository base; it must not include chart name
  or version.
- Helm CLI publishing and local verification use an OCI repository base, while
  some project workflows derive the upload target from provider-specific
  variables such as region and registry ID. The skill should inspect the
  workflow contract and ask for missing destination inputs.
- `Chart.yaml` changes happen in prep and must be merged before publish; the
  publish/tag phase fails fast if the chart version does not match the tag.
- Registry credentials live in GitHub secrets, local environment, or Helm
  registry login state, not in skill sources.
- Concrete registry URLs, registry IDs, project IDs, endpoints, and secrets do
  not belong in public reusable skill sources; use placeholders or variable
  names.
- Human-required approvals and failing checks are blockers.

## Files

- `SKILL.md`: Runtime workflow, inputs, guardrails, and output contract.
- `scripts/publish-helm-doer.sh`: Local prep/publish/verify primitives.
- `assets/`: Optional setup templates for changelog and helper.
- `agents/openai.yaml`: UI metadata.
