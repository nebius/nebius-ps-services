---
name: publish-release
description: "Use only when explicitly asked to publish GitHub Releases end to end: collect package inputs, set up optional assets, prepare/merge a PR, tag, wait, verify assets, and report. Also supports setup-only guidance."
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

## When To Use

- Publishing package artifacts to GitHub Releases end to end.
- Setting up release assets only when requested or missing.
- Running one explicit phase: `setup`, `prep`, `publish`, or `complete`.
- Producing a final publish report with PR, tag, workflow, release URL, and
  asset verification.

## When Not To Use

- Use `create-pr` for a PR that is not part of an explicitly requested release.
- Use the artifact-specific publish skill for Helm charts, container images, or
  another separately owned publication flow.
- Do not run a release workflow from a managed child worktree or active SDLC
  integration branch; return to that workflow's publication owner.

## Inputs

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

## Required Reads

- Read applicable repository instructions, current Git status and branch,
  default-branch and remote state, `CHANGELOG.md`, package version metadata, and
  the tag-triggered release workflow before mutation.
- Read the setup assets only when setup is requested or required files are
  missing.
- Resolve and follow `create-pr` and `merge-pr` for their owned publication and
  merge gates during `complete` mode.

## Writes

- `setup` may create or update the project changelog, helper, and release
  workflow after the user requests setup or complete publication needs them.
- `prep` updates and commits the changelog, then pushes either the reused
  feature branch or a new `release/<tag>` branch created from the default
  branch.
- `complete` may create and merge the release-prep PR; `publish` and `complete`
  may create and push the annotated tag under the explicit release request.
- Do not create repository-local private workflow state.

## Process

1. Inspect the current project folder and Git repository.
2. Parse the requested mode and tag. Normalize tags to
   `<tag-prefix>-vMAJOR.MINOR.PATCH`.
3. For `setup`, create or update reusable release assets from `assets/`,
   validate the generated shell/workflow files, and stop with a setup report.
4. For `prep`, require a clean named branch, an absent release tag, and current
   default-branch history. If the current branch is the default branch, require
   it to equal `origin/<default>` and create `release/<tag>`. If it is a feature
   branch, require current `origin/<default>` to be an ancestor, require any
   same-named remote branch to be an ancestor of local `HEAD`, and reuse the
   current branch. Then run the skill-owned helper script:
   `scripts/publish-release-doer.sh --mode prep ...`
   The helper updates `CHANGELOG.md`, commits release prep, and pushes the
   selected PR branch without committing directly on the default branch.
5. For `complete`, run `prep`, invoke `create-pr` for the selected prep branch,
   then invoke `merge-pr` after checks pass. Reuse an existing PR for that
   feature branch when `create-pr` resolves one; do not create a nested release
   branch from a feature branch.
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

## Idempotency

- Refuse an existing local or remote release tag before changelog mutation.
- Reuse the current feature branch and any matching open PR instead of creating
  duplicate branches or PRs. From the default branch, refuse a colliding
  `release/<tag>` branch rather than overwriting it.
- A repeated feature-branch prep for an untagged version may merge new
  `Unreleased` notes into the existing release section; never duplicate or
  empty an already prepared release section.
- Before tagging, re-read the merged changelog and current remote/default
  identity instead of relying on prep-time state.

## Failure Handling

- Stop before changelog mutation on a dirty or detached checkout, a stale
  feature branch, remote feature-branch divergence, an empty release payload,
  a duplicate tag, or an unsynchronized default branch.
- Stop at failing checks, required reviews or approvals, merge conflicts,
  missing credentials, or branch protection. Preserve the prepared branch and
  report the exact next owner action; do not retry by bypassing the gate.
- If prep succeeds but merge or publication fails, resume from the existing
  branch, PR, or tag state only after re-verifying its exact identity.

## Must Not

- Do not hardcode repository names, private endpoints, or secrets in skill
  sources or generated examples.
- Store only variable and secret names in workflow templates.
- Do not print, request, or persist secret values.
- Do not update the changelog in a dirty worktree or commit release prep
  directly on the default branch.
- Do not create a second release branch when a clean current feature branch can
  be the PR head.
- Do not publish a tag from a feature branch, a detached checkout, or a default
  branch that differs from `origin/<default>`.
- Do not use cherry-pick or commit-copy workflows to move release content
  between branches unless the user explicitly asks for that reconstruction.
- Do not force-push, use admin merge, bypass branch protection, or ignore
  required checks/reviews.
- Stop when GitHub approvals, environment approvals, missing credentials, or
  branch protection require human action.
- Verify runtime/artifact version alignment before pushing a release tag when
  package metadata is available.

## Completion Criteria

- Prep selected the correct branch path, committed a non-empty release section,
  and pushed the exact PR head.
- Complete mode merged that prep branch through required checks and reviews,
  refreshed the default branch by fast-forward only, and verified the release
  section before tagging.
- Publish pushed the annotated tag from the clean synchronized default branch;
  when waiting is enabled, the tag-triggered workflow completed successfully
  and the GitHub Release contains every expected asset.
- The final report distinguishes source/static, Git/PR, workflow, release, and
  asset evidence and names every skipped or blocked lane.

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
