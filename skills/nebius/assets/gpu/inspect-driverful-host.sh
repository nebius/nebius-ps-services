#!/usr/bin/env bash
set -euo pipefail

NODE_NAME="${1:?usage: inspect-driverful-host.sh <node-name>}"
POD_NAME="host-inspect-${NODE_NAME//[^a-zA-Z0-9-]/-}"

cleanup() {
  kubectl delete pod "${POD_NAME}" --ignore-not-found=true >/dev/null 2>&1 || true
}

trap cleanup EXIT

kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${POD_NAME}
spec:
  restartPolicy: Never
  nodeName: ${NODE_NAME}
  hostPID: true
  hostNetwork: true
  tolerations:
    - operator: Exists
  containers:
    - name: inspect
      image: ubuntu:24.04
      securityContext:
        privileged: true
      command:
        - /bin/bash
        - -lc
        - |
          chroot /host bash -lc '
            set -e
            echo ===NVIDIA-SMI===
            nvidia-smi || true
            echo ===PKG===
            dpkg -l | egrep "nvidia-container-toolkit|nvidia-driver|cuda|mlx|ofed" || true
            echo ===RUNTIME===
            grep -n "default_runtime_name|BinaryName|nvidia-container-runtime" /etc/containerd/config.toml || true
            echo ===CONFIG===
            test -f /etc/nvidia-container-runtime/config.toml && sed -n "1,120p" /etc/nvidia-container-runtime/config.toml || true
            echo ===MODULES===
            lsmod | egrep "nvidia_peermem|mlx5_core|mlx5_ib|ib_core" || true
            echo ===RDMA-LINK===
            command -v rdma >/dev/null 2>&1 && rdma link || true
            echo ===IBV-DEVICES===
            command -v ibv_devices >/dev/null 2>&1 && ibv_devices || true
          '
      volumeMounts:
        - name: host-root
          mountPath: /host
  volumes:
    - name: host-root
      hostPath:
        path: /
        type: Directory
EOF

kubectl wait --for=jsonpath='{.status.phase}'=Succeeded "pod/${POD_NAME}" --timeout=120s
kubectl logs "${POD_NAME}"
