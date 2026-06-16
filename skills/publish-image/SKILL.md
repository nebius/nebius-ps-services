---
name: publish-image
description: Generate a container image publication workflow by creating CHANGELOG.md, publish-image.sh, and .github/workflows/<project>-image-publish.yml with tag-driven releases, manual dispatch controls, and immutable tagging.
---

# Publish Image

Create a repeatable image publication setup for projects that release container images to a registry.

## Use This Skill For

- Adding an image release process to a new project.
- Standardizing an existing image publish process across services.
- Enforcing a two-step release flow:
  - `--prep` on branch (changelog update)
  - `--publish` on clean synced `main` (tag + push)

## Output Contract

Generate exactly these artifacts in the target project:

1. `CHANGELOG.md`
2. `publish-image.sh`
3. `.github/workflows/<project-name>-image-publish.yml`

## Inputs to Collect

- `project_name` (for workflow filename/name)
- `project_tag_prefix` (for example `sample-service`)
- `main_branch` (default `main`)
- `app_dir` (build context path, for example `services/<project>/webhook`)
- `python_version` (default `3.12` for Python-based test jobs)
- `image_name` (full registry path, for example `quay.io/org/app`)
- `registry_host` (for example `quay.io`)
- `publish_environment` (GitHub Actions environment name)
- `registry_secret_name` (secret with `username:token` or token)
- `registry_username_var_name` (optional variable when secret is token-only)

## Workflow

1. Copy templates from `assets/` into the target project.
2. Replace placeholders:
   - `__PROJECT_NAME__`
   - `__PROJECT_TAG_PREFIX__`
   - `__MAIN_BRANCH__`
   - `__APP_DIR__`
   - `__PYTHON_VERSION__`
   - `__IMAGE_NAME__`
   - `__REGISTRY_HOST__`
   - `__PUBLISH_ENVIRONMENT__`
   - `__REGISTRY_SECRET_NAME__`
   - `__REGISTRY_USERNAME_VAR_NAME__`
3. Keep `publish-image.sh` executable.
4. Validate:
   - `bash -n publish-image.sh`
   - YAML parse for the workflow
5. Document runtime usage in project README:
   - `./publish-image.sh --prep X.Y.Z`
   - `./publish-image.sh --publish X.Y.Z`

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

## Guardrails

- Do not commit changelog edits directly on `main`.
- `--publish` should tag and push only; no content edits on `main`.
- Tag format must be `<project_tag_prefix>-vMAJOR.MINOR.PATCH`.
- Release images from tag pushes, not mutable branch state.
- Prefer immutable image tags (`sha-*`, `X.Y.Z-g<sha>`) and digest pinning in production.
- Workflow/job check names should include `project_name` to avoid ambiguous
  checks across projects.

## Resources

- `assets/CHANGELOG.md.template`
- `assets/publish-image.sh.template`
- `assets/project-name-image-publish.yml.template`
