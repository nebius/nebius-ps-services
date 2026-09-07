# Nebius infrastructure skill

One reusable skill for building and inspecting Nebius infrastructure. Start with
[SKILL.md](SKILL.md), then load the relevant category; the
[service catalog](references/service-catalog.md) distinguishes practical assets,
guidance and integration-only coverage, with official sources and review dates.

## Categories

| Category | Coverage |
| --- | --- |
| [Compute](references/compute.md) | CPU/GPU VMs, images, lifecycle, managed Kubernetes, node groups, versions, quotas and GPU/RDMA readiness |
| [Storage](references/storage.md) | Persistent disks, snapshots, filesystems/CSI, S3 buckets and objects, registry, PostgreSQL and backups |
| [Networking](references/networking.md) | VPC pools/subnets, allocations, routes, security groups, DNS, NAT, VPN and Tunnels |
| [Observability](references/observability.md) | Metrics/logs/traces, agents, dashboards/alerts, delivery checks and Audit Logs concepts |
| [IAM](references/iam.md) | Projects, service accounts, dedicated groups/permits, credentials, federation, KMS and SecretStash metadata |
| [API/SDK](references/api-sdk.md) | Python-first SDK, CLI, REST/gRPC/S3, auth, complete pagination, deadlines and operation reconciliation |

Soperator, Serverless AI and MLflow have
[integration guidance](references/ai-service-integration.md). Terraform, Helm,
cxcli and vpngw retain their lifecycle ownership. The assets have no dependency
on those projects. The [adoption ledger](references/adoption-patterns.md)
records the generic patterns and preserved safety contracts.

## Use the inspectors

Python 3.10+ is required; the offline validation baseline is Python 3.12 and
`nebius==0.6.7`. Use an environment with the pinned SDK installed and configured
credentials. Helpers do not install dependencies, log in, repair auth or change
profiles. Explicit `--profile` or `--config-file` prevents ambient auth selectors
from overriding that selection. All resource scope is explicit.

From this skill directory:

```bash
python scripts/inspect_compute.py --project-id <project-id> --profile <profile> --json
python scripts/inspect_kubernetes.py --project-id <project-id> --cluster-id <cluster-id> --json
python scripts/inspect_storage.py --project-id <project-id> --kind disk --kind bucket --json
python scripts/inspect_vpc_topology.py --project-id <project-id> --network-id <network-id> --json
python scripts/inspect_vpc_routes.py --project-id <project-id> --network-id <network-id> --json
python scripts/inspect_quotas.py --tenant-id <tenant-id> --project-id <project-id> --region <region> --json
```

Each command supports `--help`, `--profile`, `--config-file`, `--endpoint` and
`--json`. Kubernetes without `--cluster-id` lists clusters; with it, checks the
cluster's project and includes the cluster and its node groups. Storage's
repeatable `--kind` accepts `disk`, `filesystem`, `bucket`, `registry`,
`postgresql`; defaults are disk/filesystem/bucket. Quota also supports repeatable
`--region`, `--name`, `--name-prefix`, and `--raw`; at least one tenant/project
selector is required. VPC `--network-id` filters to an exact project network.

All six commands emit `{scope, data, complete, errors}` with `--json`. Quota
also emits `mode: effective|raw`, controlled only by `--raw`. Exit status is
**0** for complete collection, **1** for incomplete/failed collection and **2**
for invalid arguments. Human output shows at most 20 top-level records;
JSON retains all successful collections. `complete` does not prove workload
readiness, sufficient quota, an atomic snapshot or absence of unresolved state.
Cloud inventory output contains environment identifiers; handle it privately.

## Reuse the assets

Add the copied skill's `assets` directory to the caller's Python import path.
Import `sdk.runtime`, `sdk.provision`, `compute.virtual_machine`,
`storage.buckets`, `iam.create_access_key`, or other category modules directly.
Imports and inspector help perform no cloud or credential I/O.

The [compute guide](references/compute.md) has an explicit VM/disk create,
operation-wait and readiness example. Builders only construct typed requests;
callers must preflight dependencies and authorize writes. They do not create an
orchestrator or provide automatic rollback. Use `owned_sdk` for a client created
by the helper; helpers receiving a client leave its lifecycle with the caller.
Do not use synchronous helpers inside an active async event loop.

For an uncertain mutation, retain `CloudError.operation_id`, `.resource_id`
and `.outcome` and reconcile before resubmitting. IDs learned during polling
survive a timeout, and cleanup errors cannot replace the primary failure.
Multi-step IAM creation retains completed group IDs and the pending membership
operation/resource IDs separately. Invalid readiness budgets or callbacks fail
before a create request is submitted.
Credential material uses representations that exclude secrets; this does not
make arbitrary serialization safe. Never log tokens, full resources or requests.

This expansion intentionally changes interfaces without compatibility shims:

- SDK initialization lives in `assets/sdk/runtime.py`; `iam/iam_api.py` is removed.
- Versioned bucket ownership lives in `assets/storage/buckets.py`;
  `iam/ensure_state_bucket.py` is removed.
- Existing service accounts/buckets require explicit IDs for adoption. IAM
  grants require an explicit dedicated single-member group and plain role IDs.
- Access-key creation accepts an explicit key ID to resume secret retrieval;
  token exchange returns `TokenMaterial`, not a secret-bearing tuple.
- Inspector JSON uses the common envelope, replacing prior bare arrays/objects.
- Privileged host shell automation is replaced by the scoped procedure in
  [active diagnostics](references/active-diagnostics.md). GPU proof pods are
  active diagnostics and do not prove end-to-end RDMA performance.

## Validate without cloud access

Create a disposable virtual environment and install the validation dependencies
there. The requirements file pins the complete environment used for this check;
review and rerun schema tests before updating it. This does not install the skill
into an agent runtime.

```bash
python3 -m venv <validation-venv>
<validation-venv>/bin/python -m pip install -r requirements-validation.txt
PYTHONDONTWRITEBYTECODE=1 <validation-venv>/bin/python -m unittest discover -s tests -v
<validation-venv>/bin/python -m ruff check --no-cache assets scripts tests
```

The core suite can run with just the standard library:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_runtime.py -v
```

Tests cover full pagination, failure/timeout handling, selector isolation,
identity/adoption, IAM membership, credential partial success, bucket mismatch,
object integrity, exact SDK constructors, quota schemas and side-effect-free
imports/help. Run the full suite from an isolated copy for portability evidence.
The repository structure validator also checks canonical metadata and trigger CSV.

Treat evidence separately: deterministic tests and SDK serialization are offline
checks; an isolated copy is portability evidence; fresh agent routing is
`RUNTIME_PASS` only after observed invocation; baseline output comparisons are
`QUALITY_PASS` only after grading [quality cases](evals/evals.json). Definition
validation is `STATIC_PASS`. Mark unavailable or unexecuted lanes explicitly.
No source test establishes installed-skill discovery, live provisioning,
authorization, service availability or workload performance.
