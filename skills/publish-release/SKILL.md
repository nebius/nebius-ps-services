---
name: publish-release
description: Generate an application release workflow by creating CHANGELOG.md, publish-release.sh, and .github/workflows/<project>-release-publish.yml for tag-driven GitHub Releases with artifact/version verification.
---

# Publish Release

Create a repeatable application-release setup for GitHub Releases.

## Use This Skill For

- Publishing versioned application artifacts to GitHub Releases.
- Standardizing release tagging + changelog flow across services.
- Enforcing a three-step release process:
  - `--prep` on branch
  - merge the changelog PR to `main`
  - `--publish` on clean synced `main`

## Output Contract

Generate exactly these artifacts in the target project:

1. `CHANGELOG.md`
2. `publish-release.sh`
3. `.github/workflows/<project-name>-release-publish.yml`

## Inputs to Collect

- `project_name` (for workflow filename/name)
- `project_tag_prefix` (for example `nebius-cxcli`)
- `package_import_name` (for example `nebius_cxcli`)
- `main_branch` (default `main`)
- `app_dir` (for example `services/<project>`)
- `python_version` (default `3.12` for Python projects)
- `asset_glob` (for example `dist/*.whl`)

## Workflow

1. Copy templates from `assets/` into the target project.
2. Replace placeholders:
   - `__PROJECT_NAME__`
   - `__PROJECT_TAG_PREFIX__`
   - `__PACKAGE_IMPORT_NAME__`
   - `__MAIN_BRANCH__`
   - `__APP_DIR__`
   - `__PYTHON_VERSION__`
   - `__ASSET_GLOB__`
3. Keep `publish-release.sh` executable.
4. Validate:
   - `bash -n publish-release.sh`
   - YAML parse for workflow
5. Confirm release flow docs in project README:
   - `./publish-release.sh --prep X.Y.Z`
   - `./publish-release.sh --publish X.Y.Z`
   - note that `--prep` auto-sets `origin/<branch>` as upstream on the first push from a new local release branch
   - note that `--prep` fails before editing `CHANGELOG.md` if the target tag already exists locally or on `origin`
   - note that `--prep` preserves markdownlint-safe blank lines between dated release sections when it rolls `Unreleased` forward
   - note that the clean-worktree check is strict and includes untracked files
   - note that `--publish` fails locally if the target release section is empty
   - note that `--publish` verifies the tagged source checkout resolves the package runtime version to `X.Y.Z` before pushing the tag

## Guardrails

- Do not edit changelog directly on `main`.
- `--prep` should start from a strictly clean worktree, including untracked files, so the changelog commit is isolated.
- `--prep` should push the current branch, and if no upstream exists yet, set `origin/<branch>` as upstream instead of failing with Git's default "no upstream branch" error.
- `--prep` should fail before editing `CHANGELOG.md` if the target tag already exists locally or on `origin`.
- `--prep` should preserve a blank line before the next `##` release heading when it rewrites `CHANGELOG.md`, so the file stays markdownlint-safe.
- `--publish` only creates/pushes tag; no content edits.
- `--publish` must fail if `CHANGELOG.md` does not already contain the target tag heading, or if that release section exists but is empty.
- `--publish` should verify `PYTHONPATH=src python` resolves `<package_import_name>.__version__ == X.Y.Z` before the tag push, and that check should work even when `setuptools-scm` is not installed in the release shell.
- Release workflow must build from tag commit, not floating branch refs.
- Resolve the tagged commit explicitly instead of assuming `GITHUB_SHA` is the release commit.
- Release workflow should verify the source-checkout runtime version from a plain interpreter before installing project dependencies, so the Git-based fallback path stays covered.
- Verify built artifact version equals release tag version.
- Fail if the release changelog section is missing or empty.
- Verify tag commit belongs to `main` history unless explicit exception is requested.
- Workflow/job check names should include `project_name` to avoid ambiguous
  checks across projects.

## Resources

- `assets/CHANGELOG.md.template`
- `assets/publish-release.sh.template`
- `assets/project-name-release-publish.yml.template`
