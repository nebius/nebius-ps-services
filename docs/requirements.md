<!-- markdownlint-disable MD001 MD024 -->
<!-- maintain-project-specs:requirements:start schema=maintain-project-specs/requirements-v1 -->
# Project Requirements

<!-- REQUIREMENT: REQ-001 status=active priority=P0 type=documentation -->
### REQ-001: Describe the repository as Nebius Platform Services

#### User Story

Repository visitors need the root README to describe the project as Nebius
Platform Services in the same terms as the canonical repository description.

#### Acceptance Criteria

- AC-001: The root README opening description is `Nebius Platform Services:
  reusable AI/ML deployment building blocks for Nebius AI Cloud`.
- AC-002: The root README continues to orient visitors to the monorepo layout,
  common use cases, cross-project policy, and project-local documentation.
- AC-003: The root changelog records the repository-wide branding update in
  its `[Unreleased]` section.

#### Negative Criteria

- NC-001: The branding update must not rename repository paths, packages,
  commands, release artifacts, or project-local historical attribution.
- NC-002: The root README must not describe the repository as Nebius Public
  Services or Nebius Professional Services.

#### Validation Method

Inspect the root README and changelog, search the affected root documents for
stale branding, and review the focused Git diff.

#### Test Method

Run exact text searches for the existing repository-slug title and new
description, reject stale Public or Professional Services branding in the root
README, and lint the changed Markdown files.

#### Evaluation Method

Compare the rendered root README opening sentence with the accepted repository
description and confirm that its existing repository-slug title and unrelated
project content are unchanged.

<!-- /REQUIREMENT: REQ-001 -->
<!-- maintain-project-specs:requirements:end -->
<!-- markdownlint-enable MD001 MD024 -->
