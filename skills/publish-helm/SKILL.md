---
name: publish-helm
description: "Use to publish Helm charts end to end from the current project: collect chart and OCI inputs, optionally set up changelog/helper/workflow assets, prep chart release changes, create and merge a PR, tag from the default branch, wait for the chart publish workflow, verify the pushed OCI chart, and report the result. Also supports explicit setup-only guidance."
---

# Publish Helm

## Help

For `$publish-helm --help` or `$publish-helm -h`, return concise help and stop before
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
- `--tag X.Y.Z[-prerelease]` or `<tag-prefix>-vX.Y.Z[-prerelease]`.
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

## Required Release Inputs

Before running `prep`, `publish`, or `complete`, resolve these from explicit
user input or unambiguous project configuration:

- Release version/tag is required. If the user does not provide it, ask for
  `X.Y.Z[-prerelease]` or the full `<tag-prefix>-vX.Y.Z[-prerelease]`; do not
  infer it from `Chart.yaml`, the latest Git tag, branch names, or changelog
  text.
- Publish destination is required. If the current project does not already have
  a chart publish workflow with a configured target and the user did not provide
  a destination, ask for the target registry or OCI repository details.
- For generic Helm CLI publishing and local verification, use an OCI repository
  base such as `oci://registry.example.com/org/charts`. If the user provides a
  full chart reference ending in the chart name, treat it as the report/pull
  reference and pass only the repository base to helper scripts.
- For project workflows that derive the upload target from provider-specific
  variables such as region and registry ID, inspect the workflow and use those
  variable names as the contract. Do not hardcode concrete registry IDs,
  registry URLs, project IDs, endpoints, or secret values in reusable skill
  sources.

## Destination Forms

- Generic Helm form: `helm push` receives the OCI repository base without chart
  name or version, and `helm pull` appends `<chart-name> --version <version>`.
- Workflow form: a project workflow may publish from environment, GitHub
  variables, or secrets and derive the final OCI reference at runtime.
  `--oci-repository` does not override such a workflow unless the workflow
  explicitly supports it; use it for local final verification and reporting.

## Workflow

1. Inspect the current project folder and Git repository.
2. Parse the requested mode and explicit tag. Helm supports SemVer prerelease
   tags such as `1.2.3-rc.1`; if no tag was provided, ask before continuing.
3. For `setup`, create or update chart-local release assets from `assets/` and
   validate the chart publish registration/workflow. Stop with a setup report.
4. For `prep`, require the current checkout to be the clean, synced default
   branch. If the user is on any other branch, or the tree is dirty, stop and
   ask them to merge their branch to the default branch, switch to it,
   fast-forward, and rerun. Then run the skill-owned helper script:
   `scripts/publish-helm-doer.sh --mode prep ...`
   The helper creates `release/<tag>` from the default branch, updates the
   chart changelog and `Chart.yaml`, validates dependencies, runs strict chart
   lint/template checks, commits release prep, and pushes that release branch.
5. For `complete`, run `prep`, invoke `create-pr`, then invoke `merge-pr` after
   checks pass for `release/<tag>`.
6. After merge, switch to the default branch, fetch, and fast-forward only.
   Verify the merged changelog and `Chart.yaml` contain the release version.
7. Run `publish` only from the clean, synced default branch:
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

The project-local helper script is optional, but it is a maintained runnable
helper template, not a documentation stub. Keep it behaviorally aligned with
the skill-owned `scripts/publish-helm-doer.sh`, which remains the canonical
doer path.

## Guardrails

- Do not hardcode registry URLs, repository names, project IDs, endpoints, or
  secrets in skill sources or generated examples.
- Store only GitHub variable and secret names in workflow templates.
- Do not print, request, or persist secret values.
- Ask for missing release tag or destination inputs instead of guessing.
- Do not include chart basename or version in `--oci-repository`.
- Treat the default branch as the release source of truth. `prep` and
  `publish` must start from a clean, synced default branch.
- If the user is on a feature branch or has local changes, fail fast before
  editing files and ask them to create and merge a PR to the default branch,
  switch to the default branch, fast-forward, and rerun.
- Do not use cherry-pick or commit-copy workflows to move release content
  between branches unless the user explicitly asks for that reconstruction.
- Treat `Chart.yaml` version changes as release-prep changes that must be
  merged before tagging; the publish/tag phase is intentionally read-only for
  chart metadata.
- Do not force-push, use admin merge, bypass branch protection, or ignore
  required checks/reviews.
- Stop when GitHub approvals, environment approvals, registry credentials, or
  branch protection require human action.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return:

- mode, chart directory, chart name, tag, version, tag prefix
- release branch, PR URL, and merge result when `complete` mode is used
- pushed tag and workflow run URL/conclusion
- OCI chart reference and pull verification result
- validation commands run
- blockers, skipped live checks, or required user approvals

## Resources

- `scripts/publish-helm-doer.sh`
- `assets/CHANGELOG.md.template`
- `assets/publish-helm.sh.template`
