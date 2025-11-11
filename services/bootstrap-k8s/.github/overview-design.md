# Bootstrap K8s on Nebius — Design Overview

This folder contains `bootstrap-k8s.sh`, an idempotent script that bootstraps the current kubectl context cluster on Nebius. It relies on a local `.env` as the source of truth (created on first run, never modified thereafter).

## What the bootstrap does
- Labels GPU-present nodes (`nvidia.com/gpu.present=true`) with `nvidia.com/gpu.deploy.operands=true` (idempotent).
- Installs NVIDIA GPU Operator and Network Operator via Helm.
- Optionally creates or attaches a Nebius shared filesystem and configures virtiofs mounts for all node groups.
- Installs the Nebius CSI mounted-fs-path driver to provide RWX access for pods.
- Optionally verifies the host mount with a short-lived BusyBox pod.

## Script flags (what they do)
- `--attach-fs NB_FS_ID`: Attach an existing filesystem by ID to all node groups. Ignores `NB_FS_NAME`.
- `--create-fs [NB_FS_NAME]`: Create (or reuse if it exists) a filesystem by name under `PROJECT_ID`, then attach to all node groups. Ignores `NB_FS_ID`. If the name is omitted, uses `NB_FS_NAME` from `.env`.
- `--fs-size-gb NB_FS_SIZE_GIB`: Size to use on creation; required when creating a brand-new filesystem.
- `--install-operators`: Only labels GPU nodes and installs NVIDIA GPU and Network operators; no filesystem actions.

### Flag precedence & filesystem logic
Flags control how `.env` variables are interpreted. The script never mutates `.env`.
1) `--create-fs` provided: use name from flag (or `NB_FS_NAME`), ignore `NB_FS_ID`; reuse by name if present, else create (needs `NB_FS_SIZE_GIB`).
2) `--attach-fs` provided: use `NB_FS_ID` from flag, ignore `NB_FS_NAME`; validate existence under `PROJECT_ID`.
3) Neither flag: if `NB_FS_ID` is set in `.env`, validate and use; else if `NB_FS_NAME` and `NB_FS_SIZE_GIB` are set, attempt creation; otherwise skip filesystem operations.

## Environment and prerequisites
- `.env` is generated on first run and then user-managed. It is never modified by the script.
- Required tools: `kubectl`, `jq`, `helm`, `nebius` CLI.
- Kubernetes current context must point to the target Nebius MK8s cluster.

### Required/optional variables in `.env`
The script expects (at minimum):

export PROJECT_ID=project-EXAMPLE_ID
export NB_FS_ID=filesystem-EXAMPLE_ID
export MOUNT_TAG=csi-storage
export MOUNT_POINT=/mnt/data
export NB_FS_NAME=mk8s-csi-storage
export NB_FS_SIZE_GIB=500
export NB_FS_TYPE=network_ssd
export NB_FS_BLOCK_SIZE=4096

`PROJECT_ID` is mandatory for Nebius operations. `NB_FS_ID` and `NB_FS_NAME` are interpreted according to flag precedence above.

The `.env` is created inline on first run if missing:
```bash
if [ ! -f .env ]; then
  cat > .env <<'EOF'
# Auto-generated, created locally; review and update values as needed
export PROJECT_ID=project-EXAMPLE_ID
export NB_FS_ID=filesystem-EXAMPLE_ID
export MOUNT_TAG=csi-storage
export MOUNT_POINT=/mnt/data
export NB_FS_NAME=mk8s-csi-storage
export NB_FS_SIZE_GIB=500
export NB_FS_TYPE=network_ssd
export NB_FS_BLOCK_SIZE=4096
EOF
  echo "Created .env. Please review and update values if needed."
fi

set -a
. ./.env
set +a
echo "Loaded .env environment variables."
```

## Implementation details
- Generate `.env` if it does not exist using an inline here-doc.
- Load variables from `.env` (never written by the script).
- Preflight checks:
  - Ensure required commands exist.
  - Ensure `PROJECT_ID` is set.
  - Derive the cluster name from the current kube context, stripping the `nebius-mk8s-` prefix when present.
  - Resolve `cluster_id` via: `nebius mk8s cluster get-by-name --name <cluster_name> --parent-id $PROJECT_ID`.
  - Hard-fail if the cluster cannot be resolved in the given project.
- Filesystem lifecycle (per flag precedence above):
  - Create or reuse by name; or attach by id; or skip if neither is specified.
  - Attach the filesystem to all node groups by patching their templates with virtiofs mount and cloud-init user data.
- Operators:
  - Install NVIDIA GPU Operator and Network Operator via Helm.
- CSI driver:
  - Pull the Nebius CSI mounted-fs-path chart (pinned) and install with `dataDir` under the host mount point.

Example Helm CSI install (for reference):
```bash
helm pull \
  oci://cr.eu-north1.nebius.cloud/mk8s/helm/csi-mounted-fs-path \
  --version 0.1.3

# Install the chart:

helm upgrade csi-mounted-fs-path ./csi-mounted-fs-path-0.1.3.tgz --install \
  --set dataDir=$MOUNT_POINT/csi-mounted-fs-path-data/
```

## Verification and idempotency
- A one-off BusyBox pod can be used to validate the host mount when `--verify-mount` is provided.
- Script runs are idempotent: labels, operators, and node-group patches can be applied repeatedly.

## References
- Nebius filesystem over CSI: https://docs.nebius.com/kubernetes/storage/filesystem-over-csi
- Nebius CLI reference: https://docs.nebius.com/cli