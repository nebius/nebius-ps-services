# Engine And Host Standard

Use this reference for Docker Engine, Docker Desktop, rootless mode, daemon
configuration, host storage/logging, Podman, containerd, or another OCI engine.

## Engine Boundary

- Docker/Buildx/Compose is the executable implementation of this skill.
- OCI compatibility is a portable artifact goal, not a claim of command-line
  parity across Docker, Podman, containerd, nerdctl, or CRI runtimes.
- Preserve the repository's selected engine and file conventions unless a
  migration is explicitly requested.
- Detect installed commands and capabilities before recommending flags.

## Host Contract

Record:

- host OS and CPU architecture;
- engine, client, daemon, builder, and Compose versions when available;
- rootful or rootless mode;
- active builder and supported platforms;
- storage and logging drivers when relevant;
- proxy, certificate, DNS, and registry configuration requirements;
- available resource ceilings and virtualization/emulation boundaries.

Do not copy daemon configuration, credential helpers, auth files, private
registries, or host-specific paths into reusable outputs.

## Rootless And User Namespaces

Running the image process as non-root and running a rootless engine are
different controls. Assess them separately.

Use rootless engines or user namespaces where supported and compatible with
ports, cgroups, storage drivers, devices, and volume ownership. Do not change
daemon configuration or enable these features without explicit authorization.

## Host Safety

- Never run `docker system prune` or broad image/container deletion.
- Never mount the Docker socket into a test container.
- Never change daemon settings, credential stores, registries, builders, or
  host networking automatically.
- Clean only uniquely labeled task-created containers and explicitly
  task-created image tags after verifying ownership.

## Official Sources

- [Docker Engine security](https://docs.docker.com/engine/security/)
- [Docker rootless mode](https://docs.docker.com/engine/security/rootless/)
- [OCI runtime specification](https://specs.opencontainers.org/runtime-spec/)
