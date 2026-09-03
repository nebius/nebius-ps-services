# Nebius Troubleshooting Playbook

Use this playbook as the Nebius product and control-plane overlay for Compute,
Managed Kubernetes, Soperator, VPC, storage, quota or capacity, IAM, AI
services, and observability. Continue with the relevant generic playbooks for
guest OS, Kubernetes, Slurm, network, storage, GPU, and application evidence.

## Scope And Identity First

Freeze the exact tenant, project, region, zone where applicable, service,
resource type and ID, resource generation or revision, operation ID, target
endpoint identity, and incident window. Distinguish desired resource state,
Nebius control-plane operation state, guest or workload state, and monitoring
state. Do not infer the selected project from a resource name.

Use the installed `nebius` skill for its existing project-selection, quota,
VPC, Managed Kubernetes, GPU or fabric, and observability authorities when the
task requires those workflows. A current-session CLI profile, selector, tenant
quota-read, or Agent authentication failure routes to
`agent-nebius-auth-diagnose`; troubleshooting must not repair or switch
credentials, profiles, selectors, IAM, or hooks as a shortcut.

## Product Branches

### Compute VM Or GPU Cluster

Verify instance and GPU-cluster status, platform and preset, image, network
interfaces, security groups, boot and secondary volume attachment, maintenance
or interruption history, serial or diagnostic evidence when authorized, guest
agent and OS state, GPU inventory, and InfiniBand topology when present.

### Managed Kubernetes

Verify cluster and node-group versions, status and operations, endpoint access,
network and security-group wiring, node health and replacement history, quota
and capacity, identities, add-ons, and workload evidence. Continue with
`kubernetes.md` and do not treat a healthy managed control-plane state as proof
that node, CNI, CSI, GPU, or application paths work.

### Soperator

Verify service resource state, chart or Soperator version, cluster operations,
node groups, storage, VPC, IAM, quota or capacity, and observable controller or
workload evidence. Continue with `soperator.md` and `slurm.md`.

### VPC And Connectivity

Trace subnet and IP allocation, route, security group, public or private
endpoint, DNS, load balancer, NAT or egress, MTU, and InfiniBand or fabric paths.
Test from the affected source identity and continue with `network.md`.

### Storage

Separate Compute disks and shared filesystems, Object Storage, container
registry, and managed database paths. Verify project and region constraints,
attachment or mount state, endpoint identity, permissions, capacity, operation
history, guest or CSI evidence, and service metrics. Continue with `storage.md`.

### Quota, Capacity, And Reservation

Compare requested resource shape with project quota, regional availability,
reservation or capacity-block use, operation errors, scheduler or autoscaler
behavior, and recent quota changes. Do not create, resize, or move capacity as a
diagnostic experiment without cost and availability authority.

### IAM And Identity

Identify caller, service account or workload identity, resource hierarchy,
role binding, audience and scope, expiration, propagation, and policy decision
without exposing credentials. Distinguish authentication, authorization, wrong
project, and unavailable-resource errors. IAM mutation always requires exact
authorization.

### AI Services

Identify the specific endpoint, job, MLflow cluster, application, or other
service; model or image revision; request or job ID; input contract; quota and
capacity; network and storage dependencies; service status; workload logs; and
operation history. Follow the service's current troubleshooting documentation
and the application code path when relevant.

## Evidence Layers

For the incident window, correlate resource ID, operation ID, request or job ID,
VM or node, pod, Slurm job, volume, and restart or replacement counts. Examine:

- resource status, conditions, operation history, maintenance, and recent
  configuration changes;
- service-specific monitoring, diagnostic logs, audit logs, and documented
  provider evidence;
- Managed Kubernetes, Soperator, guest OS, application, network, storage, GPU,
  and hardware logs owned by the customer-visible layer;
- quota and capacity evidence and relevant public service-status information;
- observability evidence only after the signal-fit, authority, selector, and
  bounded-window gate in `observability-evidence.md`.

Record provider-owned or inaccessible control-plane logs as `UNKNOWN`, not
healthy. State the exact resource, window, operation, and evidence needed for a
support escalation.

## Mutation And Escalation

Begin read-only. VM restart, node replacement, cluster update, ActiveCheck,
network or security-group change, storage attachment, quota request, IAM change,
or debug logging can affect availability, cost, identity, or data. Apply the
main skill's environment and action-specific approval gates. Preserve operation
IDs and pre-change state, use bounded changes, and verify rollback.

## Official Sources

- [Nebius AI Cloud documentation](https://docs.nebius.com/)
- [Nebius documentation index for agents](https://docs.nebius.com/llms.txt)
- [Nebius services overview](https://docs.nebius.com/overview/services)
- [Compute documentation](https://docs.nebius.com/compute)
- [Nebius CLI reference](https://docs.nebius.com/cli/reference)
