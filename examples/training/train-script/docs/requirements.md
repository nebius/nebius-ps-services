<!-- markdownlint-disable MD001 MD024 -->
<!-- markdownlint-disable-next-line MD013 -->
<!-- maintain-project-specs:requirements:start schema=maintain-project-specs/requirements-v2 -->
# Project Requirements

<!-- REQUIREMENT: REQ-001 status=satisfied priority=P1 type=feature -->
### REQ-001: Portable, maintained training application

#### User Story

Researchers can install and run a locked src-layout training CLI using Python
3.14 where the dependency graph supports it. Preserve classification and seq2seq
training, explicit CPU validation, CUDA DDP, and Slurm launch capability.

#### Acceptance Criteria

- AC-001: Install the locked src-layout application on Python 3.14 and expose
  one canonical nebius-train-script command.
- AC-004: CLI help and YAML validation work without importing or installing the
  optional ML runtime.
- AC-005: Preserve classification and seq2seq workflows, explicit local CPU
  execution and Linux CUDA DDP through Slurm.

#### Negative Criteria

- NC-001: No compatibility wrapper, alternative dependency authority, cloud
  provisioning or unverified platform claim.

#### Validation Method

Locked install, CLI/config tests, installed wheel, and documented platform
boundaries.

#### Test Method

Locked install, CLI/config tests, installed wheel, and documented platform
boundaries.

#### Evaluation Method

Distinguish local, container, installed-artifact and live GPU observations.

<!-- /REQUIREMENT: REQ-001 -->

<!-- REQUIREMENT: REQ-002 status=satisfied priority=P1 type=feature -->
### REQ-002: Correct training and durable recovery

#### User Story

Training handles one or multiple workers, weighted accumulation tails and
unpadded validation. Run-local checkpoints retain model, optimizer, scheduler,
scaler and per-rank RNG; resume validates its configuration and data identity.
Failure must not masquerade as success or delete recovery state.

#### Acceptance Criteria

- AC-002: Normalize gradients across accumulation tails and workers by examples
  or non-padding target tokens; validation counts each example exactly once.
- AC-006: Atomically retain epoch checkpoints with optimizer, scheduler, scaler
  and per-rank RNG. Resume restores the same plan, input hashes and worker
  count.
- AC-007: Refuse output overwrite and changed resume inputs. Export or
  cancellation failure is unsuccessful; completion is recorded only after export
  succeeds.

#### Negative Criteria

- NC-002: No duplicated validation rows, repeated per-rank full-memory
  preprocessing, silent failure success or automatic checkpoint deletion.

#### Validation Method

Offline tensor and tiny-model integration tests, resume equivalence, and bounded
DDP trials where available.

#### Test Method

Offline tensor and tiny-model integration tests, resume equivalence, and bounded
DDP trials where available.

#### Evaluation Method

Distinguish local, container, installed-artifact and live GPU observations.

<!-- /REQUIREMENT: REQ-002 -->

<!-- REQUIREMENT: REQ-003 status=satisfied priority=P1 type=feature -->
### REQ-003: Secure assets and auditable provenance

#### User Story

Downloads require pinned revisions and recorded provenance, never delete
arbitrary user paths, and never persist authentication. Training uses local
assets with no remote code and safe weights. Retain the project license and
applicable dependency notices and separate model/data licenses from code
licensing.

#### Acceptance Criteria

- AC-003: Downloads require immutable revisions and declared licenses, reject
  existing destinations, and expose a verified manifest only after completion.
- AC-008: Training validates managed bundles, uses local safetensors without
  remote code, and loads trusted checkpoints with restricted deserialization. Do
  not persist or echo credentials.
- AC-009: Preserve the Apache-2.0 license and applicable third-party notices.
  Keep third-party dependency and model/data license obligations separate, and
  redistribute only owned or authorized documentation.
- AC-010: Present this application as a native component of this repository,
  with product-focused documentation and source comments, the standard project
  license and ordinary package metadata.

#### Negative Criteria

- NC-003: No mutable download revision, destructive refresh, input leakage,
  unlicensed third-party content or assertion that an advisory scan proves legal
  compliance.

#### Validation Method

Path and failure regressions, dependency advisory/license review, and
documentation/reference checks.

#### Test Method

Path and failure regressions, dependency advisory/license review, and
documentation/reference checks.

#### Evaluation Method

Distinguish local, container, installed-artifact and live GPU observations.

<!-- /REQUIREMENT: REQ-003 -->

<!-- maintain-project-specs:requirements:end -->
<!-- markdownlint-enable MD001 MD024 -->
