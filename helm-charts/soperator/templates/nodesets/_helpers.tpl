{{/*
Expand the name of the chart.
*/}}
{{- define "nodesets.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "nodesets.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "nodesets.labels" -}}
helm.sh/chart: {{ include "nodesets.chart" . }}
{{ include "nodesets.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "nodesets.selectorLabels" -}}
app.kubernetes.io/name: {{ include "nodesets.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Construct container gpu resource from GPU spec ([0]) and GPU resource spec ([1]) */}}
{{- define "nodesets.resource.gpuFrom" -}}
  {{- $gpuSpec := (index . 0) | default dict -}}
  {{- $gpuEnabled := get $gpuSpec "enabled" | default false -}}
  {{- if not $gpuEnabled -}}
    {{- "" -}}
  {{- else -}}
    {{- $gpuVendorNvidia := hasKey $gpuSpec "nvidia" -}}
    {{- $gpuVendor := $gpuVendorNvidia | ternary "nvidia.com" "" -}}
    {{- $gpuResFQID := $gpuVendor | empty | ternary "gpu" (printf "%s/gpu" $gpuVendor)}}
    {{- $resources := required ".Values.nodesets[*].slurmd.resources.gpu is required as .Values.nodesets[*].gpu.enabled is true" (index . 1)}}
{{ $gpuResFQID }}: {{ get $resources "gpu" }}
  {{- end -}}
{{- end -}}

{{/* Convert a flat image string into the repository/tag object expected by the NodeSet CRD. */}}
{{- define "nodesets.imageFromFlatString" -}}
{{- $image := required (index . 1) (index . 0) -}}
{{- $repository := $image -}}
{{- $tag := "" -}}
{{- if and (not (contains "@" $image)) (regexMatch "^.+:[^/:]+$" $image) -}}
{{- $repository = regexReplaceAll "^(.+):([^/:]+)$" $image "${1}" -}}
{{- $tag = regexReplaceAll "^(.+):([^/:]+)$" $image "${2}" -}}
{{- end -}}
repository: {{ $repository | quote }}
{{- with $tag }}
tag: {{ . | quote }}
{{- end }}
pullPolicy: "IfNotPresent"
{{- end -}}

{{/* Build the slurmd image string for custom init containers. */}}
{{- define "nodesets.slurmdImageString" -}}
{{- $root := index . 0 -}}
{{- $slurmd := index . 1 -}}
{{- if $slurmd.image -}}
{{- $image := $slurmd.image -}}
{{- $repository := required ".Values.nodesets[*].slurmd.image.repository is required as the custom image is used." $image.repository -}}
{{- if $image.tag -}}
{{- printf "%s:%s" $repository $image.tag -}}
{{- else -}}
{{- $repository -}}
{{- end -}}
{{- else -}}
{{- required ".Values.images.slurmd is required as the default NodeSet slurmd image." $root.Values.images.slurmd -}}
{{- end -}}
{{- end -}}

{{/* Build the slurmd pull policy for custom init containers. */}}
{{- define "nodesets.slurmdImagePullPolicy" -}}
{{- $slurmd := index . 0 -}}
{{- if $slurmd.image -}}
{{- default "IfNotPresent" $slurmd.image.pullPolicy -}}
{{- else -}}
IfNotPresent
{{- end -}}
{{- end -}}

{{/* Standard slurm-scripts mounts for NodeSet workers. */}}
{{- define "nodesets.slurmScriptMount" -}}
{{- $root := index . 0 -}}
{{- $name := index . 1 -}}
- name: {{ $name | quote }}
  mountPath: {{ ternary "/opt/slurm_scripts/" "/mnt/jail.upper/opt/slurm_scripts/" (eq $name "slurm-scripts") | quote }}
  volumeSource:
    configMap:
      name: {{ include "slurm-cluster.slurmScriptsConfigMapName" $root | quote }}
      defaultMode: 493
{{- end -}}

{{/* Generated Slurm config mount for worker jails. */}}
{{- define "nodesets.slurmConfigJailMount" -}}
{{- $root := index . 0 -}}
- name: "slurm-configs-jail"
  mountPath: "/mnt/jail/etc/slurm"
  readOnly: true
  volumeSource:
    configMap:
      name: {{ include "slurm-cluster.slurmConfigsConfigMapName" $root | quote }}
{{- end -}}

{{/* Init guard that seeds generated Slurm configs into worker jails before worker-init. */}}
{{- define "nodesets.slurmConfigJailInitContainer" -}}
{{- $root := index . 0 -}}
{{- $ns := index . 1 -}}
{{- $slurmd := required ".Values.nodesets[*].slurmd is required." $ns.slurmd -}}
- name: "cxcli-slurm-config-jail"
  image: {{ include "nodesets.slurmdImageString" (list $root $slurmd) | quote }}
  imagePullPolicy: {{ include "nodesets.slurmdImagePullPolicy" (list $slurmd) | quote }}
  command:
    - /bin/bash
    - -c
  args:
    - |-
      set -euo pipefail

      log() { printf '[cxcli-slurm-config-jail] %s\n' "$*"; }
      fail() { log "ERROR: $*"; exit 1; }

      jail="/mnt/jail"
      source_config="/mnt/slurm-configs"
      jail_config="${jail}/etc/slurm"

      [ -d "${jail}/usr" ] || fail "shared jail is not populated at ${jail}"
      [ -s "${source_config}/slurm.conf" ] || fail "generated slurm.conf is missing in ${source_config}"

      mkdir -p "${jail_config}"
      shopt -s nullglob
      copied=0
      for path in "${source_config}"/*; do
        [ -f "${path}" ] || continue
        cp -f "${path}" "${jail_config}/$(basename "${path}")"
        copied=$((copied + 1))
      done
      [ "${copied}" -gt 0 ] || fail "generated Slurm config map is empty"
      chmod 0644 "${jail_config}"/*
      log "seeded ${copied} generated Slurm config file(s) into the shared jail"
  volumeMounts:
    - name: "jail"
      mountPath: "/mnt/jail"
    - name: "slurm-configs-jail"
      mountPath: "/mnt/slurm-configs"
      readOnly: true
{{- end -}}

{{/* Chart-owned host driver root mount for GPU workers using Nebius-image host drivers. */}}
{{- define "nodesets.gpuDriverJailMount" -}}
- name: "nvidia-driver-root"
  mountPath: "/run/nvidia/driver"
  readOnly: true
  volumeSource:
    hostPath:
      path: "/"
      type: ""
{{- end -}}

{{/* Read-only init guard for the cxcli-populated NVIDIA driver contract. */}}
{{- define "nodesets.gpuDriverJailInitContainer" -}}
{{- $root := index . 0 -}}
{{- $ns := index . 1 -}}
{{- $slurmd := required ".Values.nodesets[*].slurmd is required." $ns.slurmd -}}
- name: "cxcli-gpu-driver-jail"
  image: {{ include "nodesets.slurmdImageString" (list $root $slurmd) | quote }}
  imagePullPolicy: {{ include "nodesets.slurmdImagePullPolicy" (list $slurmd) | quote }}
  command:
    - /bin/bash
    - -c
  args:
    - |-
      set -euo pipefail

      log() { printf '[cxcli-gpu-driver-jail] %s\n' "$*"; }
      fail() { log "ERROR: $*"; exit 1; }
      require_command() {
        command -v "$1" >/dev/null 2>&1 || fail "required command is missing: $1"
      }

      for command_name in awk cat chroot env grep readlink sed sha256sum sort uname wc; do
        require_command "${command_name}"
      done

      jail="/mnt/jail"
      driver_root="/run/nvidia/driver"
      arch="$(uname -m)"
      jail_lib_dir="${jail}/usr/lib/${arch}-linux-gnu"
      host_lib_dir="${driver_root}/usr/lib/${arch}-linux-gnu"
      marker="${jail}/etc/nebius-cxcli/gpu-driver-jail.env"
      marker_schema="nebius-cxcli-soperator-jail-gpu-driver/v1"

      [ -d "${jail}/usr" ] || fail "shared jail is not populated at ${jail}"
      [ -d "${host_lib_dir}" ] || fail "host NVIDIA library directory is missing: ${host_lib_dir}"
      [ -x "${driver_root}/usr/bin/nvidia-smi" ] || fail "host nvidia-smi is missing under ${driver_root}"
      [ -d "${jail_lib_dir}" ] || fail "shared-jail NVIDIA library directory is missing: ${jail_lib_dir}"
      [ -f "${marker}" ] && [ ! -L "${marker}" ] \
        || fail "cxcli GPU driver evidence marker is missing or is not a regular file"
      [ "$(wc -l <"${marker}")" -eq 8 ] \
        || fail "cxcli GPU driver evidence marker has an unexpected field count"

      marker_value() {
        local key="$1"
        awk -F= -v expected="${key}" '$1 == expected { count += 1; sub(/^[^=]*=/, ""); value = $0 }
          END { if (count != 1 || value == "") exit 1; print value }' "${marker}" \
          || fail "cxcli GPU driver evidence marker has an invalid ${key} field"
      }

      observed_schema="$(marker_value schema)"
      observed_arch="$(marker_value arch)"
      driver_version="$(marker_value driver_version)"
      cuda_base="$(marker_value libcuda_source)"
      cuda_hash="$(marker_value libcuda_sha256)"
      nvml_base="$(marker_value libnvidia_ml_source)"
      nvml_hash="$(marker_value libnvidia_ml_sha256)"
      nvidia_smi_hash="$(marker_value nvidia_smi_sha256)"

      [ "${observed_schema}" = "${marker_schema}" ] || fail "unsupported cxcli GPU driver marker schema"
      [ "${observed_arch}" = "${arch}" ] || fail "cxcli GPU driver marker architecture does not match this worker"
      case "${driver_version}" in
        ''|*[!0-9.]*|.*|*..*|*.) fail "invalid marked NVIDIA driver version: ${driver_version}" ;;
      esac
      [ "${cuda_base}" = "libcuda.so.${driver_version}" ] \
        || fail "marked libcuda source does not match NVIDIA driver ${driver_version}"
      [ "${nvml_base}" = "libnvidia-ml.so.${driver_version}" ] \
        || fail "marked libnvidia-ml source does not match NVIDIA driver ${driver_version}"
      for digest in "${cuda_hash}" "${nvml_hash}" "${nvidia_smi_hash}"; do
        case "${digest}" in ''|*[!0-9a-f]*) fail "cxcli GPU driver marker contains a non-SHA-256 digest" ;; esac
        [ "${#digest}" -eq 64 ] || fail "cxcli GPU driver marker contains a non-SHA-256 digest"
      done

      resolve_host_library() {
        local soname="$1"
        local expected_base="$2"
        local expected_hash="$3"
        local source actual_hash
        source="$(readlink -f -- "${host_lib_dir}/${soname}")" \
          || fail "host driver library is missing or broken: ${soname}"
        case "${source}" in
          "${driver_root}"/*) ;;
          *) fail "host driver library resolves outside the read-only host root: ${soname}" ;;
        esac
        [ -f "${source}" ] && [ -s "${source}" ] \
          || fail "host driver library target is missing or empty: ${soname}"
        [ "${source##*/}" = "${expected_base}" ] \
          || fail "host driver library source changed since cxcli populated the jail: ${soname}"
        actual_hash="$(sha256sum -- "${source}" | awk '{print $1}')"
        [ "${actual_hash}" = "${expected_hash}" ] \
          || fail "host driver library hash changed since cxcli populated the jail: ${soname}"
      }

      resolve_host_library libcuda.so.1 "${cuda_base}" "${cuda_hash}"
      resolve_host_library libnvidia-ml.so.1 "${nvml_base}" "${nvml_hash}"
      host_nvidia_smi_source="$(readlink -f -- "${driver_root}/usr/bin/nvidia-smi")" \
        || fail "host nvidia-smi is missing or broken"
      case "${host_nvidia_smi_source}" in
        "${driver_root}"/*) ;;
        *) fail "host nvidia-smi resolves outside the read-only host root" ;;
      esac
      [ "$(sha256sum -- "${host_nvidia_smi_source}" | awk '{print $1}')" = "${nvidia_smi_hash}" ] \
        || fail "host nvidia-smi changed since cxcli populated the jail"

      host_driver_versions="$(env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        chroot "${driver_root}" /usr/bin/nvidia-smi \
        --query-gpu=driver_version --format=csv,noheader 2>/dev/null \
        | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | sed '/^$/d' | sort -u)" \
        || fail "host nvidia-smi could not report GPU driver versions"
      [ "$(printf '%s\n' "${host_driver_versions}" | sed '/^$/d' | wc -l)" -eq 1 ] \
        || fail "host reports mixed or missing NVIDIA driver versions"
      [ "${host_driver_versions}" = "${driver_version}" ] \
        || fail "host NVIDIA driver version changed since cxcli populated the jail"

      resolve_jail_link() {
        local name="$1"
        local expected="$2"
        local expected_hash="$3"
        local link="${jail_lib_dir}/${name}"
        local raw candidate resolved actual_hash
        [ -L "${link}" ] || fail "${name} is not a shared-jail symlink"
        raw="$(readlink -- "${link}")" || fail "${name} is a broken shared-jail symlink"
        [ "${raw}" = "${expected}" ] || fail "${name} has an unexpected shared-jail target"
        case "${raw}" in
          /*) candidate="${jail}${raw}" ;;
          *) candidate="${link%/*}/${raw}" ;;
        esac
        resolved="$(readlink -m -- "${candidate}")" \
          || fail "could not resolve shared-jail symlink: ${name}"
        case "${resolved}" in
          "${jail_lib_dir}"/*) ;;
          *) fail "${name} resolves outside the shared-jail NVIDIA library directory" ;;
        esac
        [ -f "${resolved}" ] && [ -s "${resolved}" ] \
          || fail "${name} resolves to a missing or empty shared-jail library"
        actual_hash="$(sha256sum -- "${resolved}" | awk '{print $1}')"
        [ "${actual_hash}" = "${expected_hash}" ] \
          || fail "${name} shared-jail library hash does not match cxcli evidence"
      }

      resolve_jail_link libcuda.so.1 "${cuda_base}" "${cuda_hash}"
      resolve_jail_link libcuda.so libcuda.so.1 "${cuda_hash}"
      resolve_jail_link libnvidia-ml.so.1 "${nvml_base}" "${nvml_hash}"
      resolve_jail_link libnvidia-ml.so libnvidia-ml.so.1 "${nvml_hash}"

      [ -x "${jail}/usr/sbin/ldconfig" ] || fail "jail ldconfig is missing"
      linker_cache="$(chroot "${jail}" /usr/sbin/ldconfig -p)"
      case "${linker_cache}" in
        *libcuda.so.1*) ;;
        *) fail "jail linker cache is missing libcuda.so.1" ;;
      esac
      case "${linker_cache}" in
        *libnvidia-ml.so.1*) ;;
        *) fail "jail linker cache is missing libnvidia-ml.so.1" ;;
      esac

      jailed_gpu_output="$(env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        chroot "${jail}" /usr/bin/nvidia-smi -L)" \
        || fail "jailed nvidia-smi -L failed"
      [ "$(printf '%s\n' "${jailed_gpu_output}" | grep -c '^GPU ')" -gt 0 ] \
        || fail "jailed nvidia-smi -L reported no GPUs"
      log "validated cxcli NVIDIA driver evidence without modifying the shared jail"
  resources:
    limits:
      nvidia.com/gpu: 1
  securityContext:
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    runAsNonRoot: false
    runAsUser: 0
    capabilities:
      drop:
        - ALL
      add:
        - SYS_CHROOT
  volumeMounts:
    - name: "jail"
      mountPath: "/mnt/jail"
      readOnly: true
    - name: "nvidia-driver-root"
      mountPath: "/run/nvidia/driver"
      readOnly: true
{{- end -}}
