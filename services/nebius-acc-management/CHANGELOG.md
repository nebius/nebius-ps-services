# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [v0.1.0] - 2026-01-23

- Initial project scaffolding.
- Use the Nebius Python SDK for IAM, quotas, and federation operations.
- Auto-fetch IAM token on CLI start when not provided via environment.
- Add YAML-driven `apply` command with tenant-scoped config and optional quota file.
- Separate YAML-driven workflows from CLI-only commands.
- Support multi-region project definitions in config files.
- Add versioned config and quota schemas (version 1).
- Add invite workflows: invite file template, validation, and CLI command.
- Consolidate validation into `validate` for config/quota/invite files.
- Require `tenant_id` in quota files and validate against config.
