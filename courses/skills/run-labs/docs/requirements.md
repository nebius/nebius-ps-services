<!-- markdownlint-disable MD001 MD024 -->
<!-- maintain-project-specs:requirements:start schema=maintain-project-specs/requirements-v2 -->
# Project Requirements

<!-- REQUIREMENT: REQ-001 status=satisfied priority=P0 type=feature -->
### REQ-001: Opening workstation login guidance

#### User Story

A course operator needs the first workflow step to explain how to synchronize and enter the prepared Slurm login node from the local course checkout.

#### Acceptance Criteria

- AC-001: The opening workflow documents `./sync-labs.sh <slurm-login-ip-address>` from the workstation courses root, the remote `~/courses` shell, the default root account and explicit user override.

#### Negative Criteria

- NC-001: Interactive login must not become a prerequisite for automation or run during help, status or dry-run requests; campaign sync and preflight remain authoritative.

#### Validation Method

Compare the instructions with the existing sync script and campaign preparation path; validate skill structure and canonical specifications.

#### Test Method

Use existing local fixture tests for account selection, interactive handoff, sync-only and dry-run behavior; do not connect to a live cluster.

#### Evaluation Method

Review the changed instructions and public-safe evaluation cases. Distinguish static source evidence from fresh runtime and live-login evidence.

<!-- /REQUIREMENT: REQ-001 -->

<!-- REQUIREMENT: REQ-002 status=satisfied priority=P1 type=feature -->
### REQ-002: Prepared monitoring runtime qualification

#### User Story

A campaign operator can reuse an identified prepared monitoring installation after independently checking its current runtime behavior.

#### Acceptance Criteria

- AC-001: Bind cluster, private service and backend identities to existing connection provenance, one healthy label-preserving results scrape, fresh complete GPU telemetry, intended local metrics routing and exact publication readback.
- AC-002: Preserve explicit local-only routing. Distinguish runtime preflight from new setup discovery and connection-receipt issuance; a resume alone does not rerun setup.
- AC-003: Record actual observations and skill revision separately from unchanged frozen course inputs. Source skill fixes may be activated without replaying completed GPU work.

#### Negative Criteria

- NC-001: Do not convert obsolete setup receipts, claim a failed setup validator passed, weaken runtime evidence, or automatically install infrastructure.

#### Validation Method

Review instructions against current course helpers, publisher inputs and campaign preflight contracts.

#### Test Method

Exercise source-owned evaluation cases and existing campaign regression checks; verify source and project-installed parity.

#### Evaluation Method

Report setup, source and runtime proof separately; require actual runtime observations before execution.

<!-- /REQUIREMENT: REQ-002 -->

<!-- REQUIREMENT: REQ-003 status=satisfied priority=P1 type=feature -->
### REQ-003: CUDA capstone safety prerequisite

#### User Story

An operator running the CUDA capstone needs the reviewed recipe to retain the guide's separate memory check and independently verified three-trial decision.

#### Acceptance Criteria

- AC-001: Run the supplied memcheck launcher as a dependency before capstone trials with the same selected profile and prepared executable. Require its exact completed job and zero-error original output during independent verification.
- AC-002: Preserve all three unprofiled child records, independently check the aggregate decision and shared observed hardware identity, and cover every child's dashboard metrics. Keep sanitizer and profiler measurements outside acceptance timings.

#### Negative Criteria

- NC-001: Do not treat sanitizer output as an acceptance result, substitute one trial for all three, fabricate aggregate result JSON, or claim application integration from this lab.

#### Validation Method

Check recipe ordering/profile expansion and guide alignment, then retain separate live sanitizer, aggregate, publication and browser proof.

#### Test Method

Exercise dependency ordering and profile selection plus existing verification-job completeness and multi-result metric coverage gates.

#### Evaluation Method

Review source and installed parity; distinguish static checks from target execution and visual acceptance.

<!-- /REQUIREMENT: REQ-003 -->

<!-- REQUIREMENT: REQ-004 status=satisfied priority=P1 type=feature -->
### REQ-004: Native host-transfer evidence

#### User Story

An operator can verify Fundamentals Lab 03 through its actual host-to-device copy mechanism even when its valid Systems reports contain no CUDA kernels.

#### Acceptance Criteria

- AC-001: Require host-copy proof for every Lab 03 Systems report, bound to the exact job, producer, effective buffer size and workload interval. Verify all four ordered memory/blocking modes, complete correlated submissions and completions, byte totals, synchronization and final payload readback.
- AC-002: Require actual copy timeline/detail review for both configurations with the mechanism-specific visual check. Preserve source, report and query hashes, correctness, publication and export gates.

#### Negative Criteria

- NC-001: Empty or incidental kernel lists cannot bypass transfer proof; initialization, partial copies, another job or payload size and a generic kernel approval must fail.
- NC-002: Do not claim overlap, independent per-mode payload equality or acceptance timing from the diagnostic traces.

#### Validation Method

Compare frozen source and original native copy/API records; validate identities, exact source cardinality and browser evidence independently.

#### Test Method

Exercise valid copy-only and incidental-kernel evidence, identity/count/size/ordering/type corruption and both direct and runner visual gates.

#### Evaluation Method

Separate source tests from native target and actual browser/export verification; retain warnings and original timing limitations.

<!-- /REQUIREMENT: REQ-004 -->

<!-- REQUIREMENT: REQ-005 status=satisfied priority=P1 type=feature -->
### REQ-005: Scoped diagnostic resource requests

#### User Story

An operator needs reviewed profiler recipes to retain the guide's process mode and host-memory request without changing clean measurements.

#### Acceptance Criteria

- AC-001: Inference Lab 10 captures use --in-process; unprofiled variants retain default process mode. Only Compute requests 262144 MiB of host memory.
- AC-002: Freeze a stage-local environment mapping with only SBATCH_MEM_PER_NODE as a positive decimal string in MiB. Reject unknown keys and malformed or unbounded-zero values before effects. Merge it only into that stage, with recipe values taking precedence over prepared defaults.

#### Negative Criteria

- NC-001: Do not mutate recipe inputs, leak the override to other stages or profiles, introduce shell prefixes, or admit secrets and controller-owned variables through this mapping.

#### Validation Method

Compare guide, executable recipe, frozen stages and transport behavior; keep static checks separate from live allocation and native acceptance.

#### Test Method

Exercise both profiles, exact diagnostic flags, memory scoping, precedence, input immutability and invalid mappings through the canonical controller plan.

#### Evaluation Method

Verify source and installed parity, then independently inspect the actual allocation and native report contents during execution.

<!-- /REQUIREMENT: REQ-005 -->
<!-- REQUIREMENT: REQ-006 status=satisfied priority=P1 type=feature -->
### REQ-006: Native course jobs and exact-job evidence ownership

#### User Story

An operator executes the same readable native Slurm jobs that students inspect, while retaining durable campaign recovery and independently verified evidence.

#### Acceptance Criteria

- AC-001: Run uses --workload small|large|both, defaults to both and freezes COURSE_WORKLOAD. Preserve serialized profile fields and reject the retired workload CLI without aliases.
- AC-002: Reviewed recipes select explicit course-owned per-lab batch files. Dispatch uses native sbatch with private scheduler directories, parsable job identity and existing write-ahead reconciliation; it does not call Python submission or profiler wrappers.
- AC-003: Freeze native-jobs/v2 with managed preparation binding and reject incompatible saved execution plans. Preserve historical inspection and owned cancellation without rewriting old evidence.
- AC-004: Collect and clean only exact dispatched job directories and their scheduler logs. Validate remote paths before copying, verify hashes, retain failed evidence and never adopt historical or unrelated output.
- AC-005: Keep dependency ordering, resource constraints, distributed/server evidence cardinality, independent verification, publication/browser gates and source/installed parity.

#### Negative Criteria

- NC-001: No whole-results-tree deletion, compatibility execution branch, automatic reuse of historical captures or loss of recovery safeguards.

#### Validation Method

Inspect recipe ownership, dispatch identity, saved-plan admission, private paths and canonical/installed parity.

#### Test Method

Use native scheduler/profiler doubles and campaign regression tests for argv boundaries, workload expansion, dispatch recovery, report counts, pre-copy admission and exact completed-job cleanup.

#### Evaluation Method

Keep source and local fixture evidence distinct from live target qualification and browser-reviewed publication.

<!-- /REQUIREMENT: REQ-006 -->

<!-- REQUIREMENT: REQ-007 status=satisfied priority=P1 type=feature -->
### REQ-007: Campaign-owned connection state and synchronization proof

#### User Story

A campaign operator needs automated synchronization without learner-facing bookkeeping or duplicate connection files.

#### Acceptance Criteria

- AC-001: Invoke sync-labs.sh with --sync-only, the campaign destination and prepared SSH settings, without a connection-receipt option or sync.json file.
- AC-002: Retain connection settings in the private campaign environment. Independently verify frozen remote source and workspace ownership before persisting run-labs-sync/v1 synchronization evidence.
- AC-003: Transfer or proof failure cannot mark a fresh campaign synchronized. Preserve retry, preflight and saved source/recipe validation.

#### Negative Criteria

- NC-001: Do not replace, migrate or delete historical connection files, add compatibility paths, or change monitoring, preflight, job or publication evidence receipts.

#### Validation Method

Inspect preparation, transport, campaign persistence, stage wiring and source/installed parity.

#### Test Method

Use local subprocess doubles to verify transfer arguments, independent source proof, successful persistence, failure propagation and absence of duplicate connection output.

#### Evaluation Method

Distinguish local fixture and installed parity checks from live cluster qualification.

<!-- /REQUIREMENT: REQ-007 -->

<!-- REQUIREMENT: REQ-008 status=satisfied priority=P1 type=feature -->
### REQ-008: Reuse the five-script managed preparation

#### User Story

A campaign operator can execute selected labs with the existing managed preparation and receives the exact required preparation command when a runtime is absent or stale.

#### Acceptance Criteria

- AC-001: Resolve all frozen executable stages through the course runtime bindings, including prerequisite, diagnostic and optional container launchers. Expose the owning preparation script and selection in dry-run plans without installing or connecting.
- AC-002: Preserve isolated course workspaces with valid course identities. Bind an existing prepared catalog root, defaulting to the remote courses directory or an explicit prepared_root, without copying installations, changing receipts or overriding runtime isolation.
- AC-003: Validate selected runtime receipts, status, fingerprints and artifacts before accepting preflight. Native jobs validate again on activation. Missing dependencies identify the exact preparation command and stop dispatch.
- AC-004: Keep preparation in the five scripts and learner instructions in the Lab Guide. Monitoring discovery uses the regular script monitoring action. Preserve hardware qualification, native jobs, source freezing, recovery, evidence and independent publication gates.
- AC-005: Reject earlier executable plans without rewriting history. Install the aligned skill through the documented project-local npx command and verify resource parity, retaining native host policy and all public actions.

#### Negative Criteria

- NC-001: No dependency installation, model download, container-default substitution, copied or forged runtime receipt, compatibility execution branch, live-cluster trial or automatic Git publication in this alignment.

#### Validation Method

Compare source and installed payloads; inspect actual launcher/runtime bindings, source fingerprints and frozen campaign inputs.

#### Test Method

Use deterministic runtime and campaign fixtures for all 110 recipe selections, optional launchers, missing/stale/skipped records, workspace identity, source drift, symlinks, preflight rejection, interruption and dry-run safety.

#### Evaluation Method

Run portable/Codex/Claude structural and target tests plus disposable npx parity. Keep trigger and comparative quality evidence separate from static checks and live execution.

<!-- /REQUIREMENT: REQ-008 -->

<!-- maintain-project-specs:requirements:end -->
<!-- markdownlint-enable MD001 MD024 -->
