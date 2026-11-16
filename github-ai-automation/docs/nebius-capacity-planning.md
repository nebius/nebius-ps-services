# Nebius capacity plan — GitHub AI automation

Assessment: 2026-11-11 America/Los_Angeles / 2026-11-12 UTC.
Environment: proposed production deployment; two concurrent PR jobs;
single-region availability through VM failures and rolling upgrades.

## Executive summary

The user selected two concurrent jobs and production availability within one
region. The architecture discussed in the conversation uses persistent Nebius
VMs with disposable gVisor-backed job containers, external Token Factory
inference, and GitHub-hosted intake, image builds and protected effects.
This report sizes that proposed successor. The current implementation remains
GitHub-hosted only; remote execution and HA are not implemented or qualified.

Assessment scope is public documentation across all regions. No tenant, profile
or project was selected or inspected. There is no verified deployment fit.
One regional placement is required, not a deployment in every listed region.

| Provisioning item | Steady recommendation | Replacement envelope | Qualification boundary |
| --- | --- | --- | --- |
| Trusted service pool | 2 regular VMs, `cpu-d3`, `8vcpu-32gb` each | 3 during trusted-service replacement | Each surviving VM must handle both jobs' relays and request coordination |
| Untrusted execution pool | 2 regular VMs, `cpu-d3`, `8vcpu-32gb` each | 3 during worker replacement | Each surviving VM has two execution slots; global active-job ceiling stays two |
| Total compute | 4 VMs, 32 vCPU, 128 GiB RAM, 0 GPUs | 5 VMs, 40 vCPU, 160 GiB RAM | Replace one pool at a time; no simultaneous independent upgrades |
| Boot disks | 4 × 64 GiB `network_ssd` | 5 × 64 GiB | Ubuntu 24.04 LTS, `ubuntu24.04-driverless`; resolve and pin an image ID before provisioning |
| Worker scratch/cache disks | 2 × 192 GiB `network_ssd` | 3 × 192 GiB | Disposable source/workspace storage; no authoritative state |
| Total block storage | 6 disks, 640 GiB | At most 8 disks, 896 GiB | Envelope is the worker replacement case |
| Container Registry | 1 registry; 50 GiB initial retained-image budget | Same registry | Budget, not an allocated block volume; promote tested digests |
| Object Storage | 2 private regular buckets; 10 GiB state + 50 GiB artifacts/backups budgets | Same buckets | Standard storage; conditional writes and recovery must be qualified |
| Network | 1 VPC, 2 subnets, 4 private addresses, 2 public addresses | 5 private + at most 3 public addresses | Public addresses only on trusted endpoints; private workers use default egress |
| API ingress | 2 independently reachable TLS endpoints and client failover | Third temporary endpoint possible | No single VM proxy or assumed standalone managed load balancer |
| Kubernetes/control plane | Not applicable | Not applicable | VM services managed by systemd; Docker and runsc versions selected during qualification |
| External services | GitHub Actions, Token Factory, public DNS/TLS, operational alert destination | Same dependencies | Their outages are outside the VM-failure availability objective |

Four VMs provide redundant capacity, not proof of availability. Loss of an
execution VM can interrupt its current inference/check process; recovery must
resume from durable evidence or retry a fenced attempt. Proposed recovery target:
resume eligible work within five minutes after a single VM failure, with no loss
of acknowledged durable requests and no duplicate privileged effect. This numeric
target is an engineering proposal, not a measured or user-approved SLO.

Physical host independence, project eligibility, quotas, CPU availability,
runtime compatibility and application failover remain qualification gates.
The selected scope does not include surviving a regional Object Storage outage
or an entire region failure. No uptime percentage is claimed.

Public list-price subtotal at 730 running hours is approximately **USD 817.20**
per month for compute, disks and 60 GiB of fully used Standard object storage.
The user's free compute and inference access is an account-specific premise,
not a verified discount. Registry and Virtual Networks are documented as free.
Object operations, observability, DNS and any other uncovered services are not
included in that subtotal. Actual charges require the account's coverage terms.

| Region | Public support for required services and `cpu-d3` presets | Tenant quota / physical CPU capacity | Assessment |
| --- | --- | --- | --- |
| `eu-north1` | Documented | Not inspected / unverified | Candidate; published default CPU quota is zero, not evidence of this tenant's allowance |
| `eu-west1` | Documented | Not inspected / unverified | Candidate |
| `me-west1` | Documented | Not inspected / unverified | Candidate; billing currency depends on customer entity |
| `us-central1` | Documented | Not inspected / unverified | Candidate; published default CPU quota is zero |
| `uk-south1` | Documented | Not inspected / unverified | Candidate |
| `uk-south2` | Documented | Not inspected / unverified | Candidate |
| `eu-south1` | Documented | Not inspected / unverified | Candidate |
| `eu-west2` | Documented | Not inspected / unverified | Candidate |
| `us-north1` | Documented | Not inspected / unverified | Candidate |
| `eu-north2` | Documented, restricted region | Not inspected / eligibility unknown | Existing-deployment users only; do not select without confirmed access |

No region is ranked by unmeasured latency or assumed available capacity.
Select an eligible project in a region with sufficient quota and acceptable
measured access to GitHub, the registry and Token Factory.

## Table of contents

- [Executive summary](#executive-summary)
- [Scope and assumptions](#scope-and-assumptions)
- [Component inventory](#component-inventory)
- [Process and container allocations](#process-and-container-allocations)
- [HA and scenario arithmetic](#ha-and-scenario-arithmetic)
- [Storage and retention](#storage-and-retention)
- [Networking and authentication](#networking-and-authentication)
- [Image build and update pipeline](#image-build-and-update-pipeline)
- [Quota and regional assessment](#quota-and-regional-assessment)
- [Costs](#costs)
- [Inputs required before implementation](#inputs-required-before-implementation)
- [Qualification and remaining gates](#qualification-and-remaining-gates)
- [Sources and calculation notes](#sources-and-calculation-notes)

## Scope and assumptions

The concurrency and single-region HA objective are accepted user inputs.
VM shapes, storage budgets, recovery timing and endpoint topology below are
proposals for design review. No cloud resource, credential, IAM grant or public
endpoint was created. Existing resources are not subtracted from the full-addition
estimate because no reuse was attributed or inspected.

| Input | Assessment basis | Sensitivity |
| --- | --- | --- |
| Concurrency | Two globally active PR jobs; normal placement prefers one per worker | A per-PR lock is insufficient; distributed admission is required |
| Availability | Continue processing after one VM fails; preserve redundancy during rolling upgrades | One temporary replacement is created and verified before draining an old VM |
| Daily volume | Provisional 20 jobs/day, with peaks bounded by admission | Storage and provider-call budgets scale with volume and job duration |
| Duration | Current limits: 30 minutes per stage and three hours per request | Limits are not observed service times or throughput measurements |
| Job storage | Proposed 20 GiB per active workspace, including temporary outputs | Large repositories/build suites require measurement and revised quotas |
| Retention | Proposed 30 days of sanitized artifacts/logs; state and recovery records follow separate policy | Never expire active leases, intents or unresolved requests through blanket lifecycle rules |
| Inference | External Token Factory; no GPU allocation | Two jobs do not establish two concurrent API requests; obtain model-specific limits |
| Growth | Four jobs is a separate future scenario, not the initial deployment | Requires more worker and relay capacity, not only a flag change |

The assessment does not select resources for arbitrary product live tests.
Those fixtures depend on the PR and its separately qualified capability driver;
their VM/GPU/storage quota must be budgeted independently before merge mode can
rely on them. Release publication and image-build compute remain on GitHub.

## Component inventory

| ID | Component and disposition | Source / ownership |
| --- | --- | --- |
| C01 | GitHub intake, candidate CI, effect brokers, release jobs: external | Existing root workflows; job-scoped GitHub token and protected effect ownership |
| C02 | Two trusted job-service processes: proposed | Conversation HA direction; remote job API, durable admission and recovery are new work |
| C03 | Up to two relay processes across trusted VMs: existing component, proposed placement | `src/github_ai_automation/sandbox.py`, `relay.py` |
| C04 | Up to two agent containers across workers: existing component, proposed gVisor runtime | `sandbox.py`, `Dockerfile`, `config/skills.json` |
| C05 | Validation containers: existing, serial within each job | `sandbox.py:run_checks`; execute after agent/relay cleanup |
| C06 | OS, Docker/runsc, worker launcher, telemetry: proposed host services | VM deployment and operational dependency; no Kubernetes allocation |
| C07 | Regular Object Storage state bucket: required durable shared dependency | `state.py:S3Store`; leases, fencing, intents, receipts and waivers |
| C08 | Artifact/log/backup bucket: proposed | Sanitized evidence and independent retained state exports |
| C09 | OCI registry and image updater: proposed managed registry and GitHub workflow | Existing workflow only pulls a digest; README assigns publication to operator process |
| C10 | Token Factory: external | Existing restricted Responses relay; no local inference server |
| C11 | DNS/TLS, ingress authentication and alert destination: required dependencies | Proposed remote execution boundary; protocol and operational ownership not yet finalized |
| C12 | PR-specific cloud test fixtures: unallocated external workload | Empty live-capability registry; requires a separate scoped capacity plan |
| C13 | PostgreSQL, Redis, Kubernetes, shared filesystem, dedicated NAT VM, GPUs: excluded | No established requirement for these services in this assessment |

## Process and container allocations

These are VM admission reservations and maximum budgets, not Kubernetes requests.
Current Docker limits are evidence of configuration, not measurements. Proposed
service budgets require validation. Runtime consumption remains unmeasured.

| Component / execution unit | Reservation CPU / memory | Maximum budget CPU / memory | Count and placement | Writable storage |
| --- | --- | --- | --- | --- |
| C02 job service | 0.5 vCPU / 1 GiB | 1 vCPU / 2 GiB | One per trusted VM; two steady | 2 GiB per process |
| C03 relay | 2 vCPU / 4 GiB | 2 vCPU / 4 GiB | Up to two total; both fit on a surviving trusted VM | 256 MiB each |
| C04 agent | 2 vCPU / 4 GiB | 2 vCPU / 4 GiB | Up to two globally; both fit on one worker | 20 GiB per job |
| C05 validation | 2 vCPU / 2 GiB | 2 vCPU / 2 GiB | Replaces the job's agent stage; checks are serial | Same job's 20 GiB allocation |
| C06 sandbox overhead allowance | 0.5 vCPU / 0.5 GiB | 1 vCPU / 1 GiB | One allowance per active agent/check slot | Included in job storage |
| C06 telemetry/launcher allowance | 0.25 vCPU / 0.5 GiB | 0.5 vCPU / 1 GiB | One per VM | 4 GiB rotating local buffer |
| C06 OS/runtime reservation | 1 vCPU / 2 GiB | Reserved before application allocation | One per VM | Boot/system allowance |

The relay currently inherits the agent's 2-vCPU/4-GiB limits. This conservative
plan preserves them. A smaller relay allocation is a later measured optimization,
not a prerequisite or a silently assumed code change. The extra sandbox allowance
is conservative: cgroup accounting may already include some runtime overhead;
measure it before removing this reserve. Agent tmpfs allocations count against
the agent memory limit and are not added again.

Each 8-vCPU/32-GiB VM has **7 vCPU / 30 GiB** left after OS/runtime reservation.
With both jobs on one surviving VM:

- Trusted maximum: two relays + one job service + telemetry = **5.5 vCPU / 11 GiB**.
  Spare is 1.5/7 vCPU (21.43%) and 19/30 GiB (63.33%).
- Worker maximum: two agents + two sandbox allowances + telemetry =
  **6.5 vCPU / 11 GiB**. Spare is 0.5/7 vCPU (7.14%) and 19/30 GiB (63.33%).
- Validation replaces agents: maximum worker memory falls to 7 GiB; CPU stays
  at 6.5 vCPU. Agent, relay and validation are not all charged as simultaneous
  within one job.

These margins are applied once. The second VM in each pool is explicit failure
capacity, not another percentage reserve. Four-job growth needs two trusted
`cpu-d3/16vcpu-64gb` VMs and three `cpu-d3/8vcpu-32gb` workers: **5 VMs,
56 vCPU, 224 GiB**, with two workers remaining after one fails. This is an
alternative future deployment, not an addition to the four-VM baseline.

## HA and scenario arithmetic

| Scenario | Provisioned / usable trusted VMs | Provisioned / usable workers | Active jobs | Outcome and restriction |
| --- | --- | --- | --- | --- |
| Normal / peak | 2 / 2 | 2 / 2 | 1 / 2 | Prefer one job per worker; global ceiling remains two |
| One trusted VM fails | 2 / 1 | 2 / 2 | 2 | Surviving trusted VM fits both relays; interrupted calls may retry |
| One worker fails | 2 / 2 | 2 / 1 | 2 | Surviving worker fits both jobs after old attempt is fenced |
| One VM in each pool fails | 2 / 1 | 2 / 1 | 2 | Arithmetic fits; dependency and failover behavior still require testing |
| Worker replacement | 2 / 2 | 3 / 2 | 2 | Provision and verify replacement before drain |
| Worker drain plus another worker failure | 2 / 2 | 3 / 1 | 2 | Remaining worker fits both jobs |
| Trusted replacement | 3 / 2 | 2 / 2 | 2 | Client failover and lease fencing required |
| Trusted drain plus another trusted failure | 3 / 1 | 2 / 2 | 2 | Remaining trusted VM fits both relays |
| Application rollout | 2 / 2 | 2 / 2 | 2 | Permit at most one extra idle service process; no duplicate job ownership |
| Region/shared state outage | Unavailable | Unavailable or unable to proceed safely | 0 | Outside selected HA scope; stop effects safely |

One replacement at a time yields a maximum of **5 VMs / 40 vCPU / 160 GiB**.
Worker replacement produces the maximum disk envelope: **8 disks / 896 GiB**.
Trusted replacement uses 7 disks / 704 GiB and may need a third public address.
These resource-wise maxima form a conservative quota envelope; they are not
instructions to run all replacement scenarios concurrently.

The offline calculator passed 11 HA scenarios and three four-job growth scenarios.
It verifies aggregate arithmetic, not placement, performance, provider availability
or distributed correctness. Per-worker placement must enforce at most two slots;
global admission must enforce at most two active jobs across every controller.

Current per-PR leases are insufficient for this global limit. A stale or
partitioned worker must be stopped or fenced before its slot is reused; rejecting
its result alone does not stop its resource consumption. Workers need bounded
execution authorization, safe cancellation and quarantine. Unknown effect outcomes
must be reconciled with GitHub before retrying a privileged operation.

Current recovery is hourly. Meeting the proposed five-minute target requires a
continuously available recovery loop, bounded detection, verified fencing and
retained checkpoints. A lease timeout alone is not an RTO.

## Storage and retention

| Owner / data | Allocation or usage budget | Retention / reconstruction | Performance and semantics |
| --- | --- | --- | --- |
| All VM boot disks | 4 × 64 GiB = 256 GiB `network_ssd` | Rebuild from pinned OS and deployment artifacts; no unique durable data | Two 32-GiB allocation units: up to 30 MiB/s and 2,000 read/write IOPS per disk |
| Worker scratch/cache | 2 × 192 GiB = 384 GiB `network_ssd` | Delete each job's workspace; replace failed host disks; caches are rebuildable | Six allocation units: up to 90 MiB/s and 6,000 read/write IOPS per disk |
| Approved image cache | 64 GiB within each worker scratch disk | Retain approved immutable images; evict unused digests | Jobs cannot write the shared cache |
| Active workspaces | 2 × 20 GiB worst case on a surviving worker | Destroy after completion; retain only sanitized evidence elsewhere | Proposed explicit filesystem quota, currently not implemented |
| Worker telemetry buffer | 4 GiB within scratch allocation | Rotate and ship; never block cleanup on an unavailable log sink | Job logs must be bounded and sanitized |
| Scratch safety margin | 16 GiB explicitly kept free per worker | Stop admission before depletion | Of 192 GiB, 64 + 40 + 4 + 16 = 124 GiB budgeted; 68 GiB additional spare |
| Regular state bucket | 10 GiB total initial usage budget | Proposed 2 GiB current state + 8 GiB bounded versions/history | Conditional PutObject, ETags, fencing; no filesystem bucket substitute |
| Artifact/backup bucket | 50 GiB total usage budget | 30-day logs/artifacts; seven daily plus four weekly state exports; revisit measured sizes | Independent prefix/permissions for backups; no raw credentials or unrestricted source archives |
| Registry | 50 GiB retained-layer budget | Five approved versions plus two candidates, with garbage collection | Content-addressed layers; actual deduplication and image size unmeasured |

At 20 jobs/day and 20 MiB sanitized evidence per job, 30 days uses about
11.72 GiB. Allow another 6 GiB for logs, up to 22 GiB for eleven 2-GiB state
exports without assuming compression, and 10.28 GiB spare within the 50-GiB
artifact bucket. These are estimates. Backups are a data-recovery mechanism,
not the live HA queue or authority; restoring an old lease must never revive
stale ownership. A restore must reconcile pending effects against GitHub.

Standard Object Storage is billed on actual bytes; 10/50 GiB are planning budgets,
not preallocated disks or automatic hard quotas. Monitor usage and object-version
counts. Do not apply blanket expiry to active or unresolved state. Daily backups
imply up to 24 hours of loss for a destructive-data recovery event, distinct from
the target of no acknowledged-request loss during a VM failure while state is
available. A regional outage has no claimed recovery bound in this plan.

Network SSD performance numbers are documented upper bounds, not measurements;
filesystem metadata operations and encryption can reduce observed throughput.
No shared filesystem, local SSD hardware, disk snapshot or separate database is
required by this baseline. Golden VM image storage is optional and not allocated.

## Networking and authentication

- One dedicated or explicitly reusable VPC, with trusted and worker subnets.
  Budget one non-overlapping `/28` per subnet, or equivalent existing free
  address capacity; exact CIDRs must come from the selected network's pools.
- Four private VM addresses steady and five during replacement. Two public
  IPv4 addresses serve the trusted endpoints; reserve a third for replacement.
  Total allocations: six steady and up to eight during replacement.
- Workers have no public address. Nebius documents default outbound egress using
  a shared dynamic public address for private resources. No separate NAT VM is
  needed unless a fixed source IP becomes a requirement.
- Two security groups, with an initial budget of 20 explicit rules. Permit only
  authenticated job traffic, approved relay traffic, required DNS/time and image
  retrieval. Restrict administrative access to the user's existing secure path.
  Never expose Docker, worker administration or cloud metadata to agent code.
- Two public DNS names and valid TLS certificates for separate job-service
  endpoints; public DNS must be supplied through an appropriate DNS provider.
  Nebius VPC DNS zones are internal and do not replace public DNS.
- GitHub clients need bounded endpoint failover and idempotent request IDs.
  Two A records without client behavior are not sufficient. No single proxy VM
  or undocumented managed standalone load balancer is assumed.
- Separate identities for infrastructure provisioning, registry publishing,
  worker image pulls and trusted state access. Job containers receive only
  bounded attempt/inference capabilities, never registry-write, state, cloud
  provisioning or GitHub write credentials. Long-lived Token Factory credentials
  stay on the trusted side of the VM boundary.

Proposed network qualification budget: 200 Mbps aggregate image/source transfer
at each busy worker, 20 Mbps for control/inference traffic, and 200 concurrent
connections per trusted endpoint. These are test budgets, not verified preset
limits or workload measurements. Model bandwidth and in-flight requests need
measurement independently of job concurrency. An illustrative monthly transfer
budget is 400 GiB: source exports, new image layers, logs/artifacts and retries;
the source archive size and dependency cache hit rate could change it materially.

Registry CI authentication supports service-account short-lived access tokens
or registry-specific static keys. Select and scope the supported method during
implementation; do not assume native GitHub-to-Nebius OIDC federation is available.
Remote job authentication is a separate protocol that must bind repository,
workflow identity, request, candidate, runtime digest, expiry and replay policy.

## Image build and update pipeline

GitHub Actions defines and schedules jobs; every build job executes on a runner.
Use one standard GitHub-hosted Ubuntu runner per image-build job. The current
public repository can use standard hosted runner compute without a paid plan.
No persistent Nebius build VM or self-hosted Actions runner is needed for this
baseline. If a future build exceeds the hosted runner's resources, reassess it.

Proposed update sequence:

1. A scheduled check, for example daily, and manual dispatch discover a new stable
   Codex version. An upstream repository release is not automatically an event
   in this repository; the watcher must be implemented.
2. Update an exact CLI version against a pinned trusted base. Build and scan the
   candidate image on the GitHub runner; publish to a candidate registry path.
3. Run compatibility, Token Factory, skills, isolation and recovery qualification
   against that digest without granting public PRs publishing credentials.
4. Promote only qualified digests for newly admitted jobs; preserve the image
   identity of running requests and retain previous approved versions.

The current Dockerfile pins Codex 0.167.0. Current PR workflows only pull a
qualified digest; neither this watcher nor automatic image promotion exists.
Registry writer credentials belong in protected GitHub environment secrets.
Worker pull credentials are separate and read-only. Image update failure leaves
the previous approved image usable; update availability is not on the job path.

## Quota and regional assessment

The offline selector resolved `region=all`, `inspection=public-docs`, no tenant,
no quota-request mode and multiplier 1. No credentials or cloud APIs were used.
Every regional row in the executive summary has the same alternative demand.
Tenant limits, current usage, free headroom and deficits are **not inspected**.
Project platform eligibility is a later provisioning check. CPU physical capacity
remains unverified; GPU Capacity Advisor is not an applicable CPU-capacity proof.

| Regional quota/resource dimension | Steady incremental demand | Required free replacement envelope | Status |
| --- | --- | --- | --- |
| Regular VMs | 4 | 5 | Tenant limit/usage unknown |
| Total vCPUs for non-GPU VMs | 32 | 40 | Tenant limit/usage unknown; do not invent a separate RAM quota |
| Disks | 6 | 8 | Tenant limit/usage unknown |
| Network SSD disk capacity | 640 GiB | 896 GiB | Confirm exact live quota key/unit before comparison |
| Public IPv4 addresses | 2 | 3 | Tenant limit/usage unknown |
| Allocations, private plus public | 6 | 8 | Tenant limit/usage unknown |
| Networks / subnets / security groups | 1 / 2 / 2 | 1 / 2 / 2 | Reuse not assumed |
| Security rules | Up to 20 | Up to 20 | Final policy remains to be designed |
| Registry count | 1 | 1 | Retained-layer budget 50 GiB; no invented capacity quota |
| Regular buckets / Standard object bytes | 2 / 60 GiB | 2 / 60 GiB | Versions and backups included in budgets |
| GPUs / Kubernetes clusters / filesystems | 0 / 0 / 0 | 0 / 0 / 0 | Not needed for this deployment |

Published defaults are not tenant allowances. Public documentation shows a
default quota of two regular VMs and four disks, below this HA requirement;
some regions publish zero default non-GPU vCPUs. Supplying a tenant permits a
read-only comparison. The required total allowance is existing measured usage
plus the full incremental replacement envelope. No quota increase was submitted,
and quota headroom would not reserve physical capacity.

## Costs

Published USD rates retrieved on the assessment date apply before tax or account
credits. Regional candidates are alternatives; do not sum their costs. Use 730
hours/month for the always-on estimate. The user's compute/inference entitlement
is a supplied premise; storage and supporting-service coverage remain separate.

| Item | Quantity and rate | Monthly list-price estimate |
| --- | --- | --- |
| CPU | 32 vCPU × USD 0.015/hour × 730 | USD 350.40 |
| RAM | 128 GiB × USD 0.0045/hour × 730 | USD 420.48 |
| Network SSD | 640 GiB × USD 0.071/GiB per 730 hours | USD 45.44 |
| Standard object bytes | 60 GiB fully used × USD 0.0147/GiB per 730 hours | USD 0.882 |
| Container Registry | Documented free service | USD 0 |
| Virtual Networks | Documented free service | USD 0 |
| GitHub image-build compute | Standard runner in public repository | USD 0 under current GitHub terms |
| Object API operations | USD 0.0034/1,000 class A; USD 0.00028/1,000 class B | Volume unmeasured; excluded |
| Token Factory | Account/model limits and user's free access | Account-specific; no public invoice estimate |
| Monitoring/logging, DNS, certificates, alert delivery, additional transfer charges | Provider/account coverage not fully priced | Excluded; not assumed free |

Priced subtotal: **USD 817.202/month**, rounded to **USD 817.20**.
One temporary 8-vCPU/32-GiB replacement costs USD 0.264/hour at list prices.
Its disks add 64 GiB for a trusted replacement or 256 GiB for a worker replacement,
prorated by retention. No always-on fifth VM is included in the steady bill.
Estimated user invoice cannot be derived until free-service coverage is known.

## Inputs required before implementation

| Input | What the user needs to provide or identify |
| --- | --- |
| Cloud scope | Explicit tenant ID for read-only quota checks; intended region and project ID for later provisioning |
| Existing identity | Available authenticated profile/service identity and its intended project scope; no secret values in chat |
| Registry | Existing registry ID/path, or permission in a later build task to create one; protected writer and pull-only identity delivery |
| Network | Existing VPC/subnet IDs and secure management path, or authority to create dedicated resources later |
| Public names | Two DNS names under a controlled domain, DNS-provider integration and TLS issuance method |
| State/evidence | Existing private regular buckets to assess for reuse, or new bucket names and intended retention policy |
| Token Factory | Approved review/fix model IDs, secure key-store location, endpoint/account selection and RPM/TPM/concurrency/context limits |
| GitHub | Repository/environment administration for workflows, protected secrets and variables; no GitHub App/PAT is presumed |
| Operations | Alert destination, owner, maintenance process, backup retention and acceptance of a concrete recovery SLO |
| Workload | Typical repository size, expected jobs/day and heaviest validation suite; initial concurrency is already fixed at two |
| Account coverage | Whether free access covers block/object storage, observability and other supporting services as well as compute/inference |

The user need not precreate every VM. An implementation request with exact scope
can authorize provisioning and necessary new credentials, stored only in the
intended secret stores. This assessment itself authorizes neither provisioning
nor IAM changes, public exposure, quota submissions or production enablement.

## Qualification and remaining gates

| Priority | Gap | Required evidence before production qualification |
| --- | --- | --- |
| P0 | Migration contract | Finalize remote-execution design and reconcile hosted-only REQ-001; preserve protected GitHub effects and disabled defaults |
| P0 | Global admission and stale attempts | Prove two-slot admission across both owners; partition a worker and prove termination/fencing before slot reuse |
| P0 | Failover/recovery | Kill each owner immediately after a recovery tick; demonstrate proposed five-minute recovery without duplicate effects or lost acknowledged requests |
| P0 | Failure independence | Obtain provider evidence of appropriate host placement; two VM IDs do not prove independent physical hosts |
| P0 | Runtime isolation | Qualify Codex, skills, mounts, validation and relay networking under pinned runsc; prove no Docker socket/metadata/write credentials reach job code |
| P0 | Authentication and results | Authenticate trusted requests, prevent replay, bind result provenance and revalidate exact GitHub candidate before any effect |
| P0 | Durable state | Exercise conditional-write races, partitions, restore and pending-effect reconciliation on regular Object Storage |
| P1 | Inference limits | Measure model RPM/TPM/context and in-flight calls; relay's 1,000-call cap is not concurrent-request admission |
| P1 | CPU/memory/disk demand | Run two representative worst-case jobs on one surviving worker; no OOM, disk exhaustion or missed recovery budget; record p95 duration/throttling |
| P1 | Network and image startup | Measure source/image transfer and warm startup; test both endpoint failures and allow/deny network cases |
| P1 | Upgrade overlap | Exercise one replacement plus a second failure in that pool; maintain global job limit and usable capacity |
| P1 | Dependencies and observability | Test GitHub, Token Factory, state and registry outages; stop safely, preserve evidence and alert the named owner |
| P1 | Cost and retention | Confirm account coverage, lifecycle exclusions, image retention, backup restore and actual usage alerts |

The source has no global concurrency ceiling, remote HA service or gVisor wiring.
Recovery currently uses the hourly `43 * * * *` schedule. Public documentation
search did not establish a CPU-VM anti-affinity/host-spread contract; Nebius Docs
was consulted first and an official-domain Tavily fallback also left that gap.
Do not translate these gaps into an unsupported production-ready claim.

## Sources and calculation notes

Local evidence:

- [Current requirements](requirements.md) and [design](design.md): implemented
  hosted baseline and the separately recorded production availability intent.
- [Sandbox](../src/github_ai_automation/sandbox.py),
  [stages](../src/github_ai_automation/stages.py),
  [relay](../src/github_ai_automation/relay.py),
  [state](../src/github_ai_automation/state.py) and [Dockerfile](../Dockerfile).
- [PR workflow](../../.github/workflows/github-ai-automation.yml) and
  [recovery workflow](../../.github/workflows/github-ai-recovery.yml).

Official sources, retrieved on the assessment date:

- [Nebius regions and services](https://docs.nebius.com/overview/regions)
- [Exact VM platforms and presets](https://docs.nebius.com/compute/virtual-machines/types)
- [Boot disk images](https://docs.nebius.com/compute/storage/boot-disk-images)
- [Storage types and performance](https://docs.nebius.com/compute/storage/types)
- [Compute quotas](https://docs.nebius.com/compute/resources/quotas-limits)
- [VPC quotas](https://docs.nebius.com/vpc/resources/quotas-limits)
- [Routing and default egress](https://docs.nebius.com/vpc/routing/overview)
- [VPC DNS scope](https://docs.nebius.com/vpc/overview)
- [Registry authentication](https://docs.nebius.com/container-registry/authentication)
- [Registry quotas](https://docs.nebius.com/container-registry/resources/quotas-limits)
- [Object Storage S3 compatibility](https://docs.nebius.com/object-storage/interfaces/s3-api-compatibility)
- [Object Storage quotas](https://docs.nebius.com/object-storage/resources/quotas-limits)
- [Compute prices](https://docs.nebius.com/compute/resources/pricing)
- [Object Storage prices](https://docs.nebius.com/object-storage/resources/pricing)
- [Registry prices](https://docs.nebius.com/container-registry/resources/pricing)
- [Virtual Networks prices](https://docs.nebius.com/vpc/resources/pricing)
- [GitHub runner selection](https://docs.github.com/en/actions/using-jobs/choosing-the-runner-for-a-job)
- [GitHub Actions billing](https://docs.github.com/en/actions/concepts/billing-and-usage)
- [gVisor Docker runtime](https://gvisor.dev/docs/user_guide/quick_start/docker)
- [gVisor platform selection](https://gvisor.dev/docs/user_guide/platforms)

Arithmetic uses the capacity skill's standard-library offline calculator with
millicores, MiB and GiB (1 GiB = 1,024 MiB). VM process groups use its generic
pool/component schema; its Pod-named fields do not imply Kubernetes. OS/runtime
reservations are deducted once before pool capacity; process overhead is then
added explicitly. Runtime measurements are null, not zero. Private calculator
inputs/results retain the complete normalized models; report equations and tables
provide the public-safe audit basis. No cloud deployment or load test was run.
