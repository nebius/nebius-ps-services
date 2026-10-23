# Nebius Capacity Plan — {{project}}

Assessment date: {{date}} · Environment: {{environment}} · Design revision: {{revision}}

## Executive Summary

{{Recommended region, platforms/presets, node counts, storage and networking.
State evidence confidence, feasibility gates, priced subtotal/coverage and
the most important next actions. Distinguish estimates from measured results.}}

| Resource | Steady recommendation | Required scenario maximum | Basis |
| --- | --- | --- | --- |
| Compute | {{nodes / vCPU / GiB / GPUs}} | {{provisioned and usable}} | {{evidence}} |
| Persistent storage | {{types / count / size}} | {{growth / recovery}} | {{evidence}} |
| Networking | {{IPs / LBs / throughput}} | {{peak / surge}} | {{evidence}} |
| Cost | {{currency / period / coverage}} | {{surge / growth}} | {{rates}} |

## Table of Contents

- [Executive Summary](#executive-summary)
- [Assessment Scope, Evidence and Assumptions](#assessment-scope-evidence-and-assumptions)
- [Design Component Inventory and Coverage](#design-component-inventory-and-coverage)
- [Detailed Component Resource Allocations](#detailed-component-resource-allocations)
- [Compute and Kubernetes Node-Pool Plan](#compute-and-kubernetes-node-pool-plan)
- [Storage Capacity and Performance Plan](#storage-capacity-and-performance-plan)
- [Network Capacity Plan](#network-capacity-plan)
- [Headroom, Failure, Rollout and Growth Scenarios](#headroom-failure-rollout-and-growth-scenarios)
- [Tenant/Project Quotas and Region Comparison](#tenantproject-quotas-and-region-comparison)
- [Consolidated Resource Bill and Cost Estimate](#consolidated-resource-bill-and-cost-estimate)
- [Risks, Validation Plan and Recommended Actions](#risks-validation-plan-and-recommended-actions)
- [Sources and Calculation Notes](#sources-and-calculation-notes)

## Assessment Scope, Evidence and Assumptions

{{Project boundaries, intended versus deployed architecture, Kubernetes and
component versions, environment/load/concurrency/SLO, retention/growth horizon,
availability objectives, operating hours, constraints, evidence date and gaps.}}

| Assumption ID | Value or range | Evidence/confidence | Sensitivity or validation |
| --- | --- | --- | --- |
| {{A-id}} | {{value}} | {{source/type/date}} | {{impact}} |

## Design Component Inventory and Coverage

| Component ID | Design source | Deployment/service | Disposition | Shared dependency owner |
| --- | --- | --- | --- | --- |
| {{C-id}} | {{section/link}} | {{unit}} | {{deployed/shared/external/optional/excluded + reason}} | {{owner}} |

## Detailed Component Resource Allocations

{{Repeat compact tables by subsystem; include each app/init/sidecar container.
For external/managed/VM components supply their equivalent allocation and mark
Kubernetes-only fields not applicable. All numeric proposals need a basis.}}

| Component / workload / container | Role/stage | CPU request | CPU limit | Memory request | Memory limit | Rationale/evidence |
| --- | --- | --- | --- | --- | --- | --- |
| {{C-id / names}} | {{app/init/sidecar}} | {{m/core}} | {{value or unset + reason}} | {{Mi/Gi}} | {{Mi/Gi}} | {{measurement/vendor/estimate}} |

| Component | Replicas min/desired/max or concurrency | Heap/cache allowance | Ephemeral request/limit | GPU | Pool/placement |
| --- | --- | --- | --- | --- | --- |
| {{C-id}} | {{counts}} | {{budget}} | {{Mi/Gi}} | {{type/count or none}} | {{constraints}} |

{{Show effective Pod request/limit and expected runtime budgets, stage ledger,
autoscaling signals and admission/defaulting considerations.}}

## Compute and Kubernetes Node-Pool Plan

| Pool/service | Region | Platform / preset | Nodes min/desired/max | Per-node nominal / allocatable | Boot disk |
| --- | --- | --- | --- | --- | --- |
| {{pool}} | {{region}} | {{exact supported IDs}} | {{counts}} | {{CPU/RAM/GPU/ephemeral}} | {{type/size}} |

{{Separate node reservations from infrastructure Pods. Show overhead budgets,
Pod slots, individual fit, placement reasoning, managed-service quantities and
support status. Do not claim full scheduling proof from aggregate arithmetic.}}

## Storage Capacity and Performance Plan

| Owner / data class | Nebius type / access | Count × size | Logical / provisioned / billable | Growth / retention / backups | IOPS / throughput / latency |
| --- | --- | --- | --- | --- | --- |
| {{C-id/data}} | {{service/type/mode}} | {{count × GiB}} | {{separate quantities}} | {{basis}} | {{requirements and service fit}} |

{{Document consistency, durability, fencing, mounts/attachments, expansion,
restore workspace and independence assumptions; list qualification gaps.}}

## Network Capacity Plan

| Flow/resource | Steady demand | Peak/surge demand | Nebius allocation/constraint | Evidence |
| --- | --- | --- | --- | --- |
| {{IPs/subnets/Pod CIDRs/LB/egress/traffic/fabric}} | {{quantity + unit}} | {{quantity + unit}} | {{selected design}} | {{source}} |

{{Include addressing, concurrency/connection limits, throughput, monthly transfer,
image/backup/storage traffic, supported egress and isolation constraints.}}

## Headroom, Failure, Rollout and Growth Scenarios

| Scenario | Concurrent workloads | Provisioned / usable nodes | Requests / runtime / limits | Spare capacity and denominator | Outcome |
| --- | --- | --- | --- | --- | --- |
| {{normal/peak/scale ceiling/rollout/node upgrade/failure/growth}} | {{counts/overlap}} | {{counts}} | {{resource totals}} | {{absolute + percent}} | {{fits/deficit/unresolved with reason}} |

{{State required combinations, bottlenecks, headroom purposes, provision delay
and operational restrictions. Distinguish request fit from peak SLO capacity.}}

## Tenant/Project Quotas and Region Comparison

Inspection scope/identity alias/time/completeness: {{evidence or not inspected}}.

| Quota / unit / region / scope | Limit / usage | Effective headroom | Incremental steady / maximum | Deficit / target quota | Status |
| --- | --- | --- | --- | --- | --- |
| {{provider dimension}} | {{values or unknown}} | {{value or unresolved}} | {{scenario demand}} | {{values}} | {{sufficient/insufficient/unresolved/not inspected}} |

| Region / project scope | Service/preset eligibility | Quota fit | Physical capacity evidence | Cost/latency tradeoff | Rank and action |
| --- | --- | --- | --- | --- | --- |
| {{candidate}} | {{verified/documented/unresolved}} | {{status}} | {{timestamp or unverified}} | {{basis}} | {{recommendation}} |

{{Preserve confirmed deficits alongside unresolved dimensions. Explain reused
versus incremental resources, alternatives and exact quota changes needed.}}

## Consolidated Resource Bill and Cost Estimate

| Resource / region / SKU | Quantity and billing unit | Unit rate / currency / date | Operating hours or usage | Cost | Source/coverage |
| --- | --- | --- | --- | --- | --- |
| {{resource}} | {{quantity}} | {{verified rate or unpriced}} | {{assumption}} | {{subtotal}} | {{link}} |

{{Reconcile billed nodes/disks/storage/network/managed services with other
tables. Show priced subtotal, unpriced items, temporary surge and growth
separately. State taxes/discounts/commitment assumptions; unknown is not free.}}

## Risks, Validation Plan and Recommended Actions

| Priority | Risk or gap | Required evidence/test and acceptance target | Action/owner |
| --- | --- | --- | --- |
| {{priority}} | {{issue}} | {{concrete measure}} | {{recommendation}} |

{{Cover load/startup, throttling/OOM, queues/latency, failure/recovery, storage
performance/restore, network capacity and growth triggers. No tests or cloud
mutations are executed as part of this report.}}

## Sources and Calculation Notes

{{Dated design references, measurements, official vendor URLs and versions;
normalized unit conversions, equations, effective Pod stage ledger, replica
counts and scenario calculations. Record missing evidence and confidence.
Never include credentials, private endpoints or raw cloud dumps.}}
