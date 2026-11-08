# Baselines, execution and evidence

## Establish the boundary

Use the product's installer/migration contract and supported release metadata.
Installation, installed-image tests, upgrades, patches, state migrations and
named functional/integration suites are valid independent scopes. Containers are unsuitable for certifying another
kernel, boot chain, device driver, real hardware or production network behavior.
Do not turn a package-stage result into a claim about full deployment.

Default to Linux OCI userspace testing. Other container platforms require a
compatible runtime and product-specific verification; never silently translate
Windows or kernel-dependent installation into a Linux package check.

Freeze requirements before execution: product commands, supported paths,
assertions, service types, fault points, exclusions and resource/time budgets.
Prefer the existing harness's deadlines; otherwise start with 15 minutes per
case, 60 seconds startup and 10 seconds stability observation, adjusted from
product evidence before the trial. A later change creates a new plan identity.

## Select independent operations

With no mode flags, test the exact current project's source with suite auto.
Use compatible profile defaults, otherwise one unambiguous documented default;
ask for selection if absent, empty or ambiguous. Do not discover every test in a
monorepo. Read applicable ancestor instructions without changing project scope.
An explicit install builds/resolves the current project's target once per platform.
Source campaigns are separate from lifecycle campaigns. Combined test/lifecycle
modes require explicit installed target. Installed-target test starts from an
explicit image containing the software; absence fails instead of installing it.
Patch freezes source state, payload, workflow and postconditions even when its
version stays unchanged. Migration can transform data/configuration independently.

All-tests selects applicable lifecycle operations and their smoke/selected
suites. It adds standalone image testing only for an explicitly declared
preinstalled target. Missing lifecycle configuration is blocked; only a declared
non-applicability reason excludes it. A named suite must apply to each selected
case or selection fails. Do not silently drop requested coverage.

## Freeze authentic inputs

Resolve the base tag to its platform-specific immutable image digest. Record
target OS release, architecture, package repositories, installed package versions,
runtime/interpreter layout and relevant service defaults. Compare this with an
available target manifest; disclose differences instead of claiming exact image
equivalence. The official `ubuntu:24.04` image is Noble userspace, not a VM image.

Use authentic published artifacts and recorded hashes/signatures when available.
Record artifact provenance and release ordering. Source reconstruction is a
distinct evidence class: use the exact historical source/build inputs and label
it reconstructed. Installing an old artifact against today's repositories is not
proof of its original historical dependency environment. Retain observed resolved
versions; unavailable historical dependencies limit the claim.

Capture the target artifact's relevant working bytes, including dirty/untracked build
inputs, without resetting the user's tree. Build once per platform, identify the
source/build inputs and archive hashes, then reuse those exact bytes. Verify
downloaded artifact identity before execution. A version label or Git HEAD alone
does not identify a dirty build. Keep credentials out of build contexts/layers.

Fingerprint the entire reviewed execution bundle by relative path, bytes,
file and directory modes and symlink targets. Verify contained symlinks before use. Include
harness, assertions and fixtures so changed test code cannot masquerade as the
same campaign. Keep this identity separate from baseline cache identity.

Source suites also freeze test code, runner/dependency closure, harness and
selection. Use source_manifest.py with an explicit reviewed input list; ancestor
directory modes are part of identity. Verify the staged bundle again and prove
traversal by the actual restricted service/test user. Correct the staging owner
rather than weaken service sandboxing. Source environment caches include these
inputs and remain separate from authentic predecessor caches. Read
[suite evidence](suite-evidence.md) for independent collection and receipts.

## Baseline layering and speed

1. Shared layer: declared OS and truly common target prerequisites. Harness
   dependencies use a separate interpreter/location or external inspection.
2. Predecessor layer: that authentic release's actual dependencies, service files,
   representative synthetic configuration and saved state. Install through its
   historical supported entrypoint; independently verify the installed result.
3. Quiesce: stop all baseline writers and verify installed/state invariants before
   snapshotting. Reconstruct fixtures through historical supported serializers or
   interfaces; do not patch old product code to create apparently authentic state.
4. Trial: fresh container and fresh writable volumes from the verified predecessor;
   mount only reviewed artifact inputs read-only and isolated evidence output.
   Reset external fixtures too; container filesystem isolation does not reset
   shared volumes, databases or remote services.

Never install the union of past/new dependencies in the shared layer. Do not
construct predecessors by downgrading a candidate image. Shared archive/build
caches save download time without changing installed predecessor contents.

Cache identity covers base digest, platform, target profile, authentic artifact,
resolved dependencies, fixtures and setup recipe. Before reuse inspect immutable
image identity and re-probe installed state. The planner checks supplied metadata,
not image contents. New repository resolution creates a new image/manifest/key.
Keep caches free of secrets and bounded by explicit project retention limits.

Complete [runtime preflight](runtime-preflight.md) before expensive setup. Run
one planned representative sentinel first, then fan out only after its harness
and setup work. The sentinel counts once if it used the exact frozen contract.
A harness failure stops affected fanout; independent valid cases may continue.

`reuse` accepts only verified matching persistent baselines. `refresh` rebuilds
selected owned baselines. `off` neither reads nor writes persistent baselines but
permits campaign-local reuse; it does not promise empty engine build caches.

Do not reduce the matrix unless installation paths are demonstrably equivalent.
State/configuration variants still need their distinct assertions. Declare all
combinations or explicit exclusions; show count before execution. Sequential paths
need intermediate artifacts and do not prove direct-upgrade support.

## Execution and independent checks

Invoke production-owned dependency selection, replacement, unit/config asset
installation and migration steps. If only an internal package stage is safely
testable, call that existing boundary and describe the restricted result. Avoid
creating a second installer that merely resembles production behavior.

Probe installed executable/module paths with source-tree imports disabled.
Inspect package-manager state, versions/files, dependency consistency, service
ownership/permissions and obsolete assets. Treat expected user-modified config
separately from unexpected file drift. Never mutate state from a verifier that
is supposed to observe it.

For migrations, seed synthetic old-format state, execute the product migration,
then independently assert target schema/version and meaningful invariants:
preserved records/values, renamed fields, permissions and idempotent repeat.
A service that starts after silently losing data has not passed migration.
Do not require a fictional package predecessor for a pure state migration.
For patches, independently check intended deltas and preserved invariants;
unchanged version strings do not identify either input or successful application.
For lifecycle/installed targets, run selected suites against actual installed/changed software with their frozen
assertions. Preserve fixture setup and product workflow as distinct phases.

For services, use the real manager and freeze the expected type/state: a daemon
must remain running without unexpected restarts, while a one-shot may correctly
finish inactive with successful exit. Verify the effective installed definition,
actual executable and startup evidence. Required unavailable external dependencies
make startup incomplete; stubs or altered ordering narrow the claim explicitly.

Repeat the real upgrade selection path. Record safe convergence separately from
mutation-free no-op. The latter requires zero criterion-relevant mutations, not merely equal
version strings or end-state values after rewriting the same content.

## Interruption and recovery

Define observable boundaries such as unpack-complete, before configure, after
state-version commit or before service asset replacement. Use an existing test
hook or bounded supervisor with evidence that the boundary occurred. Avoid sleeps
as proof of a precise fault point. Each fault trial starts from its own baseline.

Observe the interrupted state, then use the product's supported recovery with
the original operation/artifacts where required. Record durable progress and
postconditions independently. Manual package repair, edited checkpoints, fixture
writes after divergence or bypassed startup gates contaminate the affected claim.
Repair the harness/product at its owner and rerun a fresh trial; retain failures.
Do not invent rollback support or claim general crash safety from one fault.

## Lifecycle and reporting

Track exact container/image/volume/process identities and immutable campaign/case
labels. Separate setup, execution, recovery and verification. Stop only owned
writers, collect sanitized evidence, then remove exact owned disposable resources.
Never use global prune. Retain failed resources only by declared policy, with a
location, owner and cleanup deadline. Do not retain secret-bearing fixtures.

Record cold setup, warm-cache execution, per-case time, cache hits and executed
case count separately. No speedup claim without measured comparable runs.
Missing/blocked checks stay visible; no retroactive exclusions or overwritten
failed evidence. A helper summary is a consistency check over supplied receipts,
not an attestation or a substitute for independent runtime inspection.
