# Changelog

All notable changes to this project are tracked here. This changelog follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## How to use this file

- Keep `## [Unreleased]` at the top and add bullets as changes land.
- Before tagging a release, move `Unreleased` into a dated release section with
  `./publish-image.sh --prep X.Y.Z`.
- After the changelog PR is merged to `main`, run
  `./publish-image.sh --publish X.Y.Z` on clean synced `main`.
- Release section format is:
  `## [mysterybox-bridge-vX.Y.Z] - YYYY-MM-DD`
- Newer releases go above older releases.

## [Unreleased]

## [mysterybox-bridge-v0.1.0] - 2026-03-10
