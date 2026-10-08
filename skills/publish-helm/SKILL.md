---
name: publish-helm
description: "Use only when explicitly asked to publish Helm charts end to end: collect chart/OCI inputs, set up optional assets, prepare/merge a PR, tag, wait, verify the OCI chart, and report. Also supports setup-only guidance."
disable-model-invocation: true
---

# Publish Helm

## Help

For `$publish-helm --help` or `$publish-helm -h` (including native Claude forms), return concise help and stop before
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

## Agent Compatibility

Use `$publish-helm` in Codex, `/publish-helm` in Claude Code, or
`/skills:publish-helm` in the Claude plugin. Dollar-prefixed skill examples
refer to the same named skill on either host; use the native invocation syntax.
Preserve the declared invocation policy, approvals and workflow ownership.
Use available native tools; an unavailable required capability is a blocker,
never permission to bypass a guard or claim unobserved behavior.

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

Read [ordinary publication](references/ordinary-publication.md) before effects.

1. Resolve required explicit version, project, destination, tag prefix and live
   default. Setup-only installs requested assets and returns without publication.
2. For complete/prep, use create-pr intake before branch movement. Reuse the
   current feature; from default create a feature carrying the local work.
3. The helper's prep mode changes content only. Validate all repository changes,
   commit and push through create-pr's canonical whole-repository transaction.
4. Run local review-pr, repair safe findings through the private preparation
   handoff, revalidate and re-review. Stop for unsafe findings or human objections.
   Public prep stops at the reviewed PR. Complete routes merge only to merge-pr.
5. Observe actual protected merge, prove its exact result is in remote default
   history, and require passing applicable result CI. A queue is still pending.
6. In a clean isolated clone at that exact result, verify release metadata. Use
   helper tag mode to create an annotated tag, record its object, then push mode
   with that exact object. Never tag latest main or alter the source worktree.
7. Observe the exact tag/result publication workflow and verify the published
   artifact and digest. Report all evidence or the precise blocker.

## Setup Assets

Use setup mode when the chart does not already have a release flow:

- `assets/CHANGELOG.md.template`
- `assets/publish-helm.sh.template`

The project-local helper script is optional, but it is a maintained runnable
helper template, not a documentation stub. Keep it behaviorally aligned with
the skill-owned `scripts/publish-helm-doer.sh`, which remains the canonical
doer path.

## Guardrails

- Agentic SDLC is excluded; preserve its owners and exact promoted SHA.
- Complete authorization covers the necessary ordinary PR/review/merge/tag flow;
  do not request repeated commit, merge or routine Actions approval.
- Missing inputs, credentials, unsafe findings, human objections and additional
  protection requirements are blockers. Never bypass protections or use a PAT
  merge fallback. Agent review stays local; Actions runs deterministic jobs only.
- Prep never stages, commits, switches branches or pushes. create-pr owns those
  effects with normal hooks and repository-root `git add -A`.
- Publishing tags never edits release content. The reviewed changelog and chart
  metadata (when applicable) must already be present in the verified result.
- Never force-push, replace a tag, tag an unrelated default tip, or claim queued
  work is merged. Preserve unrelated user work and exact resume evidence.

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
