<!-- markdownlint-disable MD001 MD024 -->
<!-- markdownlint-disable-next-line MD013 -->
<!-- maintain-project-specs:design:start schema=maintain-project-specs/design-v2 -->
# Project Design

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-001 reqs=REQ-001 status=ready delivery=verified priority=P1 version=1 -->
### FEAT-001: Package and operation boundaries

#### Requirements Covered

- REQ-001: Portable, maintained training application

#### Context Evidence

The application supports classification and seq2seq training on local assets.
Regression tests cover collective ordering, checkpoint recovery, input integrity
and dependency configuration.

#### Design Details

Keep the fixed PyTorch/Hugging Face stack. Use a Typer CLI with lazy ML imports,
strict Pydantic YAML configuration, one uv lock, setuptools packaging and pytest
network guards. Require Python 3.14. The lightweight CLI has mutually exclusive
CPU and GPU extras, explicit PyTorch indexes and a single uv lock. Training is
an ordinary finite workflow containing model calls; it needs no agent runtime.
Slurm remains external scheduling, and the supplied cluster is borrowed.

#### Selected Option

Retain the workload and platform; introduce explicit boundaries and fail-fast
contracts.

#### Alternatives Considered

A monolithic module would couple configuration, data preparation and training,
reducing testability. Replacing the runtime with Trainer or a new orchestration
service expands scope without a demonstrated need.

#### Implementation Boundaries

src/nebius_train/{cli,config,__main__}.py, pyproject.toml, Makefile, Slurm glue
and README. No service, cloud provisioning, IAM, or repository-wide CI changes.

#### Test-First Success Criteria

- TDD-001: Reproduce the relevant boundary defect or establish a new observable
  acceptance test before claiming a fix.

#### Validation Plan

Locked install, CLI/config tests, installed wheel, and documented platform
boundaries.

#### Test Plan

Use local synthetic model/data; mock only external download and scheduler
boundaries.

#### Evaluation Plan

Record exact outcomes and incomplete external evidence independently.

#### Rollout And Rollback

Changes are confined to this component and do not alter existing user
environments. Checkpoints require a matching training plan.

#### Done Definition

The intended behavior is implemented, focused checks pass, and evidence limits
are documented.

#### Implementation Evidence

Implemented in the referenced source modules, CLI, tests and documentation. See
[validation](validation.md) for the executed boundaries.

#### Verification Evidence

Verification results are recorded in [validation](validation.md), with local
CPU, isolated Linux container, installed-wheel and live Slurm evidence kept
separate.

<!-- /FEATURE: FEAT-001 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-002 reqs=REQ-002 status=ready delivery=verified priority=P1 version=1 -->
### FEAT-002: Training and recovery lifecycle

#### Requirements Covered

- REQ-002: Correct training and durable recovery

#### Context Evidence

The application supports classification and seq2seq training on local assets.
Regression tests cover collective ordering, checkpoint recovery, input integrity
and dependency configuration.

#### Design Details

Separate data preparation, distributed lifecycle, checkpoint persistence and
training loops. Create one exclusive run directory before work, tokenize once on
rank zero into disk-backed run-local data, guard collectives for world size one,
and initialize NCCL only for CUDA distributed execution (Gloo for explicit CPU
tests). Sum loss by examples or non-padding target tokens; normalize accumulated
gradients by global units after DDP averaging. Evaluate unwrapped replicas with
disjoint indices. Checkpoint atomically at epoch boundaries with scaler/RNG and
input identity; retain it on every outcome. Strict resume targets a named
existing run and the same training plan. Export errors propagate.

#### Selected Option

Retain the workload and platform; introduce explicit boundaries and fail-fast
contracts.

#### Alternatives Considered

A monolithic module would couple configuration, data preparation and training,
reducing testability. Replacing the runtime with Trainer or a new orchestration
service expands scope without a demonstrated need.

#### Implementation Boundaries

src/nebius_train/ml/{data,distributed,checkpoint,engine}.py; unit and real
tiny-model integration tests. No causal-LM, sharding, mid-epoch resume, model
serving, or legacy checkpoint conversion.

#### Test-First Success Criteria

- TDD-002: Reproduce the relevant boundary defect or establish a new observable
  acceptance test before claiming a fix.

#### Validation Plan

Offline tensor and tiny-model integration tests, resume equivalence, and bounded
DDP trials where available.

#### Test Plan

Use local synthetic model/data; mock only external download and scheduler
boundaries.

#### Evaluation Plan

Record exact outcomes and incomplete external evidence independently.

#### Rollout And Rollback

Changes are confined to this component and do not alter existing user
environments. Checkpoints require a matching training plan.

#### Done Definition

The intended behavior is implemented, focused checks pass, and evidence limits
are documented.

#### Implementation Evidence

Implemented in the referenced source modules, CLI, tests and documentation. See
[validation](validation.md) for the executed boundaries.

#### Verification Evidence

Verification results are recorded in [validation](validation.md), with local
CPU, isolated Linux container, installed-wheel and live Slurm evidence kept
separate.

<!-- /FEATURE: FEAT-002 -->

<!-- markdownlint-disable-next-line MD013 -->
<!-- FEATURE: FEAT-003 reqs=REQ-003 status=ready delivery=verified priority=P1 version=1 -->
### FEAT-003: Asset and supply-chain policy

#### Requirements Covered

- REQ-003: Secure assets and auditable provenance

#### Context Evidence

The application supports classification and seq2seq training on local assets.
Regression tests cover collective ordering, checkpoint recovery, input integrity
and dependency configuration.

#### Design Details

Download into a fresh destination with an incomplete marker and create a
manifest only after success; never recursively refresh caller paths. Pin
immutable model/dataset revisions and record repository IDs, licenses and
artifact hashes. Do not call login or accept tokens as CLI flags. Use
local_files_only, trust_remote_code=False and safetensors; restricted
weights_only checkpoint loading. Preserve the project's Apache-2.0 license and
use original explanatory markup. Downloaded assets have independent licenses.
Present this application as a native component of this repository using
product-focused documentation and source comments. Include the standard project
LICENSE in the package. Generated package metadata must reflect the same
presentation.

#### Selected Option

Retain the workload and platform; introduce explicit boundaries and fail-fast
contracts.

#### Alternatives Considered

A monolithic module would couple configuration, data preparation and training,
reducing testability. Replacing the runtime with Trainer or a new orchestration
service expands scope without a demonstrated need.

#### Implementation Boundaries

src/nebius_train/download.py, LICENSE, pyproject.toml, docs/security.md and
docs/references.md. The artifacts module validates downloaded manifests and
rejects incomplete bundles. Credentials and live runtime evidence stay outside
Git.

#### Test-First Success Criteria

- TDD-003: Reproduce the relevant boundary defect or establish a new observable
  acceptance test before claiming a fix.

#### Validation Plan

Path and failure regressions, dependency advisory/license review, and
documentation/reference checks.

#### Test Plan

Use local synthetic model/data; mock only external download and scheduler
boundaries.

#### Evaluation Plan

Record exact outcomes and incomplete external evidence independently.

#### Rollout And Rollback

Changes are confined to this component and do not alter existing user
environments. Checkpoints require a matching training plan.

#### Done Definition

The intended behavior is implemented, focused checks pass, and evidence limits
are documented.

#### Implementation Evidence

Implemented in the source modules, CLI, package metadata and documentation. The
wheel includes the standard project LICENSE.

#### Verification Evidence

A recursive folder scan, wheel inspection, refreshed installed metadata,
executable-code comparison, lint and syntax checks passed. Runtime verification
results remain recorded in [validation](validation.md), with CPU, container,
installed-wheel and live Slurm evidence kept separate.

<!-- /FEATURE: FEAT-003 -->

<!-- maintain-project-specs:design:end -->
<!-- markdownlint-enable MD001 MD024 -->
