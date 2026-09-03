# Infrastructure Failure Playbooks

Treat infrastructure as a graph of contracts rather than a list of commands.
Start with read-only evidence and exact target identity. Use current official
vendor documentation and matching domain skills for product-specific behavior.

## Technology Routing

After completing stack discovery, load only the playbooks that match the
observed path:

- `slurm.md` for scheduler, controller, worker, accounting, MUNGE, and job paths.
- `soperator.md` for Soperator controllers, checks, reactions, and log pipeline.
- `kubernetes.md` for cluster control plane, workloads, nodes, CNI, CSI, and DNS.
- `nebius.md` for Nebius product and control-plane evidence.
- `linux.md` for service manager, OS, kernel, resources, clocks, and host hardware.
- `network.md`, `storage.md`, and `gpu.md` for their respective data paths.

For each relevant layer, populate the component matrix and log-coverage ledger
from `investigation-protocol.md`. A component status command is not a substitute
for incident-window logs, dependency-path verification, or active-configuration
comparison.

## Active Incident

- Define user impact, affected scope, start time, current trajectory, and safe
  stabilization options.
- Separate impact control from diagnosis. Record every mitigation and the state
  it changes.
- Preserve logs, metrics, traces, events, configuration identity, deployment
  identity, and request or job correlation before restart or failover.
- Keep the diagnosis confidence independent from service recovery status.

## Installed Service Or Host

- Confirm host, service manager, package/image version, process tree, user,
  ports, files, resource limits, health checks, and dependency endpoints.
- Follow startup, readiness, request, background-work, shutdown, and restart
  paths separately.
- Compare configuration source and effective configuration without exposing
  secrets.
- Check disk, memory, CPU, file descriptors, clocks, DNS, certificates, and
  kernel or runtime constraints only when the model makes them relevant.

## Container Or Orchestrator

- Confirm context, namespace/project, workload identity, image digest,
  generation/revision, desired and observed state, ownership labels, and
  rollout history.
- Separate scheduler, image, startup, readiness, service discovery, policy,
  storage, and application failure domains.
- Compare one affected and one unaffected workload at the same boundaries.
- Prefer inspect, diff, plan, template, lint, and server-side dry-run before
  mutation.
- In confirmed non-production, change one reversible property and observe the
  exact result; keep production read-only without action-specific approval.

## Network And Service Discovery

- Trace name resolution, address selection, route, policy, load balancer,
  transport handshake, TLS identity, proxy, service, and application response.
- Test from the actual failing source identity and network namespace.
- Distinguish timeout, refusal, reset, protocol, authentication, authorization,
  and application errors.
- Account for clock skew, caching, connection reuse, retries, and asymmetric
  paths.

## Identity, Authentication, And Authorization

- Identify the caller, credential source, token or certificate audience,
  expiration, scope, role, policy decision, and target resource without
  printing credential material.
- Distinguish missing identity, invalid authentication, denied authorization,
  propagation delay, stale cache, and wrong target.
- Do not rotate credentials or change IAM/RBAC as an experiment without
  action-specific approval.

## Storage, Database, Queue, And Filesystem

- Separate availability, capacity, latency, consistency, locking, schema,
  permissions, corruption, and client-pool failure modes.
- Check authoritative versus derived data, transaction boundaries,
  idempotency, retries, partial writes, replay, and cleanup.
- Preserve evidence before repair, compaction, cache clear, failover, restore,
  reindex, replay, or migration.
- Use read-only queries or disposable copies first. Production data mutation
  always requires explicit approval.

## Distributed Or Production-Only Failure

- Use one request, transaction, job, or event as the investigation unit.
- Correlate logs, metrics, traces, and events across participating versions and
  configurations.
- Compare affected and unaffected executions boundary by boundary.
- Analyze deadlines, retries, idempotency, partial failure, overload, queueing,
  leader changes, clock offsets, and version skew.
- Add narrow structured instrumentation tied to a hypothesis instead of broad
  unstructured logging.

## Deployment And Configuration Regression

- Compare release, image digest, manifest, package, dependency, feature flag,
  secret version identity without value, policy, migration, and rollout order.
- Identify the first boundary where desired and effective configuration differ.
- Treat rollback success as temporal localization evidence until the violating
  change and mechanism are proven.

## Environment Classification And Mutation Gate

Before live mutation, confirm through reliable metadata that the exact target
is disposable, test, sandbox, preview, or otherwise non-production. A name that
sounds temporary or access to credentials is insufficient.

For a permitted non-production change record:

```text
Exact target:
Environment evidence:
Change and single variable:
Expected observation:
Blast radius:
Rollback:
Data and credential impact:
Observed result:
```

Stop for explicit approval before destructive, irreversible, credential, IAM,
data, public-exposure, deletion, material-cost, or material-availability
actions. Keep production and unconfirmed environments read-only until the user
authorizes the exact action.

When a live infrastructure change occurs while verifying product behavior,
also follow `live-product-validation.md`. Mutation authority permits recovery;
it does not make an intervened trial valid product evidence.
