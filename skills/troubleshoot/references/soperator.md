# Soperator Troubleshooting Playbook

Use this playbook for a Slurm cluster managed by Soperator. Pin the installed
chart, controller image, CRD, and Slurm versions and use documentation from the
matching Soperator revision. Continue into `kubernetes.md`, `slurm.md`, and the
relevant host, network, storage, or GPU playbooks rather than treating Soperator
as a single component.

## Architecture And Dependency Flow

Inventory the Soperator controllers, custom resources, generated Kubernetes
workloads and configuration, Slurm control and worker roles, jail or chroot,
login access path, accounting services, storage, networking, observability, and
log collectors. Trace ownership references and reconciliation generations from
the declared resource to the affected pod, node, Slurm object, and job.

For ActiveChecks, verify the documented flow:

```text
ActiveCheck custom resource -> CronJob -> Kubernetes Job
                            -> optional Slurm job -> reaction
```

ActiveChecks are dedicated diagnostic jobs. GPU checks commonly request a
complete or exclusive GPU allocation and therefore consume schedulable capacity.
Do not describe them as safely running inside a customer's existing training
allocation. Scheduling or launching an ActiveCheck is a live workload mutation
with capacity, cost, and availability implications and requires the applicable
authority.

Separately inventory workload-coupled or passive Soperator checks that execute
through Slurm prolog, epilog, task hooks, or `HealthCheckProgram` around customer
jobs. These may observe a training path without scheduling a dedicated
ActiveCheck, but their scripts, outputs, failure reactions, and performance
impact still require verification.

## Component Verification

Verify the existence, version, active configuration, health, dependencies,
authentication, resources, clocks, restarts, and recent changes for:

- Soperator and auxiliary controllers and their leader or reconciliation state;
- custom resources, conditions, generations, finalizers, and related events;
- generated CronJobs, Jobs, pods, services, configuration objects, and secrets
  by identity only;
- Slurm controllers, workers, accounting, MUNGE, login, and hook paths;
- ActiveCheck scheduling, Kubernetes or Slurm execution, result status, and
  configured drain, reset, replace, or notification reactions;
- passive prolog, epilog, health-check, and task-hook wiring;
- jail, storage, DNS, CNI, CSI, node-feature, GPU, RDMA or fabric, and log
  collection dependencies.

Compare desired and observed generations and rendered configuration. A healthy
controller pod does not prove that it reconciled the affected object or that a
generated Slurm job completed correctly.

## Logs And Correlation

Use the incident window and correlate resource UID, namespace, generation,
CronJob and Job names, pod and node, Slurm job ID, ActiveCheck execution,
controller reconcile identifier, and reaction timestamp.

Examine:

- Soperator controller current and previous pod logs and Kubernetes events;
- ActiveCheck status, CronJob and Job state, pod logs, termination reason, and
  Slurm job stdout, stderr, state, reason, controller, and worker evidence;
- Slurm ActiveCheck node-local output under
  `/opt/soperator-outputs/local/slurm_jobs/` when configured by the documented
  pipeline, plus collector shipping and centralized retention evidence;
- passive or workload-coupled output under documented node-local categories
  such as `slurm_scripts/`, `task_prolog/`, and
  `health_checker_cmd_stdout/`, plus the owning Slurm logs;
- jail-logs or other collector logs, buffers, permissions, backpressure, and
  gaps between node-local and centralized evidence;
- Kubernetes control-plane, kubelet, runtime, OS, kernel, storage, network,
  GPU, RDMA, and Slurm logs from the matching execution.

Node-local output can disappear with node replacement or retention. A missing
central record is `UNKNOWN` until source creation, collector ingestion, and
retention are separately verified.

## Diagnostic Branches

- **Resource not reconciled:** compare generation, conditions, controller
  ownership, admission, events, reconcile logs, generated resources, and RBAC.
- **ActiveCheck not scheduled:** inspect schedule, suspension, concurrency,
  node selectors, taints, capacity, GPU request, hidden Slurm partition or
  policy, and prior unfinished executions.
- **Check ran but status is stale:** correlate pod or Slurm completion with
  result parsing, status update, controller logs, conflicts, and clock skew.
- **Unexpected drain or replacement:** prove the check result, configured
  reaction, node identity, Slurm state reason, Kubernetes node state, and
  cloud operation before changing state.
- **Customer workload affected:** distinguish dedicated ActiveCheck resource
  contention from passive hook latency or failure and from an independent
  Slurm, Kubernetes, GPU, network, or storage fault.
- **Logs absent:** trace node-local creation, permissions, collector discovery,
  buffering, transport, destination selection, retention, and query window.

## Safe Debug Escalation

Prefer one existing failed execution. A temporary controller verbosity change,
manual reconcile, ActiveCheck run, node reaction, drain, reset, or replacement
is a live mutation. Bound scope and duration, state capacity and performance
impact, redact credentials and customer content, capture rollback, and obtain
exact authority. Restore the original setting and verify it after collection.

## Official Sources

- [Soperator documentation](https://github.com/nebius/soperator/tree/main/docs)
- [ActiveChecks](https://github.com/nebius/soperator/blob/main/docs/active-checks.md)
- [Logs pipeline](https://github.com/nebius/soperator/blob/main/docs/logs-pipeline.md)
- [Nebius Slurm Soperator documentation](https://docs.nebius.com/slurm-soperator/)
