---
name: publish-helm
description: "Use to publish Helm charts end to end from the current project: collect chart and OCI inputs, optionally set up changelog/helper/workflow assets, prep chart release changes, create and merge a PR, tag from the default branch, wait for the chart publish workflow, verify the pushed OCI chart, and report the result. Also supports explicit setup-only guidance."
---

# Publish Helm

Publish a Helm chart release from the current project folder. This is a
doer-first skill: setup/guidance is still supported, but release execution is
the primary workflow when the user asks to publish.

## Use This Skill For

- Publishing a Helm chart to an OCI registry end to end.
- Setting up chart release assets only when requested or missing.
- Running one explicit phase: `setup`, `prep`, `publish`, or `complete`.
- Producing a final publish report with PR, tag, workflow, chart ref, and pull
  verification.

## Inputs Accepted

Common flags:

- `--mode setup|prep|publish|complete`; use `complete` for an end-to-end
  publish request.
- `--tag X.Y.Z` or `<tag-prefix>-vX.Y.Z`.
- `--project-dir <path>`; default current working directory.
- `--main-branch <branch>`; default the repository default branch.
- `--tag-prefix <prefix>`; derive from chart name only when unambiguous.
- `--merge-method squash|merge|rebase`; default `squash`.
- `--wait` or `--no-wait`; default `--wait` for `publish` and `complete`.

Helm inputs:

- `--chart-dir`
- `--chart-name`
- `--oci-repository`, such as `oci://registry.example.com/org/charts`
- `--public-verify` when anonymous pull verification is expected
- optional extra lint or template smoke arguments

`--oci-repository` is the repository base. It must not include the chart
basename or chart version tag; Helm infers those from chart metadata.

## Workflow

1. Inspect the current project folder and Git repository.
2. Parse the requested mode and tag. Helm supports SemVer prerelease tags such
   as `1.2.3-rc.1`.
3. For `setup`, create or update chart-local release assets from `assets/` and
   validate the chart publish registration/workflow. Stop with a setup report.
4. For `prep`, run the skill-owned helper script:
   `scripts/publish-helm-doer.sh --mode prep ...`
   It updates the chart changelog and `Chart.yaml`, validates dependencies,
   runs strict chart lint/template checks, commits release prep, and pushes the
   current branch.
5. For `complete`, run `prep`, invoke `create-pr`, then invoke `merge-pr` after
   checks pass.
6. After merge, switch to the default branch, fetch, and fast-forward only.
   Verify the merged changelog and `Chart.yaml` contain the release version.
7. Run `publish` from clean synced default branch:
   `scripts/publish-helm-doer.sh --mode publish ...`
   The helper creates and pushes only the annotated tag. If `Chart.yaml` still
   has a different version, stop and run `prep`; do not tag a release expecting
   the workflow to rewrite chart metadata.
8. If waiting is enabled, find the tag-triggered workflow with `gh run list`,
   wait with `gh run watch --exit-status`, and inspect the terminal run.
9. Verify the published chart with
   `helm pull <oci-repository>/<chart-name> --version <version>`.
10. Return the final publish report.

## Setup Assets

Use setup mode when the chart does not already have a release flow:

- `assets/CHANGELOG.md.template`
- `assets/publish-helm.sh.template`

The project-local helper script is optional after this refactor. The skill-owned
`scripts/publish-helm-doer.sh` remains the canonical doer path.

## Guardrails

- Do not hardcode registry URLs, repository names, project IDs, endpoints, or
  secrets in skill sources or generated examples.
- Store only GitHub variable and secret names in workflow templates.
- Do not print, request, or persist secret values.
- Do not include chart basename or version in `--oci-repository`.
- Do not commit release prep directly on the default branch.
- Treat `Chart.yaml` version changes as release-prep changes that must be
  merged before tagging; the publish/tag phase is intentionally read-only for
  chart metadata.
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

- mode, chart directory, chart name, tag, version, tag prefix
- PR URL and merge result when `complete` mode is used
- pushed tag and workflow run URL/conclusion
- OCI chart reference and pull verification result
- validation commands run
- blockers, skipped live checks, or required user approvals

## Resources

- `scripts/publish-helm-doer.sh`
- `assets/CHANGELOG.md.template`
- `assets/publish-helm.sh.template`
