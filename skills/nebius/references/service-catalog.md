# Infrastructure coverage and source index

Reviewed 2026-09-07. This is a coverage map, not a live availability catalog.
Check the [official documentation index](https://docs.nebius.com/llms.txt),
[service list](https://docs.nebius.com/overview/services),
[regions](https://docs.nebius.com/overview/regions) and each service's current
limits before recommending a target. A documented API/resource does not prove
a console control, tenant entitlement, available capacity or production SLA.

Depth: **practical** = guide plus callable assets/inspection; **guide** = service
workflow and official implementation route; **integration** = selection and
prerequisites only. API versions identify schemas, not product maturity.

| Category/capability | API/interface | Depth | Guide / assets |
| --- | --- | --- | --- |
| CPU/GPU VMs, images and lifecycle | compute.v1 | practical | compute.md; compute/virtual_machine.py |
| GPU clusters, InfiniBand and capacity reservations | compute.v1; capacity APIs | guide | mk8s-compatibility.md; quota-management.md |
| Container/preemptible VMs and maintenance | compute.v1 | guide | compute.md |
| Kubernetes clusters/node groups | mk8s.v1 | practical | compute.md; compute/kubernetes.py; inspect_kubernetes.py |
| Versions, autoscaling, GPU operators/RDMA | mk8s.v1; Kubernetes/Helm | practical | mk8s-compatibility.md; mk8s-gpu-setup.md; gpu assets |
| Disks, filesystems and CSI | compute.v1; Kubernetes CSI | practical | storage.md; storage/volumes.py; filesystem-pvc.yaml |
| Local SSD, custom images and snapshots | compute.v1 | guide | storage.md; compute.md |
| Object Storage bucket management | storage.v1 | practical | storage.md; storage/buckets.py |
| Objects, multipart, lifecycle and transfer | S3; storage.v1 | practical | storage.md; storage/object_transfer.py |
| Container Registry | registry.v1; registry protocol | practical | storage.md; storage/managed_services.py |
| Managed PostgreSQL | msp.postgresql.v1alpha1 | practical | storage.md; storage/managed_services.py |
| Networks/pools/subnets/allocations/routes | vpc.v1 | practical | networking.md; vpc-networking.md; route-inspection.md |
| Security groups/rules | vpc.v1 | practical | networking.md; networking/network.py |
| DNS zones/records | dns.v1 | guide | networking.md; official generated DNS schema |
| Custom NAT and VPN Gateway | VM/VPC and owning product workflow | guide | networking.md |
| Tunnels | tunnel.v1 | guide | networking.md |
| Metrics/logs/traces, agents, alerts | documented read/ingest protocols | practical | observability.md; observability/public-endpoints.yaml |
| Audit Logs | tenant audit service | guide | observability.md; explicit audit-query owner |
| Principals/groups/permits/federation | iam.v1; iam.v2 projects | practical | iam.md; iam assets |
| Access keys/authorized keys/token exchange | iam.v2 access keys; iam.v1 | practical | iam.md; iam assets |
| KMS | kms.v1 | practical metadata; lifecycle guide | iam.md; iam/security_metadata.py |
| SecretStash | mysterybox.v1 | practical metadata; lifecycle guide | iam.md; iam/security_metadata.py |
| Python SDK, CLI, REST/gRPC | service-versioned generated clients | practical | api-sdk.md; sdk assets |
| Soperator, Serverless AI, MLflow | owning public service interfaces | integration | ai-service-integration.md |

## Maturity and freshness

KMS and Audit Logs overview pages explicitly label their services preview as of
this review. Other rows mean documented coverage; this index makes no inferred
GA/SLA claim. Recheck the service overview and regional matrix at use time and
record any preview, private-access, unsupported-interface or missing-inventory
constraint. Do not convert an alpha namespace into a stable version by renaming.

API bindings are checked against released Python SDK 0.6.7. On updates, validate
real constructors, operation methods and metadata-only projections before moving
the pin. Source links live with each domain's rules so changing a claim also
changes its evidence. Prices, quotas, presets and region lists are deliberately
not duplicated. Check costs through current pricing, quota APIs and capacity
inventory; no payment/subscription changes are part of this skill.

## Maintenance acceptance

For every changed capability update its guide, source link/review date, applicable
asset, tests and routing. New claims require public documentation or a clearly
bounded verified SDK fact. The isolated-copy test must pass without this monorepo.
Add a near-miss trigger when a capability approaches another workflow's boundary.
