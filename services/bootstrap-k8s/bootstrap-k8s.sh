#!/usr/bin/env bash

# bootstrap-k8s.sh
#
# Bootstraps the current kubectl context cluster following .github/overview-design.md:
# - Generate and load .env on first run (never commit .env)
# - Label GPU nodes with nvidia.com/gpu.deploy.operands=true
# - Install NVIDIA GPU and Network Operators via Helm
# - Create or reuse a Nebius shared filesystem (optional, via args or .env)
# - Install Nebius CSI mounted-fs-path chart for RWX access
# - Optionally verify mount via a short-lived BusyBox pod on a single node
#
# Args (override .env values when provided):
#   --attach-fs NB_FS_ID
#   --create-fs [NB_FS_NAME]
#   --fs-size-gb NB_FS_SIZE_GIB
#   --verify-mount             # only verifies mount when explicitly provided; requires --attach-fs or --create-fs
#   --install-operators        # only label and install operators, no filesystem ops

set -Eeuo pipefail
IFS=$'\n\t'

# ------------------ Utilities ------------------
# log: info-level message (stdout)
log(){ printf "[INFO] %s\n" "$*"; }
# warn: non-fatal warning (stderr) continues execution
warn(){ printf "[WARN] %s\n" "$*" >&2; }
# error: error message (stderr); callers decide to exit
error(){ printf "[ERROR] %s\n" "$*" >&2; }
# require_cmd: ensure a binary exists or exit 1
require_cmd(){ command -v "$1" >/dev/null 2>&1 || { error "Required command '$1' not found"; exit 1; }; }
# have_cmd: test if a binary exists (returns 0/1)
have_cmd(){ command -v "$1" >/dev/null 2>&1; }

# usage: print help/flags and exit (caller handles exit code)
usage(){
  cat <<'USAGE'
bootstrap-k8s.sh - Nebius MK8s cluster bootstrap.

USAGE:
  ./bootstrap-k8s.sh [flags]

FLAGS:
  --attach-fs NB_FS_ID          Attach an existing filesystem by ID. When provided, NB_FS_NAME is ignored.
  --create-fs [NB_FS_NAME]      Create (or reuse by name) a filesystem. When provided, NB_FS_ID is ignored. If name not given, uses NB_FS_NAME from .env.
  --attached-fs NB_FS_ID        Verify an already attached filesystem without changing cluster/node-group config (no create/attach operations).
  --fs-size-gb <GiB>            Size (integer GiB) required when creating a new filesystem if it doesn't already exist.
  --verify-mount                Run one-off BusyBox pod to verify host mount (requires attach/create filesystem).
  --install-operators           Only install NVIDIA operators (skip filesystem & CSI steps).
  --help, -h                    Show this help and exit.

NOTES:
  - .env is the source of truth and is never modified by this script at runtime.
  - PROJECT_ID is required for Nebius operations and must be set in .env.
  - On first run, a .env template is created and the script exits; edit .env and re-run.
  - Flag precedence:
      * --create-fs: ignores NB_FS_ID, uses NB_FS_NAME (from flag or .env)
      * --attach-fs: ignores NB_FS_NAME, uses NB_FS_ID (from flag)
    If neither flag is provided: the script will skip filesystem actions unless NB_FS_ID or NB_FS_NAME in .env indicate intent.

EXAMPLES:
  # Install operators only
  ./bootstrap-k8s.sh --install-operators

  # Create new filesystem using values from .env and verify mount
  ./bootstrap-k8s.sh --create-fs --verify-mount

  # Create new filesystem named team-fs with size override, then verify
  ./bootstrap-k8s.sh --create-fs team-fs --fs-size-gb 750 --verify-mount

  # Attach existing filesystem and skip mount verification
  ./bootstrap-k8s.sh --attach-fs filesystem-abc123

  # Attach existing filesystem and verify mount
  ./bootstrap-k8s.sh --attach-fs filesystem-abc123 --verify-mount

EXIT CODES:
  0 success
  1 missing required command
  2 invalid arguments / missing required values
USAGE
}

# mktemp_dir: create a temp directory (portable fallbacks) and print path
mktemp_dir(){
  local d
  if d=$(mktemp -d -t nbk8s.XXXXXX 2>/dev/null); then printf "%s\n" "$d"; return 0; fi
  if d=$(mktemp -d 2>/dev/null); then printf "%s\n" "$d"; return 0; fi
  d="/tmp/nbk8s.$$"; mkdir -p "$d"; printf "%s\n" "$d"
}

# update_env_var: intentionally disabled (immutability of .env enforced)
update_env_var(){ :; }

# ------------------ Env management ------------------
# generate_env_if_missing: write initial .env template if absent (never overwrites)
generate_env_if_missing(){
  if [[ ! -f .env ]]; then
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
export VERIFY_MOUNT=false
export CSI_HELM_WAIT=true
export CSI_TIMEOUT=10m
EOF
    log "Created .env. Edit values (at minimum PROJECT_ID, NB_FS_ID or NB_FS_NAME) then re-run the script. Exiting early to prevent accidental bootstrap with placeholder values."
    exit 0
  fi
}

# load_env: source .env exporting variables (after optional generation)
load_env(){
  # shellcheck disable=SC1091
  set -a; . ./.env; set +a
  log "Loaded .env environment variables."
}

# parse_args: interpret CLI flags, setting globals that override .env
parse_args(){
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --attach-fs) ATTACH_FS=true; NB_FS_ID="$2"; shift 2;;
      --attached-fs) ATTACHED_FS=true; NB_FS_ID="$2"; shift 2;;
      --create-fs)
        CREATE_FS=true
        # Optional value; if next token is not another flag, treat it as the name
        if [[ $# -ge 2 && "$2" != --* ]]; then NB_FS_NAME="$2"; shift 2; else shift 1; fi
        ;;
      --fs-size-gb) NB_FS_SIZE_GIB="$2"; shift 2;;
      --verify-mount) VERIFY_MOUNT=true; shift;;
      --install-operators) ONLY_INSTALL_OPERATORS=true; shift;;
      --help|-h) usage; exit 0;;
      *) error "Unknown arg: $1"; exit 2;;
    esac
  done
}

# preflight: validate required tools, env vars, kube context, cluster existence, and mount preconditions
preflight(){
  require_cmd kubectl; require_cmd jq; require_cmd helm; require_cmd nebius
  if [[ -z "${PROJECT_ID:-}" ]]; then
    error "PROJECT_ID must be set in .env (required for Nebius operations)."; exit 2
  fi
  # Validate current kube context and matching Nebius cluster
  local ctx cname cid
  ctx=$(get_current_context)
  if [[ -z "$ctx" ]]; then
    error "No current kubectl context; set context to your Nebius MK8s cluster and retry."; exit 2
  fi
  cname=$(get_current_cluster_name || true)
  if [[ -z "$cname" ]]; then
    error "Unable to derive cluster name from current context '$ctx'"; exit 2
  fi
  cid=$(get_cluster_id_from_nebius "$cname" || true)
  if [[ -z "$cid" ]]; then
    error "Nebius cluster '$cname' not found under project '$PROJECT_ID'"; exit 2
  fi
  log "Target cluster: name=${cname}, id=${cid}"
  # Filesystem hint (skip hint if verification-only)
  if [[ -z "${NB_FS_ID:-}" && -z "${NB_FS_NAME:-}" && "${ONLY_INSTALL_OPERATORS:-false}" != "true" && "${ATTACHED_FS:-false}" != "true" ]]; then
    warn "No filesystem args provided; will skip filesystem creation."
  fi
  if [[ -z "${MOUNT_POINT:-}" ]]; then
    error "MOUNT_POINT is not set (set in .env or export MOUNT_POINT=/path)."; exit 2
  fi

  # If user requests mount verification, ensure a filesystem operation is specified
  if [[ "${VERIFY_MOUNT:-false}" == "true" ]]; then
    # Allow verify with any of: attach/create intent, or attached-fs (verification-only)
    if [[ -z "${NB_FS_ID:-}" && -z "${NB_FS_NAME:-}" ]]; then
      error "--verify-mount requires one of: --attach-fs NB_FS_ID, --create-fs [NAME], or --attached-fs NB_FS_ID"; exit 2
    fi
    if [[ -z "${MOUNT_TAG:-}" ]]; then
      error "MOUNT_TAG must be set to verify a virtiofs mount (set in .env)."; exit 2
    fi
  fi

  # If verification-only mode is requested, validate the filesystem id exists
  if [[ "${ATTACHED_FS:-false}" == "true" ]]; then
    if [[ -z "${NB_FS_ID:-}" ]]; then
      error "--attached-fs requires a filesystem id"; exit 2
    fi
    if ! nebius compute filesystem get --id "$NB_FS_ID" --parent-id "$PROJECT_ID" >/dev/null 2>&1; then
      error "Filesystem id '$NB_FS_ID' not found under project '$PROJECT_ID'"; exit 2
    fi
  fi
}

# ------------------ Cluster helpers ------------------
# get_current_context: return active kubectl context name (empty on failure)
get_current_context(){ kubectl config current-context 2>/dev/null || true; }

# get_current_cluster_name: derive normalized Nebius cluster name from kube context (strip provider prefix)
get_current_cluster_name(){
  local ctx; ctx=$(get_current_context)
  [[ -z "$ctx" ]] && return 0

  # Prefer the cluster name referenced by the current context; if missing, fall back to context name
  local cname
  cname=$(kubectl config view -o json | jq -r --arg ctx "$ctx" '.contexts[]?|select(.name==$ctx).context.cluster // empty' 2>/dev/null || true)
  if [[ -z "$cname" || "$cname" == "null" ]]; then
    cname="$ctx"
  fi

  # Normalize: trim whitespace, strip scheme if a URL sneaks in, and drop provider prefix
  cname=$(printf '%s' "$cname" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//; s|^[^:]+://||')
  cname="${cname#nebius-mk8s-}"

  printf '%s\n' "$cname"
}

# get_cluster_id_from_nebius: resolve cluster uuid by name+project (returns empty if not found)
get_cluster_id_from_nebius(){
  [[ -z "${NEBIUS_PROFILE:-}" ]] && true # optional
  have_cmd nebius || return 0
  local cname="$1"; [[ -z "$cname" ]] && return 0
  if [[ -z "${PROJECT_ID:-}" ]]; then
    error "PROJECT_ID must be set before retrieving cluster id"; return 2
  fi
  # Precise lookup (mandatory parent-id)
  local j id
  j=$(nebius mk8s cluster get-by-name --name "$cname" --parent-id "$PROJECT_ID" --format json 2>/dev/null || true)
  if [[ -n "$j" ]]; then
    id=$(printf '%s' "$j" | jq -r '.metadata.id // .id // empty' 2>/dev/null || true)
    if [[ -n "$id" && "$id" != "null" ]]; then
      printf '%s\n' "$id"; return 0
    fi
  fi
  return 0
}

# ------------------ Filesystem ensure/create/attach ------------------
# ensure_filesystem: implement flag/env precedence to obtain NB_FS_ID (create, attach, or skip)
ensure_filesystem(){
  # Flag precedence: --create-fs overrides NB_FS_ID; --attach-fs uses NB_FS_ID and ignores NB_FS_NAME
  if [[ "${CREATE_FS:-false}" == "true" ]]; then
    NB_FS_ID="" # ignore any NB_FS_ID when creating
    if ! have_cmd nebius; then warn "Nebius CLI not available; skipping filesystem creation."; return 0; fi
    if [[ -z "${NB_FS_NAME:-}" ]]; then
      error "--create-fs requires a filesystem name (pass --create-fs <name> or set NB_FS_NAME in .env)"; exit 2
    fi
    local existing_id size type block
    size="${NB_FS_SIZE_GIB:-}"; type="${NB_FS_TYPE:-network_ssd}"; block="${NB_FS_BLOCK_SIZE:-4096}"
    # Prefer precise lookup via get-by-name (avoids full list + jq filtering)
    existing_id=$(nebius compute filesystem get-by-name --name "$NB_FS_NAME" --parent-id "$PROJECT_ID" --format json 2>/dev/null | jq -r '.metadata.id // .id // empty' || true)
    if [[ -n "$existing_id" && "$existing_id" != "null" ]]; then
      NB_FS_ID="$existing_id"; log "Reusing filesystem $NB_FS_NAME -> $NB_FS_ID"; return 0
    fi
    if [[ -z "$size" ]]; then error "NB_FS_SIZE_GIB is required when creating a filesystem"; exit 2; fi
    if ! [[ "$size" =~ ^[0-9]+$ ]]; then error "NB_FS_SIZE_GIB must be an integer GiB (got '$size')"; exit 2; fi
    log "Creating filesystem $NB_FS_NAME sizeGiB=$size type=$type blockSize=$block"
    local create_json new_id
    create_json=$(nebius compute filesystem create --parent-id "$PROJECT_ID" --name "$NB_FS_NAME" --size-gibibytes "$size" --type "$type" --block-size-bytes "$block" --format json 2>/dev/null) || { error "filesystem create failed"; exit 1; }
    new_id=$(printf '%s' "$create_json" | jq -r '.metadata.id // .id // empty')
    if [[ -z "$new_id" ]]; then error "filesystem create returned empty id"; exit 1; fi
    NB_FS_ID="$new_id"; log "Created filesystem $NB_FS_NAME -> $NB_FS_ID"; return 0
  fi

  if [[ "${ATTACH_FS:-false}" == "true" ]]; then
    if [[ -z "${NB_FS_ID:-}" ]]; then error "--attach-fs requires a filesystem id"; exit 2; fi
    if ! have_cmd nebius; then warn "Nebius CLI not available; skipping filesystem attach validation."; return 0; fi
    if nebius compute filesystem get --id "$NB_FS_ID" --parent-id "$PROJECT_ID" >/dev/null 2>&1; then
      log "Using existing filesystem $NB_FS_ID"; return 0
    fi
    error "Filesystem id '$NB_FS_ID' not found under project '$PROJECT_ID'"; exit 2
  fi

  # No explicit flags: best-effort env-based behavior
  if [[ -n "${NB_FS_ID:-}" ]]; then
    if have_cmd nebius && nebius compute filesystem get --id "$NB_FS_ID" --parent-id "$PROJECT_ID" >/dev/null 2>&1; then
      log "Using existing filesystem $NB_FS_ID"; return 0
    fi
    warn "NB_FS_ID present in environment but not found; skipping filesystem operations."; return 0
  fi
  if [[ -n "${NB_FS_NAME:-}" ]]; then
    if ! have_cmd nebius; then warn "Nebius CLI not available; skipping filesystem ensure."; return 0; fi
    local existing_id size type block
    size="${NB_FS_SIZE_GIB:-}"; type="${NB_FS_TYPE:-network_ssd}"; block="${NB_FS_BLOCK_SIZE:-4096}"
    # Precise lookup by name (lighter than listing all filesystems)
    existing_id=$(nebius compute filesystem get-by-name --name "$NB_FS_NAME" --parent-id "$PROJECT_ID" --format json 2>/dev/null | jq -r '.metadata.id // .id // empty' || true)
    if [[ -n "$existing_id" && "$existing_id" != "null" ]]; then
      NB_FS_ID="$existing_id"; log "Reusing filesystem $NB_FS_NAME -> $NB_FS_ID"; return 0
    fi
    if [[ -z "$size" ]]; then warn "NB_FS_NAME present but NB_FS_SIZE_GIB missing; skipping filesystem creation"; return 0; fi
    if ! [[ "$size" =~ ^[0-9]+$ ]]; then error "NB_FS_SIZE_GIB must be an integer GiB (got '$size')"; exit 2; fi
    log "Creating filesystem $NB_FS_NAME sizeGiB=$size type=$type blockSize=$block"
    local create_json new_id
    create_json=$(nebius compute filesystem create --parent-id "$PROJECT_ID" --name "$NB_FS_NAME" --size-gibibytes "$size" --type "$type" --block-size-bytes "$block" --format json 2>/dev/null) || { error "filesystem create failed"; exit 1; }
    new_id=$(printf '%s' "$create_json" | jq -r '.metadata.id // .id // empty')
    if [[ -z "$new_id" ]]; then error "filesystem create returned empty id"; exit 1; fi
    NB_FS_ID="$new_id"; log "Created filesystem $NB_FS_NAME -> $NB_FS_ID"; return 0
  fi

  warn "No filesystem operation requested; skipping filesystem steps."; return 0
}

# attach_filesystem_to_nodegroups: patch every node group to mount the filesystem via virtiofs and cloud-init
attach_filesystem_to_nodegroups(){
  # Attaches filesystem to ALL node groups by updating their template filesystems array.
  # Uses --parent-id + --name update form. Requires NB_FS_ID, MOUNT_TAG, and cluster id.
  have_cmd nebius || { warn "Nebius CLI not found; skipping node-group filesystem attachment."; return 0; }
  [[ -n "${ONLY_INSTALL_OPERATORS:-}" && "${ONLY_INSTALL_OPERATORS}" == "true" ]] && { log "ONLY_INSTALL_OPERATORS=true; skip node-group FS attach"; return 0; }
  [[ -z "${NB_FS_ID:-}" ]] && { warn "NB_FS_ID not set; skipping node-group filesystem attachment"; return 0; }
  local cname cid ng_json names
  cname=$(get_current_cluster_name || true)
  cid=$(get_cluster_id_from_nebius "$cname" || true)
  if [[ -z "$cid" ]]; then warn "Cluster id unresolved; cannot enumerate node groups"; return 0; fi
  ng_json=$(nebius mk8s node-group list --parent-id "$cid" --format json 2>/dev/null || true)
  names=$(printf '%s' "$ng_json" | jq -r '.items[]?.name' | grep -v '^null$' || true)
  [[ -z "$names" ]] && { warn "No node groups found for cluster id $cid"; return 0; }
  log "Attaching filesystem $NB_FS_ID to node groups: $(printf '%s' "$names" | paste -sd ',')"
  local mount_tag fs_mode name USER_DATA
  mount_tag="${MOUNT_TAG:-csi-storage}"; fs_mode="READ_WRITE"

  # Build cloud-init user-data as a JSON string using jq -Rs (per docs)
  USER_DATA=$(jq -Rs '.' <<EOF
runcmd:
  - sudo mkdir -p ${MOUNT_POINT}
  - sudo mount -t virtiofs ${mount_tag} ${MOUNT_POINT}
  - printf "%s %s virtiofs defaults,nofail 0 2\n" "${mount_tag}" "${MOUNT_POINT}" | sudo tee -a /etc/fstab
EOF
)
  # Build the node-group template once; it's identical for all groups
  NB_K8S_NODE_TEMPLATE=$(cat <<EOF
{
  "spec": {
    "template": {
      "filesystems": [
        {
          "attach_mode": "${fs_mode}",
          "mount_tag": "${mount_tag}",
          "existing_filesystem": { "id": "${NB_FS_ID}" }
        }
      ],
      "cloud_init_user_data": ${USER_DATA}
    }
  }
}
EOF
)
  for name in $names; do
    # Guard: skip update if node group already has the desired filesystem attached with matching mount_tag and mode
    local current_json has_fs
    current_json=$(nebius mk8s node-group get-by-name --parent-id "$cid" --name "$name" --format json 2>/dev/null || true)
    has_fs=$(printf '%s' "$current_json" | jq -r --arg id "$NB_FS_ID" --arg tag "$mount_tag" '
      (.spec.template.filesystems // [])
      | map(
          ((.existing_filesystem.id // .existing_filesystem.metadata.id // "") == $id)
          and (.mount_tag == $tag)
          and ((.attach_mode // "") == "READ_WRITE")
        )
      | any
    ' 2>/dev/null || echo false)
    if [[ "$has_fs" == "true" ]]; then
      log "Node-group '$name' already has filesystem $NB_FS_ID mounted with tag '$mount_tag'; skipping update."
      continue
    fi
    set +e
    nebius mk8s node-group update \
      --parent-id "$cid" \
      --name "$name" \
      --patch=true \
      <(printf '%s' "$NB_K8S_NODE_TEMPLATE")
    local rc=$?
    set -e
    if [[ $rc -ne 0 ]]; then
      warn "Failed attaching filesystem to node-group '$name' (rc=$rc). Continuing."; continue
    fi
    log "Updated node-group '$name' with filesystem attachment (mount_tag=${mount_tag})."
  done
}

# ------------------ Operators and CSI ------------------
# label_gpu_nodes: add operand deployment label to GPU-present nodes (idempotent)
label_gpu_nodes(){
  log "Labeling GPU-present nodes with nvidia.com/gpu.deploy.operands=true (idempotent)"
  local nodes; nodes=$(kubectl get nodes -l 'nvidia.com/gpu.present=true' -o name 2>/dev/null || true)
  [[ -z "$nodes" ]] && { warn "No GPU nodes found"; return 0; }
  local n; for n in $nodes; do kubectl label "$n" nvidia.com/gpu.deploy.operands=true --overwrite || true; done
}

# ensure_nvidia_repo: add/update NVIDIA Helm repo silently
ensure_nvidia_repo(){
  helm repo add nvidia https://helm.ngc.nvidia.com/nvidia --force-update >/dev/null 2>&1 || true
  helm repo update >/dev/null 2>&1 || true
}

# install_gpu_operator: install/upgrade NVIDIA GPU Operator with selected components
install_gpu_operator(){
  helm upgrade --install gpu-operator nvidia/gpu-operator -n nvidia-gpu-operator --create-namespace --wait \
    --set driver.enabled=${GPU_OPERATOR_DRIVER_ENABLED:-true} \
    --set migManager.enabled=${GPU_OPERATOR_MIG_MANAGER_ENABLED:-false} \
    --set devicePlugin.enabled=${GPU_OPERATOR_DEVICE_PLUGIN_ENABLED:-true} \
    --set dcgmExporter.enabled=${GPU_OPERATOR_DCGM_EXPORTER_ENABLED:-true} \
    --set toolkit.enabled=${GPU_OPERATOR_TOOLKIT_ENABLED:-true}
}

# install_network_operator: install/upgrade NVIDIA Network Operator
install_network_operator(){
  helm upgrade --install network-operator nvidia/network-operator -n nvidia-network-operator --create-namespace --wait
}

# install_csi_driver: pull and deploy Nebius CSI mounted-fs-path driver (optionally waiting)
install_csi_driver(){
  local tmp; tmp=$(mktemp_dir)
  local chart_ref="oci://cr.eu-north1.nebius.cloud/mk8s/helm/csi-mounted-fs-path" chart_ver="0.1.3"
  helm pull "$chart_ref" --version "$chart_ver" --destination "$tmp" || { warn "helm pull failed"; return 0; }
  local pkg; pkg=$(ls "$tmp"/*.tgz | head -n1)
  if [[ "${CSI_HELM_WAIT:-true}" == "true" ]]; then
    helm upgrade --install csi-mounted-fs-path "$pkg" \
      --set "dataDir=${MOUNT_POINT%/}/csi-mounted-fs-path-data/" \
      --wait --timeout "${CSI_TIMEOUT:-10m}" || warn "CSI driver install reported non-zero"
  else
    helm upgrade --install csi-mounted-fs-path "$pkg" \
      --set "dataDir=${MOUNT_POINT%/}/csi-mounted-fs-path-data/" || warn "CSI driver install reported non-zero"
    log "CSI driver install triggered without --wait (CSI_HELM_WAIT=false)."
  fi
  rm -rf "$tmp"
}

# verify_mount_once: run ephemeral BusyBox pod to assert host mount accessibility
verify_mount_once(){
  [[ "${VERIFY_MOUNT:-false}" == "true" ]] || { log "Skipping mount verification"; return 0; }
  log "Verifying mount at ${MOUNT_POINT} via a one-off BusyBox pod"
  local pod=mount-probe ns=default
  cat <<EOF | kubectl apply -f - >/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: ${pod}
  namespace: ${ns}
spec:
  restartPolicy: Never
  containers:
    - name: probe
      image: busybox:latest
      command: ["sh","-lc","mkdir -p /host${MOUNT_POINT}; if awk '$1==\"${MOUNT_TAG}\" && $2==\"${MOUNT_POINT}\" && $3==\"virtiofs\" {found=1} END{exit !found}' /hostproc/mounts; then touch /host${MOUNT_POINT}/.probe.$$; echo MOUNT_OK; else echo MOUNT_MISSING; fi"]
      volumeMounts:
        - name: host-mount
          mountPath: /host${MOUNT_POINT}
        - name: host-proc
          mountPath: /hostproc
          readOnly: true
  volumes:
    - name: host-mount
      hostPath:
        path: ${MOUNT_POINT}
        type: DirectoryOrCreate
    - name: host-proc
      hostPath:
        path: /proc
        type: Directory
EOF
  # Give it a few seconds, then read logs and clean up
  sleep 3
  kubectl logs -n "$ns" "$pod" --tail=20 2>/dev/null || true
  kubectl delete pod -n "$ns" "$pod" --ignore-not-found >/dev/null 2>&1 || true
}

# ------------------ Main ------------------
# main: orchestrate bootstrap steps according to flags
main(){
  generate_env_if_missing
  load_env
  parse_args "$@"
  preflight

  if [[ "${ONLY_INSTALL_OPERATORS:-false}" == "true" ]]; then
    label_gpu_nodes
    ensure_nvidia_repo
    install_gpu_operator
    install_network_operator
    log "Operators installed; exiting (ONLY_INSTALL_OPERATORS=true)"; return 0
  fi

  # Verification-only path: do not change cluster/node-group config
  if [[ "${ATTACHED_FS:-false}" == "true" ]]; then
    if [[ "${VERIFY_MOUNT:-false}" != "true" ]]; then
      warn "--attached-fs provided without --verify-mount; nothing to do."; return 0
    fi
    verify_mount_once
    log "Verification completed (attached-fs)."; return 0
  fi

  ensure_filesystem
  attach_filesystem_to_nodegroups
  label_gpu_nodes
  ensure_nvidia_repo
  install_gpu_operator
  install_network_operator
  install_csi_driver
  verify_mount_once
  log "Bootstrap completed"
}

main "$@"

