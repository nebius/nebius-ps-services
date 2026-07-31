# Image Build Standard

Use this reference for Dockerfile or Containerfile work, build contexts,
BuildKit/buildx, layers, cache, dependencies, and final-image composition.

## Required Production Controls

- Produce an OCI-compatible image and add standard OCI annotations when the
  corresponding values are available.
- Use an official, vendor-maintained, or organization-approved base.
- Use an explicit base version. For release builds, record or pin the resolved
  digest and maintain an automated update path so digest pins do not freeze
  security fixes.
- Separate build tooling from the final runtime stage when compilation or
  dependency construction is required.
- Copy only runtime files, required libraries, certificates, timezone data,
  and deliberate diagnostics into the final stage.
- Use lock files and deterministic installation commands. Do not fabricate or
  update language lock files; route that work to the language owner.
- Maintain a context-specific `.dockerignore` that excludes source-control
  metadata, local environments, caches, test output, credentials, and
  secret-bearing environment files.
- Use BuildKit secret or SSH mounts. Never provide credentials through ARG,
  ENV, copied files, or deletion in a later layer.
- Order layers and use cache mounts without allowing cached dependency state to
  replace lock-file correctness.
- Remove package caches, build output, temporary credentials, tests, and other
  non-runtime content from the final stage.

## Dockerfile Rules

- Prefer JSON-array `ENTRYPOINT` and `CMD`.
- Use `COPY` for local build-context content. Use remote acquisition only when
  the source, integrity, network requirement, and cache behavior are explicit.
- Avoid large ownership-fixing layers; use `COPY --chown` where compatible.
- Keep build arguments non-secret and document whether changing them affects
  the resulting artifact.
- Do not add package managers, compilers, shells, or debugging tools to the
  final image without an operational reason.
- Do not select Alpine, distroless, or scratch solely for size. Confirm libc,
  certificates, timezones, native libraries, debugging, and framework support.

## Build Checks

- Detect whether the installed Docker/Buildx supports `docker build --check`;
  do not assume a particular version.
- Treat supported Dockerfile check warnings as findings and preserve the
  repository's configured severity policy.
- A successful build proves only that target on the selected builder platform.
  It does not prove runtime correctness or another architecture.

## Official Sources

- [Docker build best practices](https://docs.docker.com/build/building/best-practices/)
- [Docker build secrets](https://docs.docker.com/build/building/secrets/)
- [Docker build checks](https://docs.docker.com/build/checks/)
- [Dockerfile reference](https://docs.docker.com/reference/dockerfile/)
- [OCI image annotations](https://specs.opencontainers.org/image-spec/annotations/)
