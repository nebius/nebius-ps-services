# Dask on Nebius — Project README

This repository contains scripts, Terraform configuration, and Kubernetes manifests
to deploy a Molecular Dynamics + Dask workflow on **Nebius MK8s** using a custom Jupyter
notebook image and a persistent CSI-mounted project directory.

---

## Repository Structure

```
.
├── docker/                     # Docker image for the notebook + MD analysis
├── project/                    # Input trajectory + example notebook
├── scripts/                    # Deployment automation
├── terraform/                  # Nebius infrastructure
└── yamls/                      # Kubernetes manifests (CSI, RBAC, pod template)
```

---

# Deployment Workflow

Below is an overview of each script in the `scripts/` directory and what it does.

---

## 1. `scripts/0-bootstrap.sh`
Initializes Nebius environment variables, fetches an IAM token, discovers VPC subnet,
and exports Terraform variables.

```bash
scripts/0-bootstrap.sh
```

This script sets:

- `NEBIUS_TENANT_ID`
- `NEBIUS_PROJECT_ID`
- `NEBIUS_REGION`
- IAM token
- VPC subnet (`NEBIUS_VPC_SUBNET_ID`)
- Terraform variables (`TF_VAR_*`)

After running it, Terraform and the subsequent scripts will have all required context.

---

## 2. `scripts/1-build-and-push.sh`
Builds the custom Docker image and pushes it to Nebius Container Registry defined by Terraform outputs.

```bash
scripts/1-build-and-push.sh
```

Actions:

- Reads `registry_id` from Terraform
- Logs into Nebius registry
- Builds `mda-dask` Docker image
- Tags & pushes the image to `cr.<region>.nebius.cloud/.../mda-dask:latest`

---

## 3. `scripts/2-install-csi.sh`
Installs the CSI driver, creates a persistent volume, and copies the project directory into the pod.

```bash
scripts/2-install-csi.sh
```

Actions:

- Reads Terraform outputs (`mount_tag`, `nb_cluster_id`)
- Fetches kubeconfig for the MK8s cluster
- Installs Nebius CSI Helm chart
- Applies `yamls/csi-pvc-and-pod.yaml`
- Waits for pod `my-csi-app` to become ready
- Copies the local `project/` directory into the persistent volume

---

## 4. `scripts/3-deploy-dask-notebook.sh`
Deploys the Dask Operator and launches the notebook pod.

```bash
scripts/3-deploy-dask-notebook.sh
```

Actions:

- Installs `dask-kubernetes-operator` Helm chart
- Applies notebook RBAC
- Renders `yamls/pod.yaml.tpl` using `envsubst`
- Deploys notebook pod `mda-notebook`
- Provides instructions for port-forwarding:
  ```
  kubectl port-forward pod/mda-notebook 8889:8889
  ```
- Jupyter Lab becomes available at:  
  **http://localhost:8889/lab/tree/project/run_from_pod.ipynb**

Inside Jupyter, start the Dask cluster from the notebook.

---

## 5. `scripts/4-dask-status.sh`
Waits for the Dask scheduler pod to appear and prints port-forward instructions.

```bash
scripts/4-dask-status.sh
```

Actions:

- Lists running pods
- Waits for the scheduler pod (`mk8s-dask-cluster-scheduler*`)
- When detected, prints:
  ```
  kubectl port-forward <scheduler-pod> 8787:8787
  ```
- Dask dashboard becomes available at:  
  **http://localhost:8787**

---

# Typical End-to-End Usage

```bash
scripts/0-bootstrap.sh
scripts/1-build-and-push.sh
scripts/2-install-csi.sh
scripts/3-deploy-dask-notebook.sh
scripts/4-dask-status.sh
```

Open Jupyter, start the Dask cluster from the notebook, then forward the dashboard.

---

# Optional Cleanup / Deletion

To remove deployed resources:

```bash
kubectl delete daskcluster mk8s-dask-cluster
kubectl delete pod mda-notebook
helm uninstall dask-operator
kubectl delete -f yamls/csi-pvc-and-pod.yaml
kubectl delete daemonset csi-mounted-fs-path-plugin
```
