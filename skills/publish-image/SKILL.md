---
name: publish-image
description: "Use to publish container images end to end from the current project: collect release inputs, optionally set up changelog/helper/workflow assets, prep the release branch, create and merge a PR, tag from the default branch, wait for the image publish workflow, verify pushed image tags/digest, and report the result. Also supports explicit setup-only guidance."
---

# Publish Image

Publish a container image release from the current project folder. This is a
doer-first skill: setup/guidance is still supported, but release execution is
the primary workflow when the user asks to publish.

## Use This Skill For

- Publishing a container image end to end.
- Setting up image release assets only when the user asks for setup or required
  assets are missing.
- Running one explicit phase: `setup`, `prep`, `publish`, or `complete`.
- Producing a final publish report with PR, tag, workflow, image tag, and
  digest evidence.

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

Image inputs:

- `--project-name`
- `--image-name` as a full image reference, for example
  `[HOST[:PORT]/]NAMESPACE/REPOSITORY`
- `--registry-host`
- `--context`
- `--dockerfile`
- `--platforms`
- `--publish-environment`
- registry secret or variable names, never secret values

If required values are missing and cannot be derived from the repository, ask
the user before continuing.

## Workflow

1. Inspect the current project folder and Git repository.
2. Parse the requested mode and tag. Normalize tags to
   `<tag-prefix>-vMAJOR.MINOR.PATCH`.
3. For `setup`, create or update reusable release assets from `assets/`, using
   user-provided registry names, secret names, and project inputs. Validate the
   generated shell and workflow files, then stop with a setup report.
4. For `prep`, run the skill-owned helper script:
   `scripts/publish-image-doer.sh --mode prep ...`
   It updates `CHANGELOG.md`, commits the release prep, and pushes the current
   branch. It must not edit the default branch directly.
5. For `complete`, run `prep`, then invoke `create-pr` for the current branch.
   After PR checks pass, invoke `merge-pr` with the selected merge method.
6. After merge, switch to the default branch, fetch, and fast-forward only.
   Verify the release changelog section from prep is present.
7. Run `publish` from clean synced default branch:
   `scripts/publish-image-doer.sh --mode publish ...`
   The helper creates and pushes only the annotated tag.
8. If waiting is enabled, find the tag-triggered workflow with `gh run list`,
   wait with `gh run watch --exit-status`, and inspect the terminal run.
9. Verify published image tags with
   `docker buildx imagetools inspect <image>:<version>` and record the digest.
10. Return the final publish report.

## Setup Assets

Use setup mode when the project does not already have a release flow:

- `assets/CHANGELOG.md.template`
- `assets/publish-image.sh.template`
- `assets/project-name-image-publish.yml.template`

The project-local helper script is optional after this refactor. The skill-owned
`scripts/publish-image-doer.sh` remains the canonical doer path.

## Guardrails

- Do not hardcode registry URLs, repository names, project IDs, endpoints, or
  secrets in skill sources or generated examples.
- Store only secret and variable names in workflow templates.
- Do not print, request, or persist secret values.
- Do not publish from mutable branch state; publish from a release tag.
- Do not commit changelog edits directly on the default branch.
- Do not force-push, use admin merge, bypass branch protection, or ignore
  required checks/reviews.
- Stop when GitHub approvals, environment approvals, registry credentials, or
  branch protection require human action.

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
- published image tags and digest verification
- validation commands run
- blockers, skipped live checks, or required user approvals

## Resources

- `scripts/publish-image-doer.sh`
- `assets/CHANGELOG.md.template`
- `assets/publish-image.sh.template`
- `assets/project-name-image-publish.yml.template`
