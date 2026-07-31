# Workload Profiles

Classify the workload before choosing image or runtime controls. Record one
primary profile and any applicable modifiers.

## Development

- Optimize for source bind mounts, hot reload, debuggability, and short
  feedback cycles.
- Keep development tools and relaxed image-size expectations out of the
  production target.
- Treat mounted source and developer credentials as local-only interfaces.

## Production Service

- Long-running API or daemon with explicit ports, startup/readiness/liveness
  semantics, structured stdout/stderr logs, and bounded graceful shutdown.
- Stop accepting work before completing bounded in-flight operations.
- Record restart expectations without baking an orchestrator policy into the
  image.

## Worker

- Long-running background consumer with no port unless it exposes a deliberate
  health or metrics interface.
- Define queue disconnection, bounded task completion, retry ownership, and
  termination semantics.

## Job

- Finite execution with deterministic exit status.
- Define idempotency, retryable versus terminal failures, output durability,
  and the effect of interruption.
- Do not configure unconditional restarts.

## Migration

- Finite administrative operation with exclusive-execution requirements,
  credential interfaces, failure recovery, and rollback expectations.
- Keep migration execution separate from ordinary service startup unless the
  approved architecture explicitly couples them.

## Stateful

- Identify durable paths, required ownership, backup and restore behavior,
  upgrade compatibility, recovery objectives, and corruption handling.
- Never treat the container writable layer as durable storage.

## GPU

- Record CPU architecture, target GPU model or compute capability, full-GPU or
  MIG allocation, host-driver/runtime-library compatibility, shared memory,
  IPC, and CDI/device interfaces.
- Load [gpu-containers.md](gpu-containers.md) and require an actual framework or
  application GPU operation for runtime evidence.

## Development Tool

- Builders, test runners, code generators, and disposable utilities are not
  production runtime images.
- Define input/output mounts, network needs, cache ownership, and deterministic
  exit behavior.

## Classification Output

Return the profile, production intent, expected lifetime, process model,
external dependencies, state model, platform/device requirements, and the
profile-specific controls that apply or do not apply.
