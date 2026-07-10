---
name: github-workflows
description: "Use for GitHub Actions workflow work: create, review, or standardize PR/merge CI, bot-safe merge automation, reusable workflows, permissions, service-scoped checks, and release/image workflow YAML. For full project-specific release, image, or Helm publication flows with scripts or changelogs, use the matching publish-* skill."
---

# GitHub Workflows

Apply repository-native GitHub Actions patterns instead of inventing one-off workflows.

## Use This Skill For

- Creating or updating `.github/workflows/*.yml` files.
- Standardizing service-scoped PR and merge CI in this monorepo.
- Adding or reviewing bot-only merge automation.
- Adding or reviewing tag-driven GitHub Release publication workflows.
- Adding or reviewing container image publish workflows.

## Workflow

1. Identify the workflow category.
   - Always read `references/best-practices.md`.
   - For PR/push CI or merge automation, read `references/pr-merge.md`.
   - For GitHub Releases, read `references/publish-release.md`.
   - For container image publication, read `references/container-image-publish.md`.

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
