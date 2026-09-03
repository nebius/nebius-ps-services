# Changelog

This root changelog tracks repository-wide process, automation, and
documentation changes only. Project-specific release notes live in the owning
project folder.

## [Unreleased]

### Changed

- Added path-scoped CI for the reusable project-spec lifecycle and its ordinary
  session, Task Implementer, Agentic SDLC, hook installer, and project
  instruction adapters.
- Updated the root README description from Nebius Public Services to Nebius
  Platform Services and aligned it with the repository's reusable AI/ML
  deployment building-block focus.
- Helm chart publication validation now lints charts with subcharts after
  resolving chart dependencies and can run chart-specific strict lint and
  smoke render combinations.
- Helm chart publication now seeds a temporary Helm repository config from
  remote `Chart.yaml` dependencies before building locked dependencies on
  clean runners.
- Retired the downstream Soperator Helm chart family, its upstream verifier
  workflow, and its Helm publication entry now that cxcli consumes official
  upstream Soperator release artifacts directly.
- Reorganized the root README and changelog around monorepo ownership: root
  docs now describe repository layout and cross-project policy, while
  project-specific release notes live in project-local changelogs. The root
  README now avoids enumerating growing project names or every project-local
  changelog link.
- Added `skills/CHANGELOG.md` and moved reusable-skill release notes out of the
  root changelog.
- Expanded the repo-level Dependabot auto-merge policy so
  Dependabot-authored semver `uv` and `pip` dependency bumps can be
  auto-approved and auto-merged when every changed file is limited to Python
  dependency manifests or lockfiles, while source-code edits and other
  non-dependency file changes remain ineligible.
