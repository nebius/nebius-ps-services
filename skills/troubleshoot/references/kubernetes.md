# Kubernetes Troubleshooting Playbook

Use this playbook after identifying the cluster version, distribution, control
plane ownership, container runtime, CNI, CSI, DNS, ingress or gateway, and
workload deployment model. Verify version-sensitive behavior against the
matching Kubernetes and vendor documentation.

## Adaptive Decision Guide

Use only rows implicated by the failing path; record material unexamined boundaries.

<!-- markdownlint-disable MD013 -->

| Symptom | Competing explanations | Cheapest distinguishing evidence | Interpretation | Next branch |
| --- | --- | --- | --- | --- |
| Ready object, failed workload | wrong endpoints / policy / resource limit / application fault | affected pod request, current EndpointSlice and container/namespace identity | control-plane readiness does not prove data-path success | follow request to selected endpoint then Linux/network/storage owner |

<!-- markdownlint-enable MD013 -->

### Escalation Conditions

Escalate only if the next observation can separate remaining explanations. Bound
time, output, target and effects; preserve evidence and original settings. Treat
production or unconfirmed targets as read-only without exact action authority.

### Verification

Replay the original operation from the same relevant identity and workload, compare
an unaffected control, and verify the implicated boundary independently. Invalidate
pre-change observations and state material unverified scope. A passing status
command cannot replace the workload oracle.

## Failing Path And Component Identity

Map the relevant path across API server, etcd, scheduler, controller manager,
cloud controller, admission and policy, workload controller, Service and DNS,
kube-proxy or replacement, CNI, CSI, kubelet, container runtime, node OS, and
external load balancer or identity systems. For managed control planes, record
which logs and configuration are provider-owned and which evidence is unavailable.

Identify context, cluster, namespace, resource UID, generation, owner chain,
image digest, configuration and secret identities without values, service
account, RBAC, node, topology, rollout, and recent change history.

## Component Verification

For implicated control-plane, node, add-on, workload or external dependencies,
select checks needed by the current explanation: version, active configuration,
desired versus observed state, health and restart history, dependency
reachability, authentication and authorization, CPU and memory pressure,
ephemeral and persistent storage, PID pressure, clock sync, and recent changes.

Status is scoped evidence: a Ready pod does not prove request handling, a Ready
node does not prove CNI or storage data paths, and a successful API read does not
prove scheduler, admission, kubelet, or workload health.

## Implicated Logs And Events

Correlate timestamp, resource UID, pod, container, node, restart count, request
or trace ID, and rollout revision. Examine as relevant:

- workload logs for the current and previous container instance, termination
  reason, exit code, probes, lifecycle hooks, and application logs;
- workload and namespace events, controller conditions, rollout history, and
  admission or policy decisions;
- controller, scheduler, API server, and etcd logs or provider evidence;
- kubelet and container-runtime journal on the affected node;
- CNI, kube-proxy or replacement, CoreDNS, ingress or gateway, and load-balancer
  controller logs;
- CSI controller and node-plugin logs, mount events, device and filesystem logs;
- systemd journal, kernel, OOM, cgroup, network, storage, GPU, and hardware logs.

Events are rate-limited and retained briefly; absence is a coverage gap unless
retention includes the incident. Use bounded windows and selectors rather than
cluster-wide dumps.

## Diagnostic Branches

- **Not created or admitted:** owner reconcile, admission, schema, policy,
  quota, RBAC, finalizer, and API errors.
- **Pending:** scheduler predicates, requests, topology, taints, affinity,
  volumes, GPU or extended resources, quota, and preemption.
- **Image or startup:** registry and identity, image digest, runtime, mounts,
  init containers, entrypoint, environment identity, probes, and application logs.
- **Restart or termination:** previous logs, exit signal, OOM, probe or hook,
  eviction, node disruption, application trace, and kernel evidence.
- **Service connectivity:** endpoint selection, DNS, policy, route, CNI,
  proxying, load balancer, TLS, identity, port, and application response.
- **Node-specific:** compare affected and unaffected pods across kubelet,
  runtime, CNI, CSI, kernel, resources, clock, device, and maintenance history.
- **Control-plane delay:** API latency, etcd capacity, queueing, controller
  workqueue or reconcile evidence, leader changes, and provider operations.

## Controlled Debug Escalation

Prefer existing logs and a single affected resource. Ephemeral containers,
packet captures, verbosity changes, probe changes, rollouts, evictions, or new
test pods may alter state and require authority. Define the hypothesis, exact
namespace and resource, timeout, data sensitivity, performance impact, cleanup,
and rollback. Restore changed logging or workload settings and prove cleanup.

## Official Sources

- [Kubernetes cluster architecture](https://kubernetes.io/docs/concepts/architecture/)
- [Application troubleshooting](https://kubernetes.io/docs/tasks/debug/debug-application/)
- [Cluster troubleshooting](https://kubernetes.io/docs/tasks/debug/debug-cluster/)
- [Logging architecture](https://kubernetes.io/docs/concepts/cluster-administration/logging/)
- [Node pressure eviction](https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/)
- [Services and networking](https://kubernetes.io/docs/concepts/services-networking/)
