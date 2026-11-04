# Synthetic provisioning-summary fixture

This is public synthetic evidence, not a live catalog or a recommended preset.
Assess Dev/Test offline; no cloud access, credentials, writes or new research.
Two pools use the supplied supported synthetic presets in region-example:

- trusted: platform cpu-example, preset small-example, 3 nodes of 8 CPU/32 GiB,
  each with a 64 GiB network_ssd boot disk; replacement surge +1.
- runner: same platform, preset large-example, 2 nodes of 16 CPU/64 GiB,
  each with a 128 GiB network_ssd boot disk; replacement surge +1.
- Upgrade one pool at a time. Growth is not selected. One HA control plane;
  Kubernetes version remains unresolved pending component compatibility.
- 1 x 512 GiB SFS; 128 GiB logical Standard Object Storage operating budget.
- Private workers, 1 public ingress IP/LB, dynamic default egress; 1 /24 Pod
  block per worker. Address-pool availability and all quota/capacity are unknown.
- Remote model and telemetry endpoints are required; cost evidence absent.
- Small Dev/Test load: two tasks; reduced throughput during node loss.

Component C-01 has two different containers in one trusted API Pod:

| Workload/container | Count | CPU request/limit | Memory request/limit | Ephemeral request/limit | Heap/placement |
| --- | --- | --- | --- | --- | --- |
| api/main | 2 | 500m/2 | 1Gi/2Gi | 1Gi/4Gi | heap <=1Gi; trusted |
| api/audit | 2 | 100m/500m | 128Mi/256Mi | 256Mi/512Mi | buffer <=128Mi; same Pod |

Use these supplied allocations, preserving identity and units. Account for
missing platform/other workload budgets explicitly instead of certifying that
the fleet fits a complete system. Keep candidate provisioning separate from
verified readiness.
