# AI service selection and infrastructure integration

Reviewed 2026-09-07. These services receive selection and integration guidance;
this skill does not maintain duplicate lifecycle automation for them.

| Workload | Service | Infrastructure checks |
| --- | --- | --- |
| Slurm scheduling and distributed training | Soperator | Deployment ownership, supported GPU/fabric tuple, Kubernetes/node health, shared filesystem identity, Slurm/job readiness |
| Containerized on-demand jobs or endpoints | Serverless AI | Image/registry access, resource shape, data/secrets access, endpoint auth and exposure, job/endpoint lifecycle and logs |
| Experiment tracking and model registry | Managed MLflow | Service availability, project/network, authentication, artifact storage, access and backup/export requirements |

[Soperator deployment methods](https://docs.nebius.com/slurm-soperator/deploy/overview)
include distinct ownership models. Identify managed service versus self-managed
installation before acting. When cxcli owns the deployment, its supported
install/upgrade/recovery path and protected-storage identity remain authoritative.
An independently healthy Kubernetes cluster does not prove Slurm/GPU job health.

For [Serverless AI](https://docs.nebius.com/serverless/overview), choose a finite
job versus serving endpoint from the workload lifecycle. Verify public service
availability and resource limits; do not assume a VM preset maps directly to a
serverless request. Keep registry images pinned by digest, secrets in the
supported secret integration, and object data in the correct region/scope.

For [Managed MLflow](https://docs.nebius.com/mlflow), follow the current public
cluster lifecycle rather than creating an ad-hoc Kubernetes release. Verify a
scoped tracking/artifact operation independently of service provisioning. Such
operations write experiment data and require authority.

In all three cases, document dependencies, IAM, quota/cost drivers, signal
collection, lifecycle owner and independent application acceptance. Do not add a
new agent runtime, model selection framework or scheduler to the Nebius skill.
