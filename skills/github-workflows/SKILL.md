---
name: github-workflows
description: "Create, review, or standardize GitHub Actions for PR/merge CI, bot-safe automation, reusable workflows, permissions, scoped checks, or release/image jobs. Use publish-* for complete publication flows."
---

# GitHub Workflows

## Help

For `$github-workflows --help` or `$github-workflows -h`, return concise help and stop before
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

Apply repository-native GitHub Actions patterns instead of inventing one-off workflows.

## Invocation Scope

- `standalone`: create or update the selected repository workflows.
- `coordinated-candidate`: receive exact workflow paths, project commands,
  exclusions, and private bundle from `scaffold-project`; emit candidate YAML
  only in that bundle and never write the target.

In coordinated-candidate scope, use one newly assigned project-prefixed
workflow file. Do not splice an existing workflow automatically, dispatch a
workflow, change repository settings, or claim application/infrastructure
files. Return candidate path, mode, provenance, and validation requirements.

## Use This Skill For

- Creating or updating `.github/workflows/*.yml` files.
- Standardizing service-scoped PR and merge CI in this monorepo.
- Adding or reviewing bot-only merge automation.
- Adding or reviewing tag-driven GitHub Release publication workflows.
- Adding or reviewing container image publish workflows.
- Translating a `container` build, platform, SBOM, provenance, vulnerability,
  and verification contract into GitHub Actions YAML.

## Workflow

1. Identify the workflow category.
   - Always read `references/best-practices.md`.
   - For PR/push CI or merge automation, read `references/pr-merge.md`.
   - For GitHub Releases, read `references/publish-release.md`.
   - For container image publication, read `references/container-image-publish.md`.
   - For a container workflow, obtain the approved build context, Dockerfile or
     Bake target, target platforms, cache policy, and supply-chain requirements
     from the `container` contract. Do not redesign the image in workflow YAML.

2. Prefer repository conventions.
   - Scope monorepo workflows with `paths`.
   - Use project-prefixed workflow and job names.
   - Set `defaults.run.working-directory` for service-local commands.
   - Keep `permissions` minimal and add `concurrency` for cancelable CI flows.

3. Start from the closest asset template.
   - `assets/project-name-ci.yml.template`
   - `assets/project-name-dependabot-auto-merge.yml.template`
   - `assets/project-name-release-publish.yml.template`
   - `assets/project-name-image-publish.yml.template`

4. Replace placeholders and adapt only what the target service needs.
   - Keep action major pins current with repo standards.
   - Keep shell steps fail-fast with `set -euo pipefail`.
   - Validate manual inputs before privileged or destructive steps.
   - For release workflows, collect the package import name as well as the tag
     prefix so the workflow can verify the source-checkout runtime version
     before dependency installation.

5. Validate locally when possible.
   - YAML parse the workflow.
   - If a paired script/template exists, run `bash -n`.
   - Run the same lint/test/build path the workflow expects when the target service can be validated locally.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Guardrails

- Do not use `pull_request_target` for untrusted PR code execution.
- Do not grant `contents: write` or `pull-requests: write` unless the workflow actually needs it.
- Release workflows must resolve the tagged commit explicitly and verify it belongs to the release branch.
- Release note generation must fail if the matching changelog section is missing or empty.
- Image publish workflows should emit immutable tags and record the published digest.
- Container workflows own CI orchestration only. Image/runtime design and
  validation requirements remain with `container`; registry release execution
  remains with `publish-image`.
- Merge automation should be bot-scoped and narrowly authorized.

## Resources

- Assets:
  - `assets/project-name-ci.yml.template`
  - `assets/project-name-dependabot-auto-merge.yml.template`
  - `assets/project-name-release-publish.yml.template`
  - `assets/project-name-image-publish.yml.template`
- References:
  - `references/best-practices.md`
  - `references/pr-merge.md`
  - `references/publish-release.md`
  - `references/container-image-publish.md`
