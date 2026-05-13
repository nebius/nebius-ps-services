# Changelog

This root changelog tracks repository-wide process, automation, and
documentation changes only. Project-specific release notes live in the owning
project folder.

## [Unreleased]

### Changed

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
