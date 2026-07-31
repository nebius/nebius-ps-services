# Publish Image

`publish-image` publishes container images end to end from the current project
folder. It can still set up release assets, but its primary job is to execute a
release and return a completion report.

## What It Does

- Collects or derives project, tag, image, branch, and workflow inputs.
- Runs release prep only from a clean synced default branch, creating and
  pushing a `release/<tag>` branch from it.
- Uses `create-pr` and `merge-pr` for the release-prep PR path.
- Tags only from a clean synced default branch.
- Waits for the tag-triggered image workflow when requested.
- Verifies pushed image tags and reports digest evidence.
- Consumes the approved `container` build/platform/supply-chain contract and
  the `github-workflows` publication workflow rather than redefining them.

## Architecture

```text
Current project
  |
  +--> setup assets when missing or requested
  +--> skill-owned publish-image-doer.sh
  +--> create-pr -> merge-pr
  `--> tag-triggered image workflow
        |
        v
Published image tags and digest
```

## Workflow

1. Resolve release inputs and normalize the release tag.
2. Run setup mode only when requested or required assets are missing.
3. Prep from the clean synced default branch; the helper creates and pushes
   `release/<tag>`.
4. Create and merge the release-prep PR in complete mode.
5. Publish the tag from the default branch.
6. Wait for the workflow and verify the image tag/digest.
7. Return the final report.

## Core Concepts

- Doer mode does not depend on a project-local `publish-image.sh`, but the
  setup template is a maintained runnable helper and should keep the same
  `--mode prep|publish|verify` contract as the skill-owned doer.
- The default branch is the release source of truth. If work is still on a
  feature branch, merge that branch to the default branch before prep or
  publish.
- Registry locations are inputs, not hardcoded skill knowledge.
- Secret values stay in GitHub secrets, local environment, or the registry
  login mechanism; skill sources store only secret or variable names.
- Human-required approvals and failing checks are blockers.
- `publish-image` owns release tags, pushes, signing actions, waits, and
  published digest evidence; `container` owns image and runtime design.

## Files

- `SKILL.md`: Runtime workflow, inputs, guardrails, and output contract.
- `scripts/publish-image-doer.sh`: Local prep/publish/verify primitives.
- `assets/`: Optional setup templates for changelog and the release helper.
- `github-workflows`: Canonical owner of image-publish workflow YAML and its
  reusable template.
- `agents/openai.yaml`: UI metadata.
