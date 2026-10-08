---
name: publish-image
description: "Use only when explicitly asked to publish container images end to end: collect inputs, set up optional assets, prepare/merge a PR, tag, wait, verify tags/digest, and report. Use github-workflows for workflow YAML."
disable-model-invocation: true
---

# Publish Image

## Help

For `$publish-image --help` or `$publish-image -h` (including native Claude forms), return concise help and stop before
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

Use `$publish-image` in Codex, `/publish-image` in Claude Code, or
`/skills:publish-image` in the Claude plugin. Dollar-prefixed skill examples
refer to the same named skill on either host; use the native invocation syntax.
Preserve the declared invocation policy, approvals and workflow ownership.
Use available native tools; an unavailable required capability is a blocker,
never permission to bypass a guard or claim unobserved behavior.

## Purpose

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
- the approved `container` build and release contract: context, Dockerfile or
  Bake target, supported platforms, expected OCI metadata, vulnerability
  policy, SBOM/provenance requirements, and signing/verification policy

If required values are missing and cannot be derived from the repository, ask
the user before continuing.

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

Use setup mode when the project does not already have a release flow:

- `assets/CHANGELOG.md.template`
- `assets/publish-image.sh.template`

The image-publish workflow template is owned only by `$github-workflows`; use
that skill's canonical image-publish asset instead of keeping a duplicate here.

The project-local helper script is optional, but it is a maintained runnable
helper template, not a documentation stub. Keep it behaviorally aligned with
the skill-owned `scripts/publish-image-doer.sh`, which remains the canonical
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

- mode, project directory, tag, version, tag prefix
- release branch, PR URL, and merge result when `complete` mode is used
- pushed tag and workflow run URL/conclusion
- published image tags and digest verification
- validation commands run
- blockers, skipped live checks, or required user approvals

## Resources

- `scripts/publish-image-doer.sh`
- `assets/CHANGELOG.md.template`
- `assets/publish-image.sh.template`
- `$github-workflows` for image-publish workflow YAML
