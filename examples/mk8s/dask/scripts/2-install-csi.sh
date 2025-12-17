#!/usr/bin/env bash
set -euo pipefail

echo "===> Reading terraform outputs..."
export MOUNT_TAG=$(terraform -chdir=terraform output -raw mount_tag)
export NB_CLUSTER_ID=$(terraform -chdir=terraform output -raw nb_cluster_id)

echo "===> Getting kubeconfig..."
nebius mk8s cluster get-credentials \
  --id "$NB_CLUSTER_ID" --external --force

echo "===> Downloading CSI Helm chart..."
helm pull oci://cr.eu-north1.nebius.cloud/mk8s/helm/csi-mounted-fs-path --version 0.1.3

echo "===> Installing CSI chart..."
helm upgrade csi-mounted-fs-path ./csi-mounted-fs-path-0.1.3.tgz --install \
  --set dataDir=/mnt/$MOUNT_TAG/csi-mounted-fs-path-data/

rm csi-mounted-fs-path-0.1.3.tgz

echo "===> Applying CSI PVC + Pod..."
kubectl apply -f yamls/csi-pvc-and-pod.yaml

echo "===> Waiting for pod..."
kubectl wait --for=condition=Ready pod/my-csi-app --timeout=180s

echo "===> Copying project into pod..."
kubectl cp project/. my-csi-app:/project

echo "✔ Done: CSI installed and project copied."
