{{/* Expand the chart name. */}}
{{- define "son.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Create a fully qualified app name. */}}
{{- define "son.fullname" -}}
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

{{/* Chart label. */}}
{{- define "son.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Common labels. */}}
{{- define "son.labels" -}}
helm.sh/chart: {{ include "son.chart" . }}
{{ include "son.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* Selector labels. */}}
{{- define "son.selectorLabels" -}}
app.kubernetes.io/name: {{ include "son.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Validate chart-owned secret contract. */}}
{{- define "son.validate" -}}
{{- if hasKey .Values.slack "webhookUrl" }}
{{- fail "slack.webhookUrl is not supported. Put the Slack incoming webhook URL in the Kubernetes Secret referenced by slack.existingSecret/slack.existingSecretKey." }}
{{- end }}
{{- if not (has .Values.slack.mode (list "existing-webhook" "oauth-webhook")) }}
{{- fail "slack.mode must be one of: existing-webhook, oauth-webhook" }}
{{- end }}
{{- $_ := required "slack.existingSecret is required." .Values.slack.existingSecret -}}
{{- $_ := required "slack.existingSecretKey is required." .Values.slack.existingSecretKey -}}
{{- end }}

{{/* Slack webhook secret reference. */}}
{{- define "son.slack.webhook.secret.name" -}}
{{- required "slack.existingSecret is required." .Values.slack.existingSecret }}
{{- end }}

{{- define "son.slack.webhook.secret.key" -}}
{{- required "slack.existingSecretKey is required." .Values.slack.existingSecretKey }}
{{- end }}

{{/* AlertManager Go template wrappers. */}}
{{- define "son.wrapTemplate" -}}
{{ "{{ " }}{{ . }}{{ " }}"}}
{{- end }}

{{- define "son.wrapTemplateTrimL" -}}
{{ "{{- " }}{{ . }}{{ " }}"}}
{{- end }}

{{- define "son.wrapTemplateTrimLR" -}}
{{ "{{- " }}{{ . }}{{ " -}}"}}
{{- end }}

{{/* Shared names. */}}
{{- define "son.config.name" -}}
{{- include "son.fullname" . }}
{{- end }}

{{- define "son.alertManager.name" -}}
{{- include "son.fullname" . }}
{{- end }}

{{- define "son.rule.name" -}}
{{- printf "%s-%s" (include "son.fullname" .) "slurm-job" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "son.rule.groupName" -}}
slurm-job
{{- end }}

{{/* Alertmanager route settings. */}}
{{- define "son.config.route.groupWait" -}}
{{ .Values.interval.groupWait | default "30s" }}
{{- end }}

{{- define "son.config.route.groupInterval" -}}
{{ .Values.interval.group | default "5m" }}
{{- end }}

{{- define "son.config.route.repeatInterval" -}}
{{ .Values.interval.repeat | default "25h" }}
{{- end }}

{{- define "son.alertManager.replicas" -}}
{{- .Values.alertManager.replicas | default 1 }}
{{- end }}

{{- define "son.alertManager.port" -}}
{{- .Values.alertManager.port | default 9093 }}
{{- end }}

{{- define "son.alertManager.url" -}}
{{ printf "http://vmalertmanager-%s:%s" (include "son.alertManager.name" .) (include "son.alertManager.port" .) }}
{{- end }}

{{- define "son.alert.dataSourceUrl" -}}
{{ .Values.dataSourceUrl | default "http://vmsingle-metrics-victoria-metrics-k8s-stack:8429" }}
{{- end }}

{{- define "son.alert.evaluationInterval" -}}
{{ .Values.interval.evaluation | default "30s" }}
{{- end }}

{{/* Label helpers. */}}
{{- define "son.config.label.match" -}}
{{ printf "%s-%s" (include "son.fullname" .) "slack-route" | trunc 63 | trimSuffix "-" }}: "enabled"
{{- end }}

{{- define "son.rule.groupMatchLabelKey" -}}
{{ printf "%s-%s" (include "son.fullname" .) "vmrule-group" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "son.rule.groupMatchLabel" -}}
{{ include "son.rule.groupMatchLabelKey" . }}: slack
{{- end }}

{{- define "son.config.label.severity" -}}severity{{- end }}
{{- define "son.config.label.job.id" -}}job_id{{- end }}
{{- define "son.config.label.job.name" -}}job_name{{- end }}
{{- define "son.config.label.job.state" -}}job_state{{- end }}
{{- define "son.config.label.job.stateReason" -}}job_state_reason{{- end }}
{{- define "son.config.label.job.user" -}}job_user{{- end }}
{{- define "son.config.label.job.user_mail" -}}job_user_mail{{- end }}
{{- define "son.config.label.alertKey" -}}alert_key{{- end }}

{{/* Slurm job states. */}}
{{- define "son.config.jobStatus.failed" -}}FAILED{{- end }}
{{- define "son.config.jobStatus.nodeFail" -}}NODE_FAIL{{- end }}
{{- define "son.config.jobStatus.oom" -}}OUT_OF_MEMORY{{- end }}
{{- define "son.config.jobStatus.bootFail" -}}BOOT_FAIL{{- end }}
{{- define "son.config.jobStatus.cancelled" -}}CANCELLED{{- end }}
{{- define "son.config.jobStatus.deadline" -}}DEADLINE{{- end }}
{{- define "son.config.jobStatus.preempted" -}}PREEMPTED{{- end }}
{{- define "son.config.jobStatus.suspended" -}}SUSPENDED{{- end }}
{{- define "son.config.jobStatus.timeout" -}}TIMEOUT{{- end }}
{{- define "son.config.jobStatus.completed" -}}COMPLETED{{- end }}

{{/* Rule labels. */}}
{{- define "son.rule.labels" -}}
{{ include "son.config.label.severity" . }}: {{ .severity | quote }}
namespace: {{ .context.Release.Namespace | quote }}
{{ include "son.config.label.job.id" . }}: {{ include "son.wrapTemplate" "$labels.job_id" | quote }}
{{ include "son.config.label.job.name" . }}: {{ include "son.wrapTemplate" "$labels.job_name" | quote }}
{{ include "son.config.label.job.state" . }}: {{ include "son.wrapTemplate" "$labels.job_state" | quote }}
{{ include "son.config.label.job.stateReason" . }}: {{ include "son.wrapTemplate" "$labels.job_state_reason" | quote }}
{{ include "son.config.label.job.user" . }}: {{ include "son.wrapTemplate" "$labels.user_name" | quote }}
{{ include "son.config.label.job.user_mail" . }}: {{ include "son.wrapTemplate" "$labels.user_mail" | quote }}
{{ include "son.config.label.alertKey" . }}: {{ printf "job_%s_%s" (include "son.wrapTemplate" "$labels.job_id") (include "son.wrapTemplate" "$labels.job_state") | quote }}
{{- end }}

{{/* MetricsQL selectors. */}}
{{- define "son.rule.jobSelector.error" -}}
job_state=~"{{ include "son.config.jobStatus.failed" . }}|{{ include "son.config.jobStatus.nodeFail" . }}|{{ include "son.config.jobStatus.oom" . }}"
{{- end }}

{{- define "son.rule.jobSelector.warning" -}}
job_state=~"{{ include "son.config.jobStatus.bootFail" . }}|{{ include "son.config.jobStatus.cancelled" . }}|{{ include "son.config.jobStatus.deadline" . }}|{{ include "son.config.jobStatus.preempted" . }}|{{ include "son.config.jobStatus.suspended" . }}|{{ include "son.config.jobStatus.timeout" . }}"
{{- end }}

{{- define "son.rule.jobSelector.good" -}}
job_state=~"{{ include "son.config.jobStatus.completed" . }}"
{{- end }}

{{- define "son.rule.jobSelector.system" -}}
user_name!~"^(nebius|soperatorchecks)$"
{{- end }}

{{- define "son.rule.jobSelector.requireUserMail" -}}
user_mail!=""
{{- end }}

{{/* Slack message colors. */}}
{{- define "son.slack.severity.error" -}}error{{- end }}
{{- define "son.slack.severity.warning" -}}warning{{- end }}
{{- define "son.slack.severity.good" -}}good{{- end }}
{{- define "son.slack.msg.color.error" -}}{{ .Values.slack.severityColor.error | default "danger" }}{{- end }}
{{- define "son.slack.msg.color.warning" -}}{{ .Values.slack.severityColor.warning | default "#F28B30" }}{{- end }}
{{- define "son.slack.msg.color.good" -}}{{ .Values.slack.severityColor.good | default "good" }}{{- end }}
{{- define "son.slack.msg.color.unknown" -}}{{ .Values.slack.severityColor.unknown | default "#807F83" }}{{- end }}
