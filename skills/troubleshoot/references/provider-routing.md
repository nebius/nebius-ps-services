# Provider And Technology Routing

## Applicability

Load this overlay when the failing boundary depends on a cloud, managed service
or vendor implementation. Keep the generic code/resource/network model; use a
specialist only for a concrete independent question within the same worker limit.

## Version Fingerprint

Identify provider, service/distribution, region, resource generation, API/CLI and
workload versions, data-plane implementation and provider/customer ownership.
Select current official documentation through exposed vendor documentation tools;
use bounded official-source retrieval when unavailable. Do not repair credentials
or install integrations for a lookup. Unsupported combinations remain explicit gaps.

## Adaptive Decision Guide

<!-- markdownlint-disable MD013 -->

| Symptom | Competing explanations | Cheapest distinguishing evidence | Interpretation | Next branch |
| --- | --- | --- | --- | --- |
| Configuration analysis passes, request fails | modeled configuration / live path / application | tool proof scope plus actual source-to-destination incident evidence | AWS Reachability Analyzer models configuration without packets or data-plane analysis | inspect live DNS/transport/TLS/application boundary within authority |
| Managed Kubernetes appears healthy | service routing / CNI / CSI / node / workload | exact distribution, runtime and routing implementation, affected Pod/Service identity | kube-proxy commands may not apply to AKS Cilium; control-plane status is bounded | load Kubernetes guide and matching provider docs |
| Distributed GPU job fails | compatibility / placement / fabric / workload | actual GPU stack, scheduler allocation and rank/topology evidence | NVIDIA, AMD and cloud fabric support matrices are distinct | load GPU/AMD, Slurm/Soperator or Nebius guide only as implicated |
| Provider evidence inaccessible | access / retention / wrong resource-window / provider-owned fault | exact denied or unavailable source and safe alternative | unavailable is unknown, never healthy | abstain if decisive; name exact evidence for owner escalation |

<!-- markdownlint-enable MD013 -->

### Diagnostic Effects

Configuration analysis, probes, debug resources and telemetry queries have distinct
cost and state effects. Preserve provider admission and shared query budgets. Only
the coordinator starts authorized active diagnostics; never infer authority from
a vendor tutorial's restart, delete, replacement or permission-change example.

### Verification

Separate API/control-plane completion, guest/workload execution and independently
verified postconditions. Replay the original path, identity and observation window;
do not promote a modeled path or health flag into live workload proof.

## Official Sources

- [Reachability Analyzer scope](https://docs.aws.amazon.com/vpc/latest/reachability/how-reachability-analyzer-works.html)
- [AKS network implementations](https://learn.microsoft.com/troubleshoot/azure/azure-kubernetes/connectivity/troubleshoot-connection-pods-services-same-cluster)
- [Nebius GPU topology](https://docs.nebius.com/compute/clusters/gpu/topology/index)
