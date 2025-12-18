#!/usr/bin/env bash
set -euo pipefail

echo "===> Checking notebook and Dask cluster status..."

echo
echo "Current pods in 'default' namespace:"
kubectl get pods -n default
echo

echo "If you haven't yet: open Jupyter, start the Dask cluster from the notebook,"
echo "then I will wait here until the scheduler pod appears."
echo

echo "===> Waiting for Dask scheduler pod to appear..."

# Pattern of scheduler pod name
SCHEDULER_PATTERN="mk8s-dask-cluster-scheduler"

POD=""

# Wait up to ~5 minutes (60 * 5 seconds)
for i in {1..60}; do
  POD=$(kubectl get pod -n default -o name | grep "$SCHEDULER_PATTERN" || true)

  if [[ -n "$POD" ]]; then
    echo "Scheduler pod found: $POD"
    break
  fi

  echo "  Scheduler pod not yet present, retrying in 5s... ($i/60)"
  sleep 5
done

if [[ -z "$POD" ]]; then
  echo
  echo "ERROR: Scheduler pod did not appear in time."
  echo "Current pods in 'default' namespace:"
  kubectl get pods -n default
  exit 1
fi

echo
echo "===> To forward Dask dashboard (http://localhost:8787):"
echo "Run this in another terminal:"
echo "  kubectl port-forward -n default $POD 8787:8787"

echo
echo "✔ Done: Dask scheduler pod detected."
