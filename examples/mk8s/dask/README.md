# Project Setup & Automation Guide

This repository automates deployment to Nebius using Terraform, Docker, Kubernetes, CSI volumes, and Dask.

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
scripts/3-deploy-dask-notebook.sh
```

This script:

- Installs Dask Kubernetes Operator via Helm
- Applies notebook RBAC rules
- Reads registry information from Terraform outputs
- Renders `pod.yaml.tpl` via `envsubst` and deploys it
- Waits for the notebook Pod to become ready
- Prints Jupyter port-forward instructions

Access JupyterLab:

- Port-forward:
  ```
  kubectl port-forward pod/mda-notebook 8889:8889 -n default
  ```
- Open:
  - http://localhost:8889/lab/

From the notebook UI, you can start the Dask cluster.

## 6. Check Dask Cluster Status & Dashboard

```
scripts/4-dask-status.sh
```

This script:

- Shows current pods in the `default` namespace
- Waits for the Dask scheduler pod to appear
- Prints the Dask dashboard port-forward command when the scheduler is detected

Typical usage after starting the cluster from the notebook:

```
scripts/4-dask-status.sh
```

The script will produce a command like:

```
kubectl port-forward -n default pod/mk8s-dask-cluster-scheduler-... 8787:8787
```

Dask Dashboard:

- http://localhost:8787

## 7. Helpful Commands

List pods:

```
kubectl get pods
```

Port-forward to Jupyter:

```
kubectl port-forward pod/mda-notebook 8889:8889 -n default
```

Port-forward Dask dashboard (manual variant):

```
POD=$(kubectl get pod -n default -o name | grep mk8s-dask-cluster-scheduler)
kubectl port-forward -n default $POD 8787:8787
```

## 8. Full Automation Pipeline

Run the scripts in order:

```
scripts/0-bootstrap.sh
scripts/1-build-and-push.sh
scripts/2-install-csi.sh
scripts/3-deploy-dask-notebook.sh
scripts/4-dask-status.sh
```

After these steps, the Nebius infrastructure is provisioned, Docker images are built and deployed, CSI storage is attached, and the Dask Notebook + cluster are running.

### 9. Delete (optional)

Example cleanup commands:

```
kubectl delete daskcluster mk8s-dask-cluster
kubectl delete deployment dask-operator-dask-kubernetes-operator
kubectl delete pod mda-notebook
helm uninstall dask-operator
```

Use these as needed to tear down the Dask cluster, operator, and notebook resources.
