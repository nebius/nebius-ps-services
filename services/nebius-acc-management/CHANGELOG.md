# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [nebius-acc-v0.1.1] - 2026-01-23

- Add `--version` CLI flag and ensure installed wheels report the package version.
- Add installation instructions for pipx, upgrade, and version verification.
- Enforce prefixed release tags (`nebius-acc-vX.Y.Z`) and align `release.sh` with vpngw behavior.
- Ignore generated `_version.py` and expand `.gitignore` for common Python artifacts.

## [nebius-acc-v0.1.0] - 2026-01-23

- Consolidate validation into `validate` for config/quota/invite files.
- Require `tenant_id` in quota files and validate against config.
