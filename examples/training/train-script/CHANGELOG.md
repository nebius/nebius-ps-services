# Changelog

## [Unreleased]

- Document the Slurm rendezvous port override.

- Maintain the training application under the repository's Apache-2.0 license.
- Add product documentation and configuration guidance.
- Standardize the application as a src-layout package with a single uv lock,
  Python 3.14, updated dependencies, Ruff, pytest and installed-wheel validation.
- Replace hardcoded setup/download scripts with a validated CLI and explicit,
  non-destructive, revision-pinned asset acquisition.
- Correct single-worker execution, accumulation tails, validation sampling,
  checkpoint/resume identity, scaler/RNG recovery and export failure handling.
- Tokenize once into disk-backed run-local data, validate downloaded manifests,
  constrain validation split paths and redact malformed YAML diagnostics.
- Retain the latest complete epoch checkpoint and isolate run outputs; reject
  silent overwrite.
- Provide portable Slurm launch scripts and an original offline smoke fixture.
- Document the architecture and training workflow.
- Remove legacy entrypoints, pip requirements and compatibility branches.
