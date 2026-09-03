# Storage Troubleshooting Playbook

Identify the storage contract before collecting commands: block, filesystem,
shared filesystem, object, database, queue, or container volume; authoritative
owner; client and server; mount or endpoint; consistency and durability model;
capacity and performance expectations; encryption and identity; and incident
window.

## Architecture And Component Inventory

Trace the active path from application or job through library and system calls,
filesystem or object client, page and metadata caches, mount namespace, volume
attachment or CSI, network path, storage service or device, replication, and
backend media. Record versions, topology, configuration authorities, device or
volume IDs, mount options, access mode, ownership, quota, and recent changes.

## Component Verification

For each client, controller, node plugin, mount, network endpoint, service,
device, and backend dependency, verify version and existence, active
configuration, health, authentication and authorization, reachability,
attachment or mapping, capacity and inodes, latency and queue pressure, error
counters, clocks, data-path permissions, and recent operations or maintenance.

Separate these failure classes:

- availability, attachment, mount, and path discovery;
- capacity, quota, inode, file descriptor, cache, or queue exhaustion;
- latency, throughput, IOPS, throttling, contention, or small-I/O amplification;
- permissions, identity, encryption, lease, lock, or fencing;
- consistency, partial write, stale view, ordering, replay, and idempotency;
- filesystem, metadata, media, controller, replication, or data corruption.

## Logs And Correlation

Correlate application request, job, file or object key using safe redaction,
volume or device, mount, pod and node, operation ID, and timestamp. Examine:

- application, database, queue, object client, and job stdout or stderr;
- CSI controller and node plugins, orchestrator events, attachment and mount
  operations, and current or previous workload logs;
- filesystem client and server, volume service, object service, and control-plane
  operation logs where available;
- kernel block, filesystem, multipath, device reset, timeout, I/O error, RAS,
  OOM, and hung-task events;
- device health, transport, network, quota, capacity, throttling, and latency
  evidence across affected and unaffected paths.

Do not run repair utilities, remount, detach, fail over, replay, reindex,
compact, restore, or clear caches before preserving evidence and proving the
action's data and availability impact.

## Diagnostic Branches

- **Attach or mount:** desired attachment, topology and project constraints,
  controller operation, node mapping, device discovery, filesystem identity,
  mount options, permissions, and kernel evidence.
- **Slow I/O:** decompose application wait, cache, filesystem, block queue,
  transport, service, replication, and media latency; compare request shape and
  pressure with an unaffected path.
- **Capacity:** distinguish logical quota, filesystem capacity, inodes, thin or
  backend pool, reservation, and retention; identify the growth owner.
- **Consistency or corruption:** freeze the failure signature and evidence,
  identify the first invalid read or write boundary, verify replicas or checksums
  read-only where safe, and require a separately authorized recovery plan.
- **Object storage:** endpoint and region, DNS and TLS, identity and policy,
  signature clock, request ID, key and version identity, rate limits, multipart
  state, consistency expectations, and service logs.

## Controlled Debug Escalation

Detailed client debug, I/O tracing, filesystem diagnostics, or device capture
can expose data and add latency. Scope by volume, device, process, operation,
and time; estimate overhead and output; redact payloads and object names; capture
original settings; and verify rollback. Production data mutation always needs
action-specific approval.

## Official Sources

- [Linux storage documentation](https://docs.kernel.org/admin-guide/device-mapper/index.html)
- [Linux filesystems documentation](https://docs.kernel.org/filesystems/index.html)
- [Kubernetes storage concepts](https://kubernetes.io/docs/concepts/storage/)
- [Kubernetes volume troubleshooting](https://kubernetes.io/docs/tasks/debug/debug-application/debug-pods/#debugging-persistent-volumes)
- Use the deployed filesystem, CSI driver, device, database, queue, or object
  service vendor's matching official documentation.
