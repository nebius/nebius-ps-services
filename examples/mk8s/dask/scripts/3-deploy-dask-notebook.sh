#!/usr/bin/env bash
set -euo pipefail

source ./environment.sh

echo "===> Installing Dask operator..."
helm repo add dask https://helm.dask.org
helm repo update
helm install dask-operator dask/dask-kubernetes-operator || echo "Already installed"

echo "===> Applying RBAC for notebook..."
kubectl apply -f yamls/mda-notebook-rbac.yaml

echo "===> Rendering notebook pod template..."
export REGISTRY_ID=$(terraform -chdir=terraform output -raw registry_id)
export REGISTRY_PATH=$(echo "$REGISTRY_ID" | cut -d- -f2)
envsubst < yamls/pod.yaml.tpl | kubectl apply -f -

echo "===> Waiting for notebook pod..."
kubectl wait --for=condition=Ready pod/mda-notebook --timeout=180s

echo
echo "===> Jupyter is ready (http://localhost:8889/lab/)"
echo "Run this in another terminal to access it:"
echo "  kubectl port-forward pod/mda-notebook 8889:8889 -n default"
echo
echo "Then open:"
echo "  http://localhost:8889/lab/tree/project/run_from_pod.ipynb"
echo
echo "From the notebook UI, start the Dask cluster."
echo "When the cluster is running, use 'scripts/4-dask-status.sh' to wait for the scheduler and get dashboard port-forward command."

echo
echo "✔ Done: Dask operator + notebook deployed."
