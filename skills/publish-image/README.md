# Publish Image

`publish-image` publishes container images end to end from the current project
folder. It can still set up release assets, but its primary job is to execute a
release and return a completion report.

## What It Does

- Collects or derives project, tag, image, branch, and workflow inputs.
- Prepares release content on the current feature branch, creating one through
  create-pr when starting on default. Preparation never commits or pushes.
- Uses `create-pr` and `merge-pr` for the release-prep PR path.
- Tags the exact verified merge result from a clean isolated clone.
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
  +--> create-pr -> local review-pr / safe repairs -> merge-pr Actions broker
  `--> tag-triggered image workflow
        |
        v
Published image tags and digest
```

## Workflow

1. Resolve release inputs and normalize the release tag.
2. Run setup mode only when requested or required assets are missing.
3. Prepare content on the selected feature, then use create-pr for whole-repo
   commits and local review-pr for safe fixes before protected Actions merge.
4. Create and merge the release-prep PR in complete mode.
5. Tag the exact verified merge result in a clean isolated clone, then push
   its recorded annotated object to the frozen origin.
6. Wait for the workflow and verify the image tag/digest.
7. Return the final report.

## Core Concepts

- Doer mode does not depend on a project-local `publish-image.sh`, but the
  setup template is a maintained runnable helper and should keep the same
  `--mode prep|tag|push|verify` contract as the skill-owned doer.
- Prepare content on the existing feature branch. create-pr owns canonical
  commits and local review-pr repairs; merge-pr owns protected Actions merge.
  Publish only the exact verified merge result, even if default advances.
- Registry locations are inputs, not hardcoded skill knowledge.
- Secret values stay in GitHub secrets, local environment, or the registry
  login mechanism; skill sources store only secret or variable names.
- Human-required approvals and failing checks are blockers.
- `publish-image` owns release tags, pushes, signing actions, waits, and
  published digest evidence; `container` owns image and runtime design.

## Files

- `SKILL.md`: Runtime workflow, inputs, guardrails, and output contract.
- `scripts/publish-image-doer.sh`: Content-only prep and exact-result tag/push/verify primitives.
- `assets/`: Optional setup templates for changelog and the release helper.
- `github-workflows`: Canonical owner of image-publish workflow YAML and its
  reusable template.
- `agents/openai.yaml`: UI metadata.

See [ordinary publication](references/ordinary-publication.md) for preparation,
exact-result tagging, protected merge, resume and verification boundaries.
