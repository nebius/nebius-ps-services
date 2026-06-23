---
name: publish-release
description: "Use to publish GitHub Releases end to end from the current project: collect package inputs, optionally set up changelog/helper/workflow assets, prep the release branch, create and merge a PR, tag from the default branch, wait for the release workflow, verify the GitHub Release assets, and report the result. Also supports explicit setup-only guidance."
---

# Publish Release

Publish a versioned application/package release to GitHub Releases from the
current project folder. This is a doer-first skill: setup/guidance is still
supported, but release execution is the primary workflow when the user asks to
publish.

## Use This Skill For

- Publishing package artifacts to GitHub Releases end to end.
- Setting up release assets only when requested or missing.
- Running one explicit phase: `setup`, `prep`, `publish`, or `complete`.
- Producing a final publish report with PR, tag, workflow, release URL, and
  asset verification.

## Inputs Accepted

Common flags:

- `--mode setup|prep|publish|complete`; use `complete` for an end-to-end
  publish request.
- `--tag X.Y.Z` or `<tag-prefix>-vX.Y.Z`.
- `--project-dir <path>`; default current working directory.
- `--main-branch <branch>`; default the repository default branch.
- `--tag-prefix <prefix>`; derive from project name only when unambiguous.
- `--merge-method squash|merge|rebase`; default `squash`.
- `--wait` or `--no-wait`; default `--wait` for `publish` and `complete`.

Release inputs:

- `--project-name`
- `--package-import-name`
- `--asset-glob`
- `--python-version`
- optional build, lint, test, or artifact-verification commands

If required values are missing and cannot be derived from the repository, ask
the user before continuing.

## Workflow

1. Inspect the current project folder and Git repository.
2. Parse the requested mode and tag. Normalize tags to
   `<tag-prefix>-vMAJOR.MINOR.PATCH`.
3. For `setup`, create or update reusable release assets from `assets/`,
   validate the generated shell/workflow files, and stop with a setup report.
4. For `prep`, run the skill-owned helper script:
   `scripts/publish-release-doer.sh --mode prep ...`
   It updates `CHANGELOG.md`, commits release prep, and pushes the current
   branch. It must not edit the default branch directly.
5. For `complete`, run `prep`, invoke `create-pr`, then invoke `merge-pr` after
   checks pass.
6. After merge, switch to the default branch, fetch, and fast-forward only.
   Verify the release changelog section from prep is present.
7. Run `publish` from clean synced default branch:
   `scripts/publish-release-doer.sh --mode publish ...`
   The helper verifies the runtime package version when configured, creates the
   annotated tag, and pushes only the tag.
8. If waiting is enabled, find the tag-triggered workflow with `gh run list`,
   wait with `gh run watch --exit-status`, and inspect the terminal run.
9. Verify the GitHub Release with `gh release view <tag>` and confirm expected
   assets exist.
10. Return the final publish report.

## Setup Assets

Use setup mode when the project does not already have a release flow:

- `assets/CHANGELOG.md.template`
- `assets/publish-release.sh.template`
- `assets/project-name-release-publish.yml.template`

The project-local helper script is optional after this refactor. The skill-owned
`scripts/publish-release-doer.sh` remains the canonical doer path.

## Guardrails

- Do not hardcode repository names, private endpoints, or secrets in skill
  sources or generated examples.
- Store only variable and secret names in workflow templates.
- Do not print, request, or persist secret values.
- Do not commit changelog edits directly on the default branch.
- Do not force-push, use admin merge, bypass branch protection, or ignore
  required checks/reviews.
- Stop when GitHub approvals, environment approvals, missing credentials, or
  branch protection require human action.
- Verify runtime/artifact version alignment before pushing a release tag when
  package metadata is available.

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

## Output Contract

Return:

- mode, project directory, tag, version, tag prefix
- PR URL and merge result when `complete` mode is used
- pushed tag and workflow run URL/conclusion
- GitHub Release URL and asset verification result
- validation commands run
- blockers, skipped live checks, or required user approvals

## Resources

- `scripts/publish-release-doer.sh`
- `assets/CHANGELOG.md.template`
- `assets/publish-release.sh.template`
- `assets/project-name-release-publish.yml.template`
