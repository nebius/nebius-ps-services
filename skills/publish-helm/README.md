# Publish Helm

`publish-helm` publishes Helm charts to OCI registries end to end from the
current project folder. It can still set up chart release assets, but its
primary job is to execute a release and return a completion report.

## What It Does

- Collects or derives chart, tag, branch, OCI repository, and workflow inputs.
- Runs chart release prep on the current feature branch.
- Validates dependencies, strict lint, and template smoke rendering.
- Uses `create-pr` and `merge-pr` for the release-prep PR path.
- Tags from a clean synced default branch.
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
3. Prep the release branch with the skill-owned helper.
4. Create and merge the release-prep PR in complete mode.
5. Publish the tag from the default branch.
6. Wait for the workflow and verify the OCI chart pull.
7. Return the final report.

## Core Concepts

- Doer mode does not depend on a project-local `publish-helm.sh`.
- `--oci-repository` is the OCI repository base; it must not include chart name
  or version.
- Registry credentials live in GitHub secrets, local environment, or Helm
  registry login state, not in skill sources.
- Human-required approvals and failing checks are blockers.

## Files

- `SKILL.md`: Runtime workflow, inputs, guardrails, and output contract.
- `scripts/publish-helm-doer.sh`: Local prep/publish/verify primitives.
- `assets/`: Optional setup templates for changelog and helper.
- `agents/openai.yaml`: UI metadata.
