# Kubernetes And Helm Handoff Contract

The container skill must not create Kubernetes or Helm resources. Return this
contract to `$helmchart`.

## Image

- Repository, release tag, resolved digest, and supported platforms.
- Platforms actually runtime-tested.
- Pull/authentication interface without credential values.

## Process

- Entrypoint, arguments, working directory, UID, GID, stop signal, PID 1
  behavior, and maximum graceful-shutdown duration.

## Ports And Health

- Port number, protocol, name, and purpose.
- Startup, readiness, and liveness method, path or command, expected result,
  timing assumptions, and dependency behavior.

## Configuration And Secrets

- Required and optional environment variable names.
- Secret names and expected environment or mount interface, never values.
- Reload behavior and failure behavior for missing configuration.

## Filesystem And Storage

- Read-only-root compatibility.
- Writable, temporary, socket, cache, and persistent paths.
- UID/GID, modes, size expectations, backup, and recovery requirements.

## Security

- Required capabilities, seccomp assumptions, privilege requirements, user
  namespace compatibility, and whether privilege escalation is needed.
- Every non-default requirement must include its reason.

## Resources And Networking

- Measured baseline, startup peak, steady state, concurrency assumptions, and
  known OOM behavior.
- Required ingress, egress, DNS, proxy, certificate, and dependency interfaces.

## Lifecycle And Devices

- Startup ordering, pre-stop behavior, retry ownership, and maximum shutdown.
- GPU, MIG, shared memory, huge pages, IPC, or other device requirements.

`$helmchart` owns probes, security contexts, volumes, image references,
resource requests/limits, lifecycle hooks, scheduling, and other Kubernetes
resources derived from this contract.

## Official Sources

- [Kubernetes images](https://kubernetes.io/docs/concepts/containers/images/)
- [Kubernetes probes](https://kubernetes.io/docs/concepts/workloads/pods/probes/)
- [Kubernetes security contexts](https://kubernetes.io/docs/tasks/configure-pod-container/security-context/)
- [Kubernetes resource management](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/)
