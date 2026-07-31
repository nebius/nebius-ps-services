# Runtime Standard

Use this reference for process behavior, identity, signals, configuration,
filesystems, health, storage, logging, networking, and resources.

## Process And Identity

- Run as a known numeric non-root UID/GID unless root is demonstrably required.
  Record any exception and the smallest required root phase.
- Ensure PID 1 forwards signals and reaps children when the application starts
  subprocesses. Add a lightweight init only when the process cannot satisfy
  that contract itself.
- Prefer exec-form entrypoint and command definitions.
- On SIGTERM, stop accepting new work, complete bounded in-flight work, release
  resources, and exit before the declared grace period.
- Keep one operational responsibility per container without enforcing an
  inflexible one-process rule.

## Configuration And Secrets

- Inject environment-specific configuration at runtime.
- Document required and optional variable names, validation, and reload
  behavior without persisting sensitive values.
- Mount or inject secrets at runtime. Do not bake them into the image, Compose
  files, committed `.env` files, or default environment values.

## Filesystem And State

- Identify every writable, temporary, socket, cache, and persistent path plus
  its UID/GID and permissions.
- Store durable state in a volume or external service.
- Make the root filesystem read-only where practical. Back deliberate writes
  with tmpfs, `emptyDir`, or persistent storage as appropriate.
- Test volume ownership using the declared numeric identity rather than
  relying on a username lookup.

## Health And Ports

- Define startup, readiness, and liveness separately even when Docker consumes
  a single image `HEALTHCHECK`.
- A readiness failure removes the workload from service; a liveness failure
  asserts that restart is appropriate. Do not conflate dependency availability
  with process liveness.
- Document port number, protocol, name, and purpose. `EXPOSE` documents image
  intent and does not publish a host port.

## Logging, Networking, And Resources

- Write application logs to stdout and stderr. Keep rotation and collection
  outside the image unless a documented runtime owns them.
- Record required ingress, egress, DNS, proxies, certificates, and dependency
  endpoints without embedding private addresses.
- Measure startup peak and steady-state CPU/memory behavior. Record known OOM
  characteristics and concurrency assumptions; do not invent limits.

## Conditional Hardening

- Drop all capabilities and add back only demonstrated requirements.
- Disable privilege escalation.
- Use the runtime-default seccomp profile unless a documented custom profile is
  required.
- Avoid privileged mode and host PID, IPC, and network namespaces.
- Keep read-only-root, tmpfs, rootless engine, user namespace, init, and
  `HEALTHCHECK` decisions aligned with the target runtime.

## Official Sources

- [Dockerfile command and signal behavior](https://docs.docker.com/reference/dockerfile/)
- [Docker runtime security](https://docs.docker.com/engine/security/)
- [Kubernetes probes](https://kubernetes.io/docs/concepts/workloads/pods/probes/)
- [Kubernetes resource management](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/)
