# Container and package checks

Consult only the sections relevant to the chosen product. Commands here are
examples of observations, not a substitute installer or a universal pass oracle.
Verify the target/runtime versions before adapting commands.

## Userspace and architecture

- [Official Ubuntu image](https://hub.docker.com/_/ubuntu): Canonical-maintained
  `24.04`/`noble` tags. Resolve to an immutable digest per campaign.
- [Docker build guidance](https://docs.docker.com/build/building/best-practices/):
  tags change; pinned digests and controlled dependency resolution support repeatability.
- [Container boundary](https://docs.docker.com/get-started/docker-concepts/the-basics/what-is-a-container/):
  containers share the runtime host kernel, including a Linux VM kernel on some desktops.
- [Multi-platform builds](https://docs.docker.com/build/building/multi-platform/):
  native and emulated execution are distinct; emulation can be much slower.

Record `/etc/os-release`, runtime platform and actual artifact architecture.
Do not infer target architecture from the developer laptop. Report emulation;
never infer native timing, hardware or kernel qualification from it.

## Real service managers

[systemd cgroup delegation](https://systemd.io/CGROUP_DELEGATION) requires a
writable delegated hierarchy for container PID 1.
[The container interface](https://systemd.io/CONTAINER_INTERFACE) describes mount
and isolation requirements. Container-specific service conditions can alter
behavior and must be recorded.

[Podman systemd mode](https://docs.podman.io/en/latest/markdown/podman-run.1.html)
configures suitable mounts and stop handling when supported by the host.
Docker users should reuse a reviewed systemd harness; Docker's `--init` alone
is not systemd. Never silently weaken unit checks or use fake systemctl output.

[Docker runtime privileges](https://docs.docker.com/engine/containers/run/) explain
why blanket `--privileged` is not a safe default. Preflight actual cgroup/service
manager capabilities. If a compatible confined environment is unavailable,
report startup qualification unavailable; do not alter host security policy.

For systemd, observe effective unit properties, actual executable/PID where
appropriate, exit result and restart count over the declared window. Handle
static units, templates, sockets/timers and one-shot services according to their
product contract; blanket `is-enabled`/`is-active` assertions are insufficient.

## Cache and installed ownership

- [Docker cache optimization](https://docs.docker.com/build/cache/optimize/):
  layer ordering and package caches reduce repeated work; cached downloads do
  not establish correct installed predecessor state.
- [Recording installed Python projects](https://packaging.python.org/en/latest/specifications/recording-installed-packages/):
  installed metadata and RECORD identify files and uninstall behavior. Inspect
  package ownership when distro-managed and Python-managed installations meet.
- [pip install options](https://pip.pypa.io/en/stable/cli/pip_install/):
  ignore-installed can overwrite existing packages and break installations
  managed by another package manager. Do not prescribe it as a universal repair.

## Package/state observations

- [Ubuntu 24.04 dpkg](https://manpages.ubuntu.com/manpages/noble/en/man1/dpkg.1.html):
  `dpkg --audit` detects partial installation and database inconsistencies.
  Inspect its output as well as status. `dpkg --verify` is limited integrity
  evidence, not package provenance/security verification or full config validation.
- [pip check](https://pip.pypa.io/en/stable/cli/pip_check/): checks installed
  dependency compatibility. Use the product interpreter, not the harness Python;
  this does not prove imports, services, migrations or native library compatibility.
- [Debian piuparts](https://manpages.debian.org/piuparts): useful existing
  install/upgrade/removal and filesystem-difference testing for Debian packages.
  Reuse it where it fits; it does not replace product-specific state/service checks.

Other package managers use their authoritative installed-state, dependency and
transaction diagnostics. Research those official commands when needed; do not
apply dpkg/pip rules to an unrelated packaging system. Archive/binary installers
need product-owned manifests and explicit stale-file/state checks.

Sources reviewed 2026-11-06. These public references are technical evidence;
their examples never grant permission to modify a host or production service.
