{{/*
Expand the name of the chart.
*/}}
{{- define "soperator-backups.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "soperator-backups.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "soperator-backups.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "soperator-backups.labels" -}}
helm.sh/chart: {{ include "soperator-backups.chart" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Validate required external backup inputs. Credentials are intentionally provided
by an existing runtime Secret and are never accepted as Helm values.
*/}}
{{- define "soperator-backups.validate" -}}
{{- if not .Values.bucket.name -}}
{{- fail "bucket.name is required for Soperator jail backup configuration." -}}
{{- end -}}
{{- if not .Values.bucket.endpoint -}}
{{- fail "bucket.endpoint is required for Soperator jail backup configuration." -}}
{{- end -}}
{{- if not .Values.secret.name -}}
{{- fail "secret.name is required for Soperator jail backup configuration." -}}
{{- end -}}
{{- if not .Values.secret.keys.accessKeyID -}}
{{- fail "secret.keys.accessKeyID is required for Soperator jail backup configuration." -}}
{{- end -}}
{{- if not .Values.secret.keys.secretAccessKey -}}
{{- fail "secret.keys.secretAccessKey is required for Soperator jail backup configuration." -}}
{{- end -}}
{{- if not .Values.secret.keys.backupPassword -}}
{{- fail "secret.keys.backupPassword is required for Soperator jail backup configuration." -}}
{{- end -}}
{{- end -}}
