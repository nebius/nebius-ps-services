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

- make `make fmt` apply Ruff safe fixes before formatting so import-order issues are resolved before CI `lint`
- replace personal-owner examples in the README and CLI help output with `nebius`
- normalize example command ordering in the README and CLI help to `github-report <command> --owner <owner>`
- document supported output formats and filename-based format inference in CLI help and add a dedicated README section
- make `list-repos` public-only by default and add `--all` to include accessible private repositories

## [github-report-v0.1.2] - 2026-03-15

- replace the org-specific CLI flow with a required `--owner` option that works for GitHub organizations and personal accounts
- support activity reports for owner-wide scans and individual public repositories using the same modifications-then-commits ranking
- make the installer idempotent for re-runs and wheel upgrades, and simplify GitHub token guidance for end users

## [github-report-v0.1.1] - 2026-03-15

- improve installer guidance for Python setup, GitHub account/token creation, and post-install verification
- publish installer, tarball, checksums, and install notes as release assets

## [github-report-v0.1.0] - 2026-03-14

- first release
