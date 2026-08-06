---
name: publish-release
description: "Use to publish GitHub Releases end to end from the current project: collect package inputs, optionally set up changelog/helper/workflow assets, prep the release branch, create and merge a PR, tag from the default branch, wait for the release workflow, verify the GitHub Release assets, and report the result. Also supports explicit setup-only guidance."
---

# Publish Release

## Help

For `$publish-release --help` or `$publish-release -h`, return concise help and stop before
any workflow step. State the purpose and invocation policy. Show exact usage
for every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

## Purpose

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
4. For `prep`, require the current checkout to be the clean, synced default
   branch. If the user is on any other branch, or the tree is dirty, stop and
   ask them to merge their branch to the default branch, switch to it,
   fast-forward, and rerun. Then run the skill-owned helper script:
   `scripts/publish-release-doer.sh --mode prep ...`
   The helper creates `release/<tag>` from the default branch, updates
   `CHANGELOG.md`, commits release prep, and pushes that release branch.
5. For `complete`, run `prep`, invoke `create-pr` for `release/<tag>`, then
   invoke `merge-pr` after checks pass.
6. After merge, switch to the default branch, fetch, and fast-forward only.
   Verify the release changelog section from prep is present.
7. Run `publish` only from the clean, synced default branch:
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

The project-local helper script is optional, but it is a maintained runnable
helper template, not a documentation stub. Keep it behaviorally aligned with
the skill-owned `scripts/publish-release-doer.sh`, which remains the canonical
doer path.

## Guardrails

- Do not hardcode repository names, private endpoints, or secrets in skill
  sources or generated examples.
- Store only variable and secret names in workflow templates.
- Do not print, request, or persist secret values.
- Treat the default branch as the release source of truth. `prep` and
  `publish` must start from a clean, synced default branch.
- If the user is on a feature branch or has local changes, fail fast before
  editing files and ask them to create and merge a PR to the default branch,
  switch to the default branch, fast-forward, and rerun.
- Do not use cherry-pick or commit-copy workflows to move release content
  between branches unless the user explicitly asks for that reconstruction.
- Do not force-push, use admin merge, bypass branch protection, or ignore
  required checks/reviews.
- Stop when GitHub approvals, environment approvals, missing credentials, or
  branch protection require human action.
- Verify runtime/artifact version alignment before pushing a release tag when
  package metadata is available.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return:

- mode, project directory, tag, version, tag prefix
- release branch, PR URL, and merge result when `complete` mode is used
- pushed tag and workflow run URL/conclusion
- GitHub Release URL and asset verification result
- validation commands run
- blockers, skipped live checks, or required user approvals

## Resources

- `scripts/publish-release-doer.sh`
- `assets/CHANGELOG.md.template`
- `assets/publish-release.sh.template`
- `assets/project-name-release-publish.yml.template`
