# Publish Release

`publish-release` publishes application/package artifacts to GitHub Releases
end to end from the current project folder. It can still set up release assets,
but its primary job is to execute a release and return a completion report.

## What It Does

- Collects or derives project, package, tag, branch, and workflow inputs.
- Reuses a clean current feature branch for release prep, or creates and pushes
  `release/<tag>` when prep starts from the clean synced default branch.
- Uses `create-pr` and `merge-pr` for the release-prep PR path.
- Tags only from a clean synced default branch.
- Creates the annotated tag locally, verifies the package runtime version
  against it when configured, and pushes only after the version matches.
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
3. Prep on the clean current feature branch, or create `release/<tag>` from the
   clean synced default branch.
4. Create and merge the release-prep PR in complete mode.
5. Publish the tag from the default branch.
6. Wait for the workflow and verify the GitHub Release assets.
7. Return the final report.

## Core Concepts

- Doer mode does not depend on a project-local `publish-release.sh`, but the
  setup template is a maintained runnable helper and should keep the same
  `--mode prep|publish|verify` contract as the skill-owned doer.
- The default branch is the release source of truth for tagging. Prep may reuse
  a clean feature branch that contains current default-branch history; it must
  never create a nested release branch from that feature branch.
- Prep from the default branch creates `release/<tag>` before changing the
  changelog, so release changes still reach the default branch through a PR.
- Package import name and asset glob are inputs, not hardcoded skill knowledge.
- A runtime mismatch removes the exact unpushed local tag; an ambiguous push
  failure retains the local tag for identity inspection before retry.
- Secret values stay in GitHub secrets or local auth state, not in skill
  sources.
- Human-required approvals and failing checks are blockers.

## Files

- `SKILL.md`: Runtime workflow, inputs, guardrails, and output contract.
- `scripts/publish-release-doer.sh`: Local prep/publish/verify primitives.
- `assets/`: Optional setup templates for changelog, helper, and workflow.
- `agents/openai.yaml`: UI metadata.
