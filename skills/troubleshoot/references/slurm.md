# Slurm Troubleshooting Playbook

Use this playbook when the discovered path includes Slurm. Pin the observed
Slurm version and compare its active configuration with the matching SchedMD
documentation before interpreting defaults or fields.

## Architecture And Configuration Discovery

Inventory the configured roles and flows:

- `slurmctld` primary and backup controllers, state directory, partitions,
  scheduler and selection plugins, and configured controller addresses.
- `slurmd` workers, node registrations, hardware inventory, spool paths, and
  configured node or dynamic-node definitions.
- `slurmdbd` and database when accounting is enabled, including cluster name,
  storage endpoint identity, and association or QOS dependencies.
- MUNGE identity and key-distribution boundary on every participating host.
- CLI, REST, federation, prolog, epilog, SPANK, job container, GRES, cgroup,
  topology, switch or fabric, and power-management plugins when present.
- Control path, job-submission path, allocation path, launch path, I/O path,
  accounting path, and node-state transition path.

Locate the effective `slurm.conf`, included files, `gres.conf`, `cgroup.conf`,
`acct_gather.conf`, `slurmdbd.conf`, plugstack configuration, systemd units,
and generated configuration sources. Record the source of truth and compare
running configuration with repository, image, or management-plane intent.

## Component Verification

For each controller, worker, database daemon, MUNGE daemon, and relevant plugin
or external dependency, verify:

- expected binary or image version and compatible protocol generation;
- process or workload health and restart history;
- active configuration and ownership or permissions of state and spool paths;
- forward and reverse dependency reachability on configured addresses and ports;
- MUNGE encode/decode health, credential age, UID/GID context, and clock sync;
- CPU, memory, file descriptor, PID, disk, inode, and cgroup pressure;
- controller state continuity, node state and reason, registration details,
  drain events, job requeue or failure reasons, and recent changes.

Use Slurm's real control or job path as evidence. A responsive `sinfo` or
`scontrol ping` does not prove worker launch, accounting, job I/O, or GRES health.

## Mandatory Log Discovery

Discover configured paths rather than assuming defaults:

| Evidence owner | Required sources |
| --- | --- |
| Controller | `SlurmctldLogFile`; syslog or journal fallback; controller service events; state-save errors |
| Worker | `SlurmdLogFile`; syslog or journal fallback; worker service events; launch and registration errors |
| Scheduler | Scheduler/backfill messages in controller log; relevant plugin diagnostics and job reasons |
| Accounting | `LogFile` from `slurmdbd.conf`; database service logs; controller accounting messages |
| Authentication | MUNGE service journal or log on caller, controller, and worker; credential and clock errors |
| Job | Job stdout and stderr; submit response; job state and reason; step, launch, and I/O evidence |
| Hooks | Prolog, epilog, task prolog/epilog, SPANK, health-check output, exit status, and timeout evidence |
| Host | systemd journal, kernel, OOM, cgroup, filesystem, device, network, GPU, and fabric evidence |

Filter by the incident window and correlate job ID, step ID, node, process ID,
controller identity, restart count, and credential timestamps. Distinguish
stale errors from events on the failing execution. If a configured log path is
empty or absent, prove whether logging is routed to syslog or the journal and
whether retention covers the incident.

## Diagnostic Branches

### Submission Or Scheduling

Test policy, association, QOS, reservation, partition, dependency, feature,
license, topology, and resource predicates. Use pending or state reasons and
controller scheduler evidence to distinguish an unsatisfied constraint from a
scheduler defect or stale state.

### Controller To Worker

Correlate registration and RPC evidence on both endpoints. Test name
resolution, address selection, route, firewall, port, Slurm protocol version,
MUNGE credential, clock skew, controller identity, and spool/state permissions.
Do not infer connectivity from one direction only.

### Launch Or Job Failure

Follow allocation to batch step, prolog, cgroup, GRES assignment, task launch,
application stdout/stderr, epilog, and final state. A prolog or epilog failure
can drain a node or requeue a job; preserve its output and exit evidence before
resuming or undraining anything.

### Accounting

Trace controller enqueue, `slurmdbd` receipt, database operation, and query
visibility. Separate scheduler operation from accounting availability and
record backlog, timeout, authentication, schema, capacity, and retention evidence.

### GPU Or Fabric

Compare configured and discovered GRES, allocation environment, device files,
cgroup device access, driver state, Xid or ECC evidence, topology, and fabric
health. Continue with `gpu.md` and `network.md`.

## Controlled Debug Escalation

Prefer incident-window logs at the existing level. If decisive evidence is
missing, use the narrowest documented daemon, scheduler, RPC, or plugin debug
setting for a bounded interval. Capture original values, expected volume and
performance impact, exact rollback, and secret-redaction rules. Reproduce one
identified event, restore the original level, and verify rollback. Do not leave
cluster-wide debug enabled.

## Official Sources

- [Slurm overview](https://slurm.schedmd.com/overview.html)
- [Slurm configuration](https://slurm.schedmd.com/slurm.conf.html)
- [Controller](https://slurm.schedmd.com/slurmctld.html) and
  [worker](https://slurm.schedmd.com/slurmd.html) documentation
- [Accounting daemon](https://slurm.schedmd.com/slurmdbd.html)
- [Troubleshooting guide](https://slurm.schedmd.com/troubleshoot.html)
- [Prolog and epilog guide](https://slurm.schedmd.com/prolog_epilog.html)
- [Job state codes](https://slurm.schedmd.com/job_state_codes.html)
- [Generic resources](https://slurm.schedmd.com/gres.html)
