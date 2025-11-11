# Nebius MK8s Bootstrap Script

`bootstrap-k8s.sh` prepares the current Nebius Managed Kubernetes (MK8s) cluster for GPU workloads and optional shared filesystem access. It is idempotent, relies on a local `.env` (source of truth), and hard‑fails if the active kubectl context does not map to a Nebius cluster in your project.

## Features
- GPU node labeling (`nvidia.com/gpu.present=true` -> `nvidia.com/gpu.deploy.operands=true`).
- NVIDIA GPU Operator + Network Operator install via Helm.
- Optional filesystem create or attach with virtiofs mount on all node groups.
- Nebius CSI mounted-fs-path driver install (RWX host path access).
- Optional mount verification using a temporary BusyBox pod.

## Prerequisites
Install and configure:
- `kubectl` with context pointing to the target Nebius MK8s cluster.
- `helm`
- `jq`
- `nebius` CLI (authenticated; same project as `PROJECT_ID`).

## .env (Created Once, Never Mutated)
On first run the script generates `.env` if missing. Edit it manually afterward.
Required base variables:
```bash
export PROJECT_ID=project-EXAMPLE_ID          # REQUIRED: Nebius project
export NB_FS_ID=filesystem-EXAMPLE_ID         # Optional existing filesystem id
export NB_FS_NAME=mk8s-csi-storage            # Optional filesystem name (for creation/reuse)
export NB_FS_SIZE_GIB=500                     # Size GiB (required for creation)
export NB_FS_TYPE=network_ssd                 # Filesystem type
export NB_FS_BLOCK_SIZE=4096                  # Block size bytes
export MOUNT_TAG=csi-storage                  # virtiofs tag
export MOUNT_POINT=/mnt/data                  # Host mount point
export VERIFY_MOUNT=false                     # Enable via flag normally
export CSI_HELM_WAIT=true                     # Use helm --wait for CSI chart
export CSI_TIMEOUT=10m                        # Helm wait timeout
```

## Flag Precedence
1. `--create-fs [NAME]`: Create/reuse filesystem by name; ignores `NB_FS_ID`.
2. `--attach-fs ID`: Attach existing filesystem by ID; ignores `NB_FS_NAME`.
3. `--attached-fs ID`: Verification-only; do not create or attach, just check the existing mount. Requires `--verify-mount` and that `MOUNT_TAG`/`MOUNT_POINT` are set.
4. No FS flags: env-driven best effort (use `NB_FS_ID` if present; else try create with `NB_FS_NAME` + `NB_FS_SIZE_GIB`; else skip).

Mount verification only runs if a filesystem action occurred and `--verify-mount` (or `VERIFY_MOUNT=true`) is set.

## Usage Examples
```bash
# Operators only (no filesystem work)
./bootstrap-k8s.sh --install-operators

# Create filesystem using name from .env, verify mount
./bootstrap-k8s.sh --create-fs --verify-mount

# Create / reuse named filesystem with size override
./bootstrap-k8s.sh --create-fs team-fs --fs-size-gb 750 --verify-mount

# Attach existing filesystem by ID, skip verification
./bootstrap-k8s.sh --attach-fs filesystem-abc123

# Attach existing filesystem and verify
./bootstrap-k8s.sh --attach-fs filesystem-abc123 --verify-mount

# Verify an already-attached filesystem without modifying the cluster
./bootstrap-k8s.sh --attached-fs filesystem-abc123 --verify-mount
```

## Quick Validation
After a successful run you can validate core components quickly.

```bash
# 1. Confirm cluster context and derived cluster name
kubectl config current-context

# 2. GPU operator pods healthy
kubectl get pods -n nvidia-gpu-operator

# 3. Network operator pods healthy
kubectl get pods -n nvidia-network-operator

# 4. CSI mounted-fs-path driver pods (search globally)
kubectl get pods -A | grep csi-mounted-fs-path || echo "CSI driver pods not found"

Expected results:
- Operators namespaces show pods in `Running` state.
- At least one CSI driver pod present.
- `mount-check` logs include your `MOUNT_POINT` path and a virtiofs mount line.
 - If you ran the script with `--verify-mount`, the probe pod logs print `MOUNT_OK` on success (or `MOUNT_MISSING` if the virtiofs mount isn't present).

## What Happens Internally
1. Generate `.env` if missing, then load it.
2. Preflight: check required commands, ensure `PROJECT_ID`, derive cluster name (strip `nebius-mk8s-`), resolve cluster id (hard-fail if missing).
3. Filesystem lifecycle per flags (create, attach, or skip) under `PROJECT_ID`.
4. Patch all node groups with virtiofs mount + cloud-init, if filesystem present.
5. Label GPU nodes.
6. Install GPU + Network operators.
7. Install CSI mounted-fs-path driver (pinned chart version).
8. (Optional) Run BusyBox probe pod to verify mount.
9. Exit with success message.

## Troubleshooting
| Symptom | Likely Cause | Action |
|---------|--------------|--------|
| Error: PROJECT_ID must be set | Missing value in `.env` | Edit `.env`, re-run |
| Cluster not found | Wrong kubectl context or project | `kubectl config current-context`; verify project; re-auth `nebius` CLI |
| Filesystem create fails | Missing size or invalid type | Provide `--fs-size-gb` or set `NB_FS_SIZE_GIB`; check docs |
| Mount verification skipped | No filesystem action | Use `--create-fs` or `--attach-fs` plus `--verify-mount` |
| GPU nodes not labeled | No nodes expose GPU | Confirm `kubectl get nodes -L nvidia.com/gpu.present=true` |

## References
- Design doc: `.github/overview-design.md`
- Nebius filesystem over CSI: https://docs.nebius.com/kubernetes/storage/filesystem-over-csi
- Nebius CLI: https://docs.nebius.com/cli
- NVIDIA GPU Operator: https://docs.nvidia.com/datacenter/cloud-native/gpu-operator

