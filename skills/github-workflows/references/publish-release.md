# Publish Release Workflows

Use this reference for tag-driven GitHub Releases that publish built artifacts.

## Required invariants

- Trigger only from tags matching `<project>-vMAJOR.MINOR.PATCH`.
- Check out full git history.
- Resolve the tagged commit explicitly with `git rev-list -n 1 <tag>`.
- Verify that tagged commit belongs to the intended release branch.
- Verify the tagged source checkout resolves the package runtime version to the tag version before project dependencies are installed.
- Rebuild the artifact from the tagged commit.
- Verify the built artifact version matches the tag version.
- Generate release notes from `CHANGELOG.md`.
- Fail if the changelog section for the tag is missing or empty.
- Skip duplicate release creation if the GitHub Release already exists.
- Upload a release manifest artifact and publish a short run summary.

## Local helper alignment

If the project also ships `publish-release.sh`, keep it aligned with the workflow:

- `--prep` should require a strictly clean worktree, including untracked files.
- `--prep` updates only `CHANGELOG.md`, commits it, and should auto-set `origin/<branch>` as upstream on the first push from a new local release branch.
- `--prep` should fail before editing anything if the target tag already exists locally or on `origin`.
- `--publish` should only create and push the annotated tag.
- `--publish` should verify the tagged source checkout resolves the package runtime version to the exact tag version before the push, and that check should not depend on `setuptools-scm` being installed in the release shell.
- `--publish` should fail fast if `CHANGELOG.md` does not already contain the tag heading, or if that release section exists but is empty.
- The helper should enforce the release branch policy unless the user explicitly overrides it.

## Repo examples

- `.github/workflows/nebius-cxcli-release.yml`
- `.github/workflows/vpngw-release.yml`
- `services/nebius-cxcli/publish-release.sh`
- `services/vpngw/publish-release.sh`
