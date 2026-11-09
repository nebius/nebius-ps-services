# Runtime preflight and fast execution

Preflight early, before building every historical image. It diagnoses whether
selected trials can run; it does not prove the product operation passed.

## Three capability boundaries

1. Controller: inspect required tools, compatible versions, execution context,
   remote transport if already configured, output access and free disk. Check
   available/reclaimable memory using the host's safety policy. Keep harness
   dependencies separate from product interpreters and package ownership.
2. Runtime host: confirm engine reachability, target platform and actual
   native/emulated execution, memory/disk headroom, cgroups, mounts, networking,
   service-manager support and disposable resource limits. A healthy laptop does
   not prove a remote worker or desktop Linux VM has enough headroom.
3. Product: confirm authentic artifacts and historical runtime requirements,
   interpreter layout, service-manager expectations, supported workflow and
   representative source fixtures. Failures in this layer are not engine failures.

Use bounded disposable probes, exact resource names and deadlines. Plan-only
performs no pull, build, container execution or lifecycle operation, test collection or suite execution. Preflight-only
may resolve/pull required images and run minimal tooling/runtime probes within
existing authority, but never collect tests, execute suites, install, upgrade, patch or migrate the product.
It is exclusive with plan-only. Explicit unavailable engines block; auto chooses
project policy first, then compatible Docker, then Podman, and freezes the choice.

## Select the runtime class

Use ordinary process containers for ordinary CLI/API/source tests; real service
manager containers for unit/startup behavior; and a capable kernel environment
for tests that need specific kernel features. Ubuntu userspace does not supply
an absent host kernel module. Declare and probe precise capabilities; missing
required capabilities block that lane. Do not turn a suite name into a universal
privileged runtime recipe. Any necessary narrow capability exception needs the
existing task's authority and a recorded scope.

Project/profile/artifact target declarations take precedence. Only source-only
runs with no declared target may use the observed engine-native platform. A remote
engine or Docker Desktop VM can differ from the controller. Unknown/unreachable
engines block resolution. Record native/emulated execution; emulation is not
native performance evidence. A project test Dockerfile is harness metadata to
build and resolve during a full run, never an image reference or assumed production
Dockerfile. CLI image overrides defaults, but not explicit environment bindings.

## Service-manager capability

A shell container starting successfully does not prove systemd can run. Use a
small probe with the actual manager as PID 1, delegated writable cgroups and
reviewed mounts. Collect its exit status and diagnostics before cleanup.
Docker `--init` is not systemd. Fake systemctl responses cannot establish startup.
Do not repeatedly rebuild predecessor images to repair a runtime cgroup failure.
Do not default to privileged mode, host namespaces or host security changes.

If confined service startup is unavailable, retain independently valid package
checks and mark required startup evidence incomplete. A reduced package-only
scope must be declared as a separate trial, not retroactively called full success.
See [container checks](container-checks.md) for official references.

## Historical package ownership

Inspect actual interpreter and installation layout before choosing dependency
commands. A successful import from the source checkout is not an installed probe.
OS-owned packages and Python-installed distributions can have different ownership
and uninstall metadata. Record the observed conflict and fix the product-owned
upgrade path; do not silently add blanket overwrite/ignore-installed switches.
Such switches can hide file ownership and stale-file problems.

Keep shared bases minimal. Preinstalling new runtime packages or the union of all
release dependencies masks whether historical upgrades select dependencies
correctly. Rebuild only the affected baseline recipe when its owner changes.

## Diagnosable, bounded speed

Freeze a complete bundle once, build each required artifact once per platform,
and use verified baseline images with fresh writable state for every case.
Check bundle identity at execution; changing harness files creates a new campaign
identity but need not invalidate baseline images whose recipes remain unchanged.

Run a representative planned sentinel, then bounded fanout (default maximum two
cases). Include suite-internal concurrency in resource planning. Use product
harness timeouts where defined; otherwise start with 15 minutes per case,
60 seconds startup and 10 seconds service stability, adjusted before execution.
Track preflight, preparation, operation, verification, recovery and cleanup time.
Compare like-for-like cold/warm runs before claiming a speed improvement.

On failure, collect bounded sanitized engine/manager/product diagnostics first.
Distinguish tooling/runtime limitations, fixture defects and product assertions.
Repair the causal owner and rerun fresh from a known-good checkpoint before the
first divergence/intervention. Retain earlier failed evidence. Stop exactly owned
writers before snapshot or cleanup; never prune unrelated containers or caches.

These patterns are public, product-neutral lessons. They do not require a cloud,
a particular product, a remote worker or a new universal container runner.
