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
