# Troubleshooting And Validation

Use this reference for build/runtime failures and before claiming completion.

## Investigation Order

1. Reproduce with the smallest build target or disposable local container.
2. Separate build, image configuration, runtime process, host engine,
   networking, storage, resource, platform, device, and orchestrator evidence.
3. Inspect exact exit status, image config, effective user, command, health,
   mounts, network mode, architecture, resource events, and bounded logs.
4. Test the causal hypothesis with one narrow change.
5. Escalate to `$troubleshoot` when the failure crosses application,
   infrastructure, host, or orchestrator boundaries.

## Common Failure Classes

- Build context omissions, ignored inputs, incorrect stage or target, cache
  masking, missing lock data, build-secret misuse, or architecture-specific
  dependencies.
- Shell-form commands, wrong PID 1, lost signals, unreaped children, invalid
  workdir, missing runtime libraries, or incompatible entrypoint arguments.
- Numeric ownership and permission mismatches, undeclared writes, read-only
  failures, volume shadowing, or missing persistent state.
- DNS, proxy, certificate, port-bind, localhost, network-policy, or dependency
  readiness failures.
- Architecture mismatch, emulation differences, native extension or downloaded
  binary mismatch.
- OOM kill, PID exhaustion, shared-memory pressure, CPU starvation, or
  shutdown exceeding the grace period.
- Health checks that test a dependency instead of the application or conflate
  startup, readiness, and liveness.
- GPU driver/runtime/framework incompatibility, missing CDI/device allocation,
  wrong compute capability, or insufficient shared memory.

## Definition Of Done

Do not describe a container as production-ready unless:

- the selected target builds;
- the expected process starts and exits or terminates correctly;
- the effective UID/GID matches the declared contract;
- writable, temporary, and persistent paths are known;
- health semantics are defined and tested where possible;
- no known secret enters metadata or layers;
- every claimed platform is built and runtime-tested, or marked unvalidated;
- privilege, capability, namespace, seccomp, and device requirements are
  explicit;
- required vulnerability, SBOM, provenance, signing, and verification evidence
  is available;
- Compose or Helm consumers receive the complete runtime contract;
- every unmet requirement is visible as an exception or skipped check.

## Evidence Rules

- Bound stdout, stderr, history, inspect, and scanner output.
- Redact values; report configuration and secret names only.
- Distinguish static, build, emulated, native runtime, GPU runtime, and remote
  publication evidence.
- Report the exact command category and result without persisting secrets or
  private endpoints.
- Clean only resources created and labeled by the current test.
