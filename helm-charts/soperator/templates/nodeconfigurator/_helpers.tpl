{{/*
Expand the name of the chart.
*/}}
{{- define "nodeconfigurator.name" -}}
{{- include "slurm-cluster.name" . }}
{{- end }}

{{/*
Create a default fully qualified app name.
NodeConfigurator cluster-scoped RBAC follows the cluster name so two releases in
different namespaces do not fight over the same ClusterRole names.
*/}}
{{- define "nodeconfigurator.fullname" -}}
{{- include "nodeconfigurator.name" . }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "nodeconfigurator.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "nodeconfigurator.labels" -}}
helm.sh/chart: {{ include "nodeconfigurator.chart" . }}
{{ include "nodeconfigurator.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "nodeconfigurator.selectorLabels" -}}
app.kubernetes.io/name: {{ include "nodeconfigurator.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
