<!-- markdownlint-disable MD001 MD024 -->
<!-- maintain-project-specs:design:start schema=maintain-project-specs/design-v2 -->
# Project Design

<!-- FEATURE: FEAT-001 reqs=REQ-001 status=ready delivery=verified priority=P0 version=1 -->
### FEAT-001: Workstation entry and automated sync distinction

#### Requirements Covered

- REQ-001: Opening workstation login guidance.

#### Context Evidence

`SKILL.md` starts with environment resolution; `references/environment.md` describes noninteractive access. The course `sync-labs.sh` already implements interactive handoff and `scripts/prepare.py` owns campaign sync-only.

#### Design Details

Add the interactive command to workflow step 1 after resolving the accepted target. Explain workstation execution, remote default directory and account selection. Keep the controller on the workstation and defer its sync-only path to stage 3, with no mandatory manual shell.

#### Selected Option

Clarify the existing script contract in the skill entrypoint; this exposes the human login path without modifying transport or campaign state.

#### Alternatives Considered

Making interactive SSH mandatory would block noninteractive agents. Adding another login helper would duplicate the existing script.

#### Implementation Boundaries

Only skill source guidance, target-owned evals and this contract pair change. Preserve scripts, metadata, campaign claims, preflight, recovery and outputs. No installation is part of this delivery.

#### Test-First Success Criteria

- TDD-001: The baseline lacks the opening interactive command; existing script tests already specify account selection and terminal behavior.

#### Validation Plan

Run portable, Codex and Claude static structure checks with canonical evals; validate the paired specifications and compare source and installed-file manifests.

#### Test Plan

Run selected existing sync-labs fixture tests covering target selection, interactive handoff, sync-only and dry-run.

#### Evaluation Plan

Add a workstation-start case and an automation/dry-run boundary case. Review-only code and security lanes assess the changed source; native runtime and quality runs remain separate evidence.

#### Rollout And Rollback

Deliver source edits for a later user-controlled installation. Revert only this focused change if needed; preserve unrelated working changes.

#### Done Definition

Guidance and evaluation definitions match source behavior, static checks pass, and installed files remain byte-identical.

#### Implementation Evidence

`SKILL.md` step 1 documents the workstation command, remote directory and account selection while preserving automated sync-only and non-mutating request boundaries. Target trigger and quality cases cover workstation entry, login-only routing and dry-run behavior. No scripts or host metadata changed.

#### Verification Evidence

Portable, Codex and Claude structural checks passed, including standard fields, strict frontmatter and canonical eval definitions (11 trigger cases; 8 quality cases). Repository Markdown lint passed. Existing local sync fixtures: 19 passed, 76 deselected, covering account selection, interactive handoff, login-directory failure, sync-only and dry-run. Independent read-only code and security reviews found no blocking issues. Installed payload hashes stayed unchanged. Static validators report the intentional project-contract docs directory as non-canonical. Native trigger, comparative quality, npx installation and live-cluster validation were not run; this verification covers the source clarification only.

<!-- /FEATURE: FEAT-001 -->

<!-- FEATURE: FEAT-002 reqs=REQ-002 status=ready delivery=verified priority=P1 version=1 -->
### FEAT-002: Prepared-runtime preflight boundary

#### Requirements Covered

- REQ-002: Prepared monitoring runtime qualification.

#### Context Evidence

The campaign environment and preflight schemas already delegate prerequisite observation to the execution agent. The current setup helper separately validates managed deployment artifacts and writes v2 connection files; the publisher consumes explicit prepared connections rather than a setup receipt.

#### Design Details

Clarify the preflight reference and entrypoint. Always independently verify current target, service/backend identity, private routes, one healthy scrape with label preservation, complete fresh GPU telemetry and intended local write/read routing. Use current course checks for the invariants they cover and retain independent proof for remaining prepared-runtime observations. Do not claim setup qualification for a prepared runtime. Keep setup discovery, receipt schemas and infrastructure ownership unchanged. Apply exact publisher generation/readback and browser gates later.

#### Selected Option

Clarify existing agent-owned preflight without adding a second controller or receipt compatibility layer.

#### Alternatives Considered

Reinstalling every prepared target would broaden lab execution. Accepting obsolete setup schemas would weaken the setup contract.

#### Implementation Boundaries

Skill instructions, references, evaluation cases and paired documentation only. No controller schema, recipes, course correctness, deployment product source or infrastructure changes.

#### Test-First Success Criteria

- TDD-001: Review cases distinguish a healthy prepared runtime from failed new setup discovery and reject unproven telemetry or routing.

#### Validation Plan

Check references, Markdown, canonical specs, source/installed parity and changed-scope review.

#### Test Plan

Use existing campaign preflight and frozen-input regression checks. Do not create tests that merely mirror prose.

#### Evaluation Plan

Add a prepared-runtime resume case preserving local-only routing and all runtime gates.

#### Rollout And Rollback

Update source first, refresh the project-scoped installation, record its revision and preserve unchanged campaign inputs. Revert only these scoped instruction edits if necessary.

#### Done Definition

Guidance and installed source agree; reported runtime evidence is independent and source/setup claims remain bounded.

#### Implementation Evidence

The entrypoint and environment reference distinguish independent prepared-runtime qualification from new setup discovery. The quality and trigger cases require local-only routing, effective scrape and backend proof, full fresh telemetry, later publisher readback and unchanged frozen campaign inputs.

#### Verification Evidence

The 64 focused controller and evidence-runner tests pass. Canonical evaluation JSON and trigger CSV parse, and the Codex structural validator passes with the intentional project-contract docs warning. Read-only review found no serious source-guidance issue. The project-scoped Skills CLI installation succeeded. This verifies source guidance and existing controller gates; it does not claim all-course completion or successful new setup discovery on an older prepared deployment.

<!-- /FEATURE: FEAT-002 -->

<!-- FEATURE: FEAT-003 reqs=REQ-003 status=ready delivery=verified priority=P1 version=2 -->
### FEAT-003: Retain capstone memcheck and child evidence

#### Requirements Covered

- REQ-003: CUDA capstone safety prerequisite.

#### Context Evidence

The CUDA Lab 12 guide invokes sanitizer.sbatch before three fresh processes. Its reviewed recipe currently omits that dependency. The controller already dispatches dependencies and requires verification for every dispatched job; the evidence runner already supports child result indices.

#### Design Details

Add the exact guide memcheck command as a recipe dependency. Reuse profile expansion, existing single-allocation scheduling and every-job verification. Clarify that independent verification must inspect zero-error sanitizer output and independently recompute the original three-trial aggregate. Capture paired child indices covering all originals through the existing publisher; do not create synthetic aggregate results.

#### Selected Option

Use the existing dependency stage and result-index evidence interfaces.

#### Alternatives Considered

A new sanitizer stage kind or another publisher would duplicate existing execution and evidence owners. Omitting the guide's safety gate would leave the lab incomplete.

#### Implementation Boundaries

Recipe, execution guidance, tests and paired specifications only. The course owns its aggregate hardware identity repair. No deployment source, infrastructure change, compiler installation or acceptance-timing substitution.

#### Test-First Success Criteria

- TDD-001: The current recipe fails a test requiring memcheck before trials for both profiles; preserve three acceptance children.

#### Validation Plan

Run focused controller/evidence tests, skill structure, source/installed parity, source review and security checks.

#### Test Plan

Check exact dependency order, executable/profile binding and existing all-job verification rejection; retain multi-result coverage regressions.

#### Evaluation Plan

Review required zero-error and aggregate observations against the guide. Live completion remains a separate campaign gate.

#### Rollout And Rollback

Finish the unchanged prior campaign first, refresh the source-owned installation, then create a new frozen campaign. Revert only this change if required; preserve earlier evidence.

#### Done Definition

The recipe retains memcheck, verification guidance preserves all children and safety evidence, and focused checks plus installation parity pass.

#### Implementation Evidence

The reviewed CUDA capstone recipe now invokes the supplied memcheck launcher as a dependency. Execution guidance requires zero-error diagnostics, independent aggregate math, observed hardware identity and complete child metric coverage; the quality case exercises these boundaries.

#### Verification Evidence

Before repair, eight focused cases failed for omitted dependency or incorrect/missing aggregate GPU identity. After repair, 210 selected controller, evidence-runner and course review checks pass. One unrelated PyTorch-dependent case was excluded because the local validation environment lacks PyTorch. Shell syntax, ShellCheck, Ruff, Markdown, course validator, generated HTML and skill structure checks pass. Read-only code/security review found no serious issue. The project-scoped installation succeeded and 26 source/installed files matched. This established source verification before the subsequent live checks below.

Subsequent prepared-target H200 verification completed CUDA Labs 11 through 13 for both profiles. The capstone retained its separate zero-error memcheck, all three counterbalanced unprofiled children, independently checked aggregate math and observed GPU identity, both original native reports, and all child metrics across two publication generations. Actual Grafana, Systems and Compute images were inspected and hash-bound before export; artifact hashes, sanitized contents and ZIP bytes matched. The qualification lab retained two fresh equivalent runs per profile and all three metric views. Original timing samples and output arrays were not retained, application integration remains pending, and full-catalog completion is not claimed.

<!-- /FEATURE: FEAT-003 -->

<!-- FEATURE: FEAT-004 reqs=REQ-004 status=ready delivery=verified priority=P1 version=3 -->
### FEAT-004: Verify the host-copy mechanism

#### Requirements Covered

- REQ-004: Native host-transfer evidence.

#### Context Evidence

The frozen Lab 03 source executes four groups of five warmup and twenty measured host-to-device copies, synchronizing every measured iteration, followed by one payload-sized device-to-host correctness readback. Its original Systems reports contain copy activity and a lab_workload range with no CUDA kernel; the generic validator rejects them solely for the empty kernel list.

#### Design Details

Route every Fundamentals Lab 03 report through a dedicated host_copy validator before generic kernel admission. Bind schema, Systems stage, job, rank-0 producer, effective explicit size and profile, one GPU/process/context/stream and the workload interval. Require four ordered pageable/pinned blocking/nonblocking groups with 25 successful correlated logical submissions/completions each and exact byte totals. Count logical copies independently from native activity segments. Verify successful synchronization after the warmup group and each measured copy, plus the blocking stream synchronizations. Require a final complete payload readback after all groups. The independent family verifier derives the proof from report-owned memory-kind labels and raw API/copy correlations, preserving report/query hashes and warnings. Require matching_host_copy in visual reviews for both configurations; the existing evidence runner consumes the validator's returned check without a second workflow.

#### Selected Option

Add a lab-specific mechanism validator inside the existing native evidence owner, preserving the frozen recipe and original GPU work.

#### Alternatives Considered

A blanket empty-kernel exemption would admit initialization-only captures. Adding dummy kernels or changing profiling would alter the experiment. Requiring raw activity count to equal logical copy count would reject valid segmented transfers.

#### Implementation Boundaries

Native validation, focused tests, evidence guidance, evaluation cases and paired specifications. No course workload, recipe, monitoring, deployment or public CLI change.

#### Test-First Success Criteria

- TDD-001: A complete copy-only fixture fails before repair and passes afterward; missing or corrupt proof fails even with incidental kernels.
- TDD-002: Generic kernel visual approval fails for Lab 03; only valid copy proof and the exact mechanism review can reach export validation.

#### Validation Plan

Check source tests, lint, public-safe docs, source/installed parity and independent review. Replay the unchanged campaign from verification after all owned GPU jobs are terminal.

#### Test Plan

Cover both sizes and profiles, strict integer checks, complete modes, byte totals, exact identity, ordered workload/readback bounds, synchronization and review/hash gates.

#### Evaluation Plan

Review a copy-only acceptance case and reject partial or substituted transfer evidence. Native target and browser proof remain separate from fixture validation.

#### Rollout And Rollback

Refresh the source-owned skill installation, record its revision separately and continue from the unchanged verification checkpoint. Revert only the scoped source repair if necessary; preserve failed evidence and original jobs.

#### Done Definition

The canonical validator and runner reject malformed transfer evidence and accept independently verified original Lab 03 copies; actual views and exported hashes remain mandatory.

#### Implementation Evidence

The native owner now routes every Lab 03 capture through exact host-copy validation before generic kernel checks. Focused tests cover copy-only and incidental-kernel cases, corrupted identity/count/size/timing/type evidence, mandatory mechanism-specific reviews, report/query/image hashes, PNG validation and both configuration views. Guidance and the quality case document the complete independent copy/synchronization proof and its limits.

#### Verification Evidence

The pre-repair regression produced 23 expected failures. The controller/evidence-runner suites pass 109 tests after repair; a further process/thread identity negative control failed before its check was added and now passes. Scoped Ruff, Markdown, canonical specs and skill structure checks pass, with only the existing intentional docs-folder warning. Independent source review found no material defect. Original target reports pass the source-bound raw-row qualifier and eight corrupted copies are rejected. The maintained and project-installed skill now complete Fundamentals Lab 03 in both workload profiles on a prepared H200 target. Eight exact jobs completed successfully; four original Systems reports independently prove all four ordered transfer modes, successful correlations, complete byte totals, synchronization and final readback. Original files, native query proofs and frozen source hashes were rechecked after jobs became quiescent. Four actual Grafana tables match all 32 displayed values, including verified SI display conversion; four actual Systems views match the selected measured copies and surrounding workload ranges. Both exports contain the two original results, four reviewed PNGs, a 16-row summary and manifest; checksums, review joins, privacy projections and ZIP bytes pass. This verifies the host-copy evidence repair, not full-catalog completion. Diagnostic warnings remain retained; per-mode full payloads and raw timing samples are absent, and no overlap or equal-work speedup is claimed.

<!-- /FEATURE: FEAT-004 -->

<!-- FEATURE: FEAT-005 reqs=REQ-005 status=ready delivery=verified priority=P1 version=1 -->
### FEAT-005: Freeze diagnostic stage memory

#### Requirements Covered

- REQ-005: Scoped diagnostic resource requests.

#### Context Evidence

Inference Lab 10 rejects captures lacking --in-process. Its guide and profiler templates contain the flag, but its explicit Systems stages omit it. The guide requests additional host RAM only for Compute; catalog.actions currently replaces any stage environment with prepared variables.

#### Design Details

Retain each executable recipe item's optional environment mapping. Admit only SBATCH_MEM_PER_NODE with a positive decimal string in MiB, rejecting all other keys and malformed values before freezing. Merge prepared/profile variables, then the reviewed stage mapping, then the controller-owned workload profile into a fresh stage dictionary. Add --in-process to both Systems variants and 262144 MiB only to Compute. Existing transport passes that frozen mapping to the submission process; no new shell wrapper or runtime interface is needed.

#### Selected Option

Use a narrowly validated recipe stage environment through the existing controller and transport.

#### Alternatives Considered

A global memory override would affect clean measurements. A shell prefix would violate canonical launcher ownership. Arbitrary recipe environment variables would add unnecessary authority.

#### Implementation Boundaries

Catalog, reviewed recipe, focused tests and owning guidance/specifications only. Preserve course algorithms, clean parameters, native-report gates, Slurm target authority and deployment source.

#### Test-First Success Criteria

- TDD-001: Both profile plans retain capture flags and scope additional memory only to Compute.
- TDD-002: Invalid mappings fail before submission; repeated expansion preserves inputs and stage isolation.

#### Validation Plan

Run focused controller and vLLM capture tests, source review, lint, course validators and installed parity.

#### Test Plan

Cover positive decimal MiB, zero, numeric rather than string values, signs, suffixes, whitespace, newlines, unknown/secret/runtime keys, both profiles and prepared-default precedence.

#### Evaluation Plan

Check frozen plan and observed Slurm memory independently before accepting native evidence. Static tests alone do not prove profiler completion.

#### Rollout And Rollback

Finish prior frozen campaigns, repair source and refresh the project-scoped installation, then create a fresh campaign. Preserve prior evidence; revert only scoped edits if needed.

#### Done Definition

Guide and executable recipes agree; stage memory is validated and isolated; focused checks and installation parity pass.

#### Implementation Evidence

The canonical catalog validates the narrow stage environment before freezing, preserves executable item mappings, and merges each into a separate stage. Lab 10 Systems variants now retain --in-process; Compute alone declares 262144 MiB. Public execution guidance, course README and changelog explain the distinction.

#### Verification Evidence

Before repair, 23 focused cases failed for omitted flags/memory, lost precedence or accepted invalid mappings. After repair, 178 controller, evidence-runner, collection and vLLM capture tests pass; scoped Ruff passes. Source review confirms existing transport preserves the frozen stage mapping and no shell wrapper, secret/runtime override or allocation change leaks into clean jobs. Installed parity and live allocation/report verification are separate subsequent gates.

<!-- /FEATURE: FEAT-005 -->
<!-- FEATURE: FEAT-006 reqs=REQ-006 status=ready delivery=verified priority=P1 version=2 -->
### FEAT-006: Native dispatch with preserved campaign recovery

#### Requirements Covered

- REQ-006: Native course jobs and exact-job evidence ownership.

#### Context Evidence

The approved course refactor replaces generic Python execution wrappers with explicit per-lab native jobs, separates workload size from diagnostic mode, and introduces private per-job output directories. Existing campaign orchestration still owns scheduling, durable intent, uncertain dispatch reconciliation and evidence verification.

#### Design Details

Recipes name reviewed course-owned batch files. The catalog freezes native-jobs/v1 and expands workload sizes into existing serialized profile identities. Native submission prepares private logs/jobs parents and executes sbatch --parsable with explicit working directory and scheduler paths. It validates launcher containment and narrow scheduler overrides before dispatch. The existing intent/receipt lifecycle owns retries and exact job identity; incompatible saved plans cannot execute. Status and owned cancellation remain available for historical state.

Collection inventories only exact dispatched job subtrees and scheduler logs, rejects traversal, NUL separators, symlinks and unowned paths before rsync, and verifies returned hashes. Cleanup requires completed successful dispatches and removes only their exact runtime outputs. Published history and unrelated jobs stay outside collection and cleanup. Explicit profiler jobs preserve server/rank/vendor coordination and report cardinality. Source instructions, recipes and installed payload use the same contract.

#### Selected Option

Share explicit native lab jobs between learner commands and the existing durable campaign controller.

#### Alternatives Considered

A replacement generic dispatcher would hide commands again. Removing campaign orchestration would lose recovery and evidence guarantees. Reusing historical result trees would break producing-job identity.

#### Implementation Boundaries

Canonical run-labs instructions, recipes, catalog, native submission, collection, cleanup, saved-plan validation, tests and project-installed payload. No live cluster changes, infrastructure installation or evidence publication.

#### Test-First Success Criteria

- TDD-001: Native argv and both workloads reach the reviewed jobs; retired wrappers and incompatible executable plans fail.
- TDD-002: Unowned paths fail before copying, and completed-job cleanup leaves history and other jobs unchanged.
- TDD-003: Existing dispatch reconciliation, dependency, verification and evidence gates continue passing.

#### Validation Plan

Run controller/evidence regressions and native command doubles, inspect source and installed parity, and validate canonical specs.

#### Test Plan

Exercise workload defaults, scheduler override restrictions, path ownership, job identities, report failures, server export waits, distributed producers and collection/cleanup boundaries.

#### Evaluation Plan

Compare frozen recipes with actual native job commands. Require future live campaigns to establish target runtime and browser evidence independently.

#### Rollout And Rollback

Refresh the project installation from canonical source and create fresh native campaigns. Keep old receipts/results readable; do not translate saved executable plans or rewrite original evidence. Revert only scoped source edits if needed.

#### Done Definition

Native dispatch and exact-job evidence ownership are locally verified with preserved campaign safeguards and identical installed payload.

#### Implementation Evidence

The native-jobs/v1 catalog, direct sbatch submission, workload CLI, safe pre-copy admission, exact-job cleanup and incompatible-plan rejection are implemented. Instructions and recipes select explicit native jobs. Historical specification records remain provenance; this design supersedes their generic-launcher assumptions without discarding their evidence.

#### Verification Evidence

The complete courses suite passes 2150 tests, including native execution, campaign, collection, saved-plan and recovery regressions. Native process tests check server shutdown/export, failure propagation and rank/report completeness. Changed Python lint, all course validators and source/installed parity pass. All 2,872 original course evidence files remain unchanged. No live cluster or published result replacement was performed; target execution and browser evidence gates remain mandatory for future campaigns.

Subsequent alignment clarifies that collection includes only dispatched job trees and their scheduler logs. The full courses suite now passes 2165 tests; independent dispatch/collection/cleanup review found no material defect and canonical/installed payloads remain identical. Course-side history copying is bounded and binds attribution metadata to copied bytes. Existing evidence is preserved; no live campaign was run.

<!-- /FEATURE: FEAT-006 -->

<!-- maintain-project-specs:design:end -->
<!-- markdownlint-enable MD001 MD024 -->
