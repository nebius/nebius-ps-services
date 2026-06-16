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
   - note that `--prep` is idempotent while the target tag is still unreleased; once `Unreleased` is empty, reruns should leave `CHANGELOG.md` and `HEAD` unchanged
   - note that the clean-worktree check is strict and includes untracked files
   - note that `--publish` fails locally if the target release section is empty
   - note that `--publish` verifies the tagged source checkout resolves the package runtime version to `X.Y.Z` before pushing the tag

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings back
into this skill's local source materials before completion when the current task
contract allows source edits. Update the narrowest appropriate surface:
`SKILL.md` for runtime rules, `references/` for detailed guidance, `assets/`
for reusable templates, `scripts/` for deterministic helpers, and README or
changelog entries for human-facing or release-note updates.

If the current task is explicitly read-only/report-only, or source writes are
outside this skill's task contract, do not edit skill sources; report the
skipped source update instead.

Do not capture secrets, private URLs, customer data, raw logs, one-off local
state, or unverified/vendor-specific claims. If a useful learning is not safe,
not evidence-backed, or outside this skill's scope, report that it was skipped.

## Guardrails

- Do not edit changelog directly on `main`.
- `--prep` should start from a strictly clean worktree, including untracked files, so the changelog commit is isolated.
- `--prep` should push the current branch, and if no upstream exists yet, set `origin/<branch>` as upstream instead of failing with Git's default "no upstream branch" error.
- `--prep` should fail before editing `CHANGELOG.md` if the target tag already exists locally or on `origin`.
- `--prep` should preserve a blank line before the next `##` release heading when it rewrites `CHANGELOG.md`, so the file stays markdownlint-safe.
- `--prep` should be idempotent while the target tag remains unreleased: once `Unreleased` is empty, reruns should not rewrite `CHANGELOG.md` or create another commit.
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
