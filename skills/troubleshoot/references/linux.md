# Linux Host Troubleshooting Playbook

Use this playbook for bare-metal hosts, VMs, container nodes, and guest OS
layers. Record distribution, kernel, architecture, boot ID, init and service
manager, package or image identity, time source, cgroup generation, security
controls, and whether the host is mutable or production.

## Component And Resource Verification

Verify the affected service unit, process tree, executable and libraries,
effective configuration, user and groups, capabilities, namespaces, cgroups,
limits, sockets, files, devices, mounts, dependencies, restart policy, and last
transition. Compare desired state with the running process and open resources.

Check CPU saturation and steal time, memory and swap, OOM and pressure stalls,
PID and file-descriptor exhaustion, disk and inode capacity, I/O latency,
cgroup throttling, thermal or power events, clock sync, and recent package,
kernel, boot, configuration, or hardware changes when the model makes them relevant.

## Mandatory Log Layers

Correlate wall time, monotonic time where available, boot ID, unit invocation,
PID, cgroup, host, device, and workload identifiers. Examine:

- service stdout and stderr and configured component log files;
- systemd unit status properties and bounded journal entries for the unit,
  current and previous boot, without relying only on the final status line;
- kernel ring buffer or journal for OOM, cgroup, filesystem, block, network,
  driver, PCIe, machine-check, RAS, thermal, watchdog, and hung-task events;
- authentication, audit, firewall, scheduled-task, package, and boot logs when
  the hypothesis reaches those boundaries;
- container runtime, orchestrator, storage, GPU, and hardware logs owned by
  services above or below the host.

Prove retention and boot coverage. A clean current boot cannot eliminate a
failure that occurred before reboot.

## Diagnostic Branches

- **Fails to start:** unit dependencies and ordering, executable, user,
  permissions, environment identity, configuration parse, mounts, ports,
  limits, sandboxing, and component logs.
- **Killed or restarted:** exit status and signal, supervisor action, OOM,
  watchdog, probe, crash or core evidence, cgroup pressure, and kernel events.
- **Resource exhaustion:** identify the owning cgroup or process, temporal
  growth, limit versus host capacity, allocator or descriptor evidence, and
  triggering workload.
- **Time-related:** source, synchronization state, measured offset, step or
  slew events, timezone assumptions, certificate or credential timestamps, and
  cross-host uncertainty.
- **Kernel or hardware:** driver and firmware identity, device topology,
  repeatable error signature, RAS or machine-check evidence, affected and
  unaffected hosts, and vendor guidance.

## Controlled Debug Escalation

Temporary service verbosity, core-dump capture, audit rules, tracing, or kernel
dynamic debug can expose sensitive data or impose overhead. State the exact
subsystem and predicate, size and duration bound, storage location, performance
impact, access controls, cleanup, and rollback. Never enable broad debug or
unbounded tracing across a production host without exact authority.

## Official Sources

- [systemd journal documentation](https://www.freedesktop.org/software/systemd/man/latest/systemd-journald.service.html)
- [journalctl documentation](https://www.freedesktop.org/software/systemd/man/latest/journalctl.html)
- [Linux kernel administration guide](https://docs.kernel.org/admin-guide/index.html)
- [Linux RAS documentation](https://docs.kernel.org/admin-guide/ras.html)
- [Linux cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html)
- [Pressure stall information](https://docs.kernel.org/accounting/psi.html)
