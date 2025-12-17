# Project Setup & Automation Guide

This repository automates deployment to Nebius using Terraform, Docker, Kubernetes, CSI volumes, and Dask.

## Folder Structure

```
.
├─ environment.sh
├─ terraform/
├─ scripts/
│  ├─ 0-bootstrap.sh
│  ├─ 1-build-and-push.sh
│  ├─ 2-install-csi.sh
│  └─ 3-deploy-dask.sh
└─ yamls/
```

## 1. Environment Variables

Create a file named `environment.sh` in the project root:

```
export NEBIUS_TENANT_ID="YOUR_TENANT"
export NEBIUS_PROJECT_ID="YOUR_PROJECT"
export NEBIUS_REGION="eu-north1"
```

Do not commit this file.

The bootstrap script automatically sources it.

## 2. Bootstrap Nebius + Terraform

Run:

```
scripts/0-bootstrap.sh
```

This script:

- Sources `environment.sh`
- Sets Nebius profile parent-id
- Creates Terraform service account
- Generates IAM auth keys
- Assigns admin permissions to the service account
- Runs `terraform init/validate/apply` inside `terraform/`

After completion, all Terraform resources are deployed.

## 3. Build and Push Docker Image

```
scripts/1-build-and-push.sh
```

This script:

- Reads Terraform outputs (`registry_id`, `nebius_region`)
- Builds Docker image using Buildx
- Tags it for Nebius Container Registry
- Pushes it to the registry path derived from Terraform

## 4. Install CSI and Upload Project Code

```
scripts/2-install-csi.sh
```

This script:

- Loads Kubernetes credentials for the Nebius MK8s cluster
- Downloads and installs the CSI mounted filesystem Helm chart
- Applies PVC and example Pod manifests
- Waits for the Pod to become ready
- Copies local `project/` contents into the running Pod

Result: a Pod with a mounted filesystem containing your project.

## 5. Deploy Dask Operator and Notebook

```
scripts/3-deploy-dask.sh
```

This script:

- Installs Dask Kubernetes Operator via Helm
- Applies notebook RBAC rules
- Renders `pod.yaml.tpl` via `envsubst` and deploys it
- Waits for the notebook Pod to become ready
- Prints port-forwarding commands

Access:

- JupyterLab → http://localhost:8889/lab/
- Dask Dashboard → http://localhost:8787

## 6. Helpful Commands

List pods:

```
kubectl get pods
```

Port-forward to Jupyter:

```
kubectl port-forward pod/mda-notebook 8889:8889
```

Port-forward Dask dashboard:

```
POD=$(kubectl get pod -n default -o name | grep mk8s-dask-cluster-scheduler)
kubectl port-forward -n default $POD 8787:8787
```

## 7. Full Automation Pipeline

```
scripts/0-bootstrap.sh
scripts/1-build-and-push.sh
scripts/2-install-csi.sh
scripts/3-deploy-dask.sh
```

After all steps, the Nebius infrastructure is provisioned, Docker images are built and deployed, CSI storage is attached, and Dask Notebook + cluster are running.
