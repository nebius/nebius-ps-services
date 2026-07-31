# Multi-Platform Standard

Use this reference for ARM64, AMD64, buildx, native builders,
cross-compilation, emulation, manifest lists, and platform evidence.

## Platform Model

Always distinguish:

- **Host platform:** where Codex and the engine run.
- **Builder platform:** where each build step executes.
- **Target platform:** what the final image is built for.
- **Runtime-validation platform:** where that image was actually run and
  tested.

Do not infer one field from another.

## Strategies

- Emulation: convenient for broad build coverage but often much slower for
  compilation and compression. It does not prove native performance or
  runtime correctness.
- Multiple native builders: strongest evidence for architecture-specific
  builds and tests.
- Cross-compilation: preferred when the language/toolchain supports explicit
  build and target settings.

Use `BUILDPLATFORM` and `TARGETPLATFORM` deliberately, and verify native
extensions, downloaded binaries, base-image variants, and package-manager
resolution for each target.

## Evidence Policy

- A multi-platform manifest proves published platform descriptors, not runtime
  behavior.
- Successful emulated compilation is insufficient for performance-sensitive
  code, native extensions, GPU workloads, or production correctness.
- Build and run tests natively on every supported production architecture when
  practical. Mark missing runtime platforms unvalidated.
- Do not hardcode `linux/amd64` because the developer or CI environment happens
  to use it.

## Example

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --tag example/service:test \
  .
```

This command may require pulls, a configured builder, and either native nodes
or emulation. It is evidence only for the build results actually produced.

## Official Source

- [Docker multi-platform builds](https://docs.docker.com/build/building/multi-platform/)
