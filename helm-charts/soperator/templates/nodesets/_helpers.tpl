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

{{/* Chart-owned host driver root mount for GPU workers using Nebius-image host drivers. */}}
{{- define "nodesets.gpuDriverJailMount" -}}
- name: "nvidia-driver-root"
  mountPath: "/run/nvidia/driver"
  readOnly: false
  volumeSource:
    hostPath:
      path: "/"
      type: ""
{{- end -}}

{{/* Init guard that makes the shared jail consume real host NVIDIA driver libraries. */}}
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

      jail="/mnt/jail"
      driver_root="/run/nvidia/driver"
      arch="$(uname -m)"
      jail_lib_dir="${jail}/usr/lib/${arch}-linux-gnu"
      host_lib_dir="${driver_root}/usr/lib/${arch}-linux-gnu"
      prep_lock="${jail}/etc/cxcli_gpu_driver_jail_prep.lock"
      prep_marker="${jail}/etc/cxcli_gpu_driver_jail_prep"

      [ -d "${jail}/usr" ] || fail "shared jail is not populated at ${jail}"
      [ -d "${host_lib_dir}" ] || fail "host NVIDIA library directory is missing: ${host_lib_dir}"
      [ -x "${driver_root}/usr/bin/nvidia-smi" ] || fail "host nvidia-smi is missing under ${driver_root}"

      mkdir -p "${jail}/etc" "${jail_lib_dir}"
      exec 9>"${prep_lock}"
      flock 9

      rm -f "${jail}/etc/gpu_libs_installed.flag"
      find "${jail_lib_dir}" -maxdepth 1 -type f \
        \( -name 'libcuda.so*' -o -name 'libnvidia-ml.so*' \) \
        -size 0 -print -delete

      copy_driver_lib() {
        local soname="$1"
        local host_link="${host_lib_dir}/${soname}"
        local source="${host_link}"
        [ -e "${host_link}" ] || fail "host driver library ${soname} is missing in ${host_lib_dir}"
        if [ -L "${host_link}" ]; then
          local target
          target="$(readlink "${host_link}")"
          case "${target}" in
            /*) source="${driver_root}${target}" ;;
            *) source="$(dirname "${host_link}")/${target}" ;;
          esac
        fi
        [ -s "${source}" ] || fail "host driver library target for ${soname} is missing or empty: ${source}"
        local target_name
        target_name="$(basename "${source}")"
        cp -f "${source}" "${jail_lib_dir}/${target_name}"
        chmod 0644 "${jail_lib_dir}/${target_name}"
        ln -sfn "${target_name}" "${jail_lib_dir}/${soname}"
      }

      copy_driver_lib libcuda.so.1
      copy_driver_lib libnvidia-ml.so.1
      ln -sfn libcuda.so.1 "${jail_lib_dir}/libcuda.so"
      ln -sfn libnvidia-ml.so.1 "${jail_lib_dir}/libnvidia-ml.so"

      [ -x "${jail}/usr/sbin/ldconfig" ] || fail "jail ldconfig is missing"
      chroot "${jail}" /usr/sbin/ldconfig
      linker_cache="$(chroot "${jail}" /usr/sbin/ldconfig -p)"
      case "${linker_cache}" in
        *libcuda.so.1*) ;;
        *) fail "jail linker cache is missing libcuda.so.1" ;;
      esac
      case "${linker_cache}" in
        *libnvidia-ml.so.1*) ;;
        *) fail "jail linker cache is missing libnvidia-ml.so.1" ;;
      esac

      {
        printf 'prepared_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'host_driver_root=%s\n' "${driver_root}"
      } > "${prep_marker}"
      log "prepared NVIDIA driver libraries in the shared jail"
  volumeMounts:
    - name: "jail"
      mountPath: "/mnt/jail"
    - name: "nvidia-driver-root"
      mountPath: "/run/nvidia/driver"
{{- end -}}
