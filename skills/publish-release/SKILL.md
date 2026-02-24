---
name: publish-release
description: Generate an application release workflow by creating CHANGELOG.md, publish-release.sh, and .github/workflows/<project>-release-publish.yml for tag-driven GitHub Releases with artifact/version verification.
---

# Publish Release

Create a repeatable application-release setup for GitHub Releases.

## Use This Skill For

- Publishing versioned application artifacts to GitHub Releases.
- Standardizing release tagging + changelog flow across services.
- Enforcing a two-step release process:
  - `--prep` on branch
  - `--publish` on clean synced `main`

## Output Contract

Generate exactly these artifacts in the target project:

1. `CHANGELOG.md`
2. `publish-release.sh`
3. `.github/workflows/<project-name>-release-publish.yml`

## Inputs to Collect

- `project_name` (for workflow filename/name)
- `project_tag_prefix` (for example `nebius-cxcli`)
- `main_branch` (default `main`)
- `app_dir` (for example `services/<project>`)
- `python_version` (default `3.12` for Python projects)
- `asset_glob` (for example `dist/*.whl`)

## Workflow

1. Copy templates from `assets/` into the target project.
2. Replace placeholders:
   - `__PROJECT_NAME__`
   - `__PROJECT_TAG_PREFIX__`
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

## Guardrails

- Do not edit changelog directly on `main`.
- `--publish` only creates/pushes tag; no content edits.
- Release workflow must build from tag commit, not floating branch refs.
- Verify built artifact version equals release tag version.
- Verify tag commit belongs to `main` history unless explicit exception is requested.
- Workflow/job check names should include `project_name` to avoid ambiguous
  checks across projects.

## Resources

- `assets/CHANGELOG.md.template`
- `assets/publish-release.sh.template`
- `assets/project-name-release-publish.yml.template`
