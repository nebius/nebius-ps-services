# Changelog

All notable changes to this project are tracked here. This changelog follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## How to use this file

- Keep `## [Unreleased]` at the top and add bullets as changes land.
- Before release tagging, move `Unreleased` into a dated release section with
  `./publish-release.sh --prep X.Y.Z`.
- After changelog PR merge to `main`, run
  `./publish-release.sh --publish X.Y.Z` on clean synced `main`.
- Release section format:
  `## [github-report-vX.Y.Z] - YYYY-MM-DD`

## [Unreleased]
