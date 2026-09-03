<!-- markdownlint-disable MD001 MD024 -->
<!-- maintain-project-specs:design:start schema=maintain-project-specs/design-v1 -->
# Project Design

<!-- FEATURE: FEAT-001 reqs=REQ-001 status=ready priority=P0 version=1 -->
### FEAT-001: Root repository identity documentation

#### Requirements Covered

- REQ-001: Describe the repository as Nebius Platform Services.

#### Context Evidence

The root `README.md` is the repository orientation page. Before this change,
its opening sentence called the project Nebius Public Services. The root
`CHANGELOG.md` explicitly owns repository-wide documentation changes.

#### Design Details

Keep the repository-slug H1 and place the accepted Nebius Platform Services
description verbatim in the opening paragraph. Preserve the existing
repository-layout, use-case, policy, automation, and license sections. Record
the branding change in the root `[Unreleased]` changelog.

#### Selected Option

Update only the human-facing root description and retain the existing
`nebius-ps-services` repository slug in the README title and wherever it
identifies a real path, package source, or release location.

#### Alternatives Considered

Renaming the README title, repository identifiers, or historical attribution
would expand the task beyond the requested description correction and risk
confusing the stable repository slug with the human-facing service name.

#### Implementation Boundaries

The implementation owns the root `README.md`, root `CHANGELOG.md`, and this
canonical root specification pair. Project-local documentation, source code,
configuration, package names, Git remotes, and release artifacts are excluded.

#### Test-First Success Criteria

- TDD-001: Before implementation, an exact search shows the root README
  contains the stale `Nebius Public Services` description.
- TDD-002: After implementation, exact searches find the accepted description
  and find no stale Public or Professional Services branding in the root
  README.

#### Validation Plan

Validate the canonical spec pair, run scoped Markdown lint and exact branding
searches, inspect `git diff --check`, and review the final changed-scope diff.

#### Test Plan

Use deterministic text assertions for the root README opening sentence and
stale-brand absence. Confirm the repository-slug title remains unchanged and
the root changelog has one concise Unreleased entry for the change.

#### Evaluation Plan

Review the root README as a visitor-facing document and compare its opening
sentence with the accepted repository identity and description.

#### Rollout And Rollback

Publish the documentation changes through the normal repository review flow.
If the branding decision is reversed, revert the focused root documentation
and matching specification records together.

#### Done Definition

The root README and changelog consistently present the accepted repository
identity, the canonical specs validate with complete traceability, and no
unrelated project behavior or identifiers change.

<!-- /FEATURE: FEAT-001 -->
<!-- maintain-project-specs:design:end -->
<!-- markdownlint-enable MD001 MD024 -->
