#!/usr/bin/env bash
set -euo pipefail

GPU_NAMESPACE="${GPU_NAMESPACE:-nvidia-gpu-operator}"
NETWORK_NAMESPACE="${NETWORK_NAMESPACE:-nvidia-network-operator}"
NODE_NAME="${1:-}"

echo "== Node labels =="
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\tGPU="}{.metadata.labels.feature\.node\.kubernetes\.io/pci-10de\.present}{"\tNIC="}{.metadata.labels.feature\.node\.kubernetes\.io/pci-15b3\.present}{"\n"}{end}'

echo
echo "== Allocatable GPU and RDMA resources =="
kubectl get nodes -o json \
  | jq -r '.items[]
    | "\(.metadata.name)\tGPU=\(.status.allocatable["nvidia.com/gpu"] // "-")\tRDMA_SHARED=\(.status.allocatable["rdma/shared_device"] // "-")"'

echo
echo "== GPU Operator pods =="
kubectl get pods -n "${GPU_NAMESPACE}" -o wide

echo
echo "== GPU Operator daemonsets =="
kubectl get ds -n "${GPU_NAMESPACE}"

echo
echo "== Network Operator pods =="
kubectl get pods -n "${NETWORK_NAMESPACE}" -o wide

echo
echo "== Network Operator daemonsets =="
kubectl get ds -n "${NETWORK_NAMESPACE}"

echo
echo "== ClusterPolicy status =="
kubectl get clusterpolicy -n "${GPU_NAMESPACE}" cluster-policy -o json \
  | jq '{state:.status.state, conditions:.status.conditions}'

if kubectl get nicclusterpolicy -n "${NETWORK_NAMESPACE}" nic-cluster-policy >/dev/null 2>&1; then
  echo
  echo "== NicClusterPolicy status =="
  kubectl get nicclusterpolicy -n "${NETWORK_NAMESPACE}" nic-cluster-policy -o json \
    | jq '{state:.status.state, appliedStates:.status.appliedStates}'
fi

if [[ -n "${NODE_NAME}" ]]; then
  echo
  echo "== Allocatable resources on ${NODE_NAME} =="
  kubectl describe node "${NODE_NAME}" | sed -n '/Allocatable:/,/Events:/p'
fi
