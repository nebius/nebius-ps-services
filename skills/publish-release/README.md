# Publish Release

`publish-release` publishes application/package artifacts to GitHub Releases
end to end from the current project folder. It can still set up release assets,
but its primary job is to execute a release and return a completion report.

## What It Does

- Collects or derives project, package, tag, branch, and workflow inputs.
- Runs release prep on the current feature branch.
- Uses `create-pr` and `merge-pr` for the release-prep PR path.
- Tags from a clean synced default branch.
- Verifies package runtime version before tag push when configured.
- Waits for the tag-triggered GitHub Release workflow when requested.
- Verifies the GitHub Release and expected assets.

## Architecture

```text
Application project
  |
  +--> setup assets when missing or requested
  +--> skill-owned publish-release-doer.sh
  +--> create-pr -> merge-pr
  `--> tag-triggered release workflow
        |
        v
GitHub Release with assets
```

## Workflow

1. Resolve release inputs and normalize the release tag.
2. Run setup mode only when requested or required assets are missing.
3. Prep the release branch with the skill-owned helper.
4. Create and merge the release-prep PR in complete mode.
5. Publish the tag from the default branch.
6. Wait for the workflow and verify the GitHub Release assets.
7. Return the final report.

## Core Concepts

- Doer mode does not depend on a project-local `publish-release.sh`.
- Package import name and asset glob are inputs, not hardcoded skill knowledge.
- Secret values stay in GitHub secrets or local auth state, not in skill
  sources.
- Human-required approvals and failing checks are blockers.

## Files

- `SKILL.md`: Runtime workflow, inputs, guardrails, and output contract.
- `scripts/publish-release-doer.sh`: Local prep/publish/verify primitives.
- `assets/`: Optional setup templates for changelog, helper, and workflow.
- `agents/openai.yaml`: UI metadata.
