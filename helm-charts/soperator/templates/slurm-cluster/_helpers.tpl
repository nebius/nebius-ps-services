{{/* Name of the cluster */}}
{{- define "slurm-cluster.name" -}}
{{- default .Chart.Name .Values.clusterName | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Create chart name and version as used by the chart label */}}
{{- define "slurm-cluster.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Selector labels */}}
{{- define "slurm-cluster.selectorLabels" -}}
app.kubernetes.io/name: {{ include "slurm-cluster.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Common labels */}}
{{- define "slurm-cluster.labels" -}}
helm.sh/chart: {{ include "slurm-cluster.chart" . }}
{{ include "slurm-cluster.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* Cluster-scoped ConfigMap name for scripts mounted into Slurm pods. */}}
{{- define "slurm-cluster.slurmScriptsConfigMapName" -}}
{{- printf "%s-slurm-scripts" (include "slurm-cluster.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "validateAccountingConfig" -}}
{{- if .Values.slurmNodes.accounting.enabled -}}
  {{- if not (or .Values.slurmNodes.accounting.externalDB.enabled .Values.slurmNodes.accounting.mariadbOperator.enabled) -}}
    {{- fail "If slurmNodes.accounting.enabled is true, either slurmNodes.accounting.externalDB.enabled or slurmNodes.accounting.mariadbOperator.enabled must be true." -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "validatePartitionConfig" -}}
{{- $partitionConfig := .Values.partitionConfiguration | default dict -}}
{{- $configType := default "structured" $partitionConfig.configType -}}
{{- if not (has $configType (list "default" "custom" "structured")) -}}
  {{- fail (printf "partitionConfiguration.configType must be one of default, custom, or structured; got %q." $configType) -}}
{{- end -}}
{{- $rawConfig := get $partitionConfig "rawConfig" | default list -}}
{{- if and $rawConfig (ne $configType "custom") -}}
  {{- fail (printf "partitionConfiguration.rawConfig is set but partitionConfiguration.configType is %q. Use configType: custom or remove rawConfig." $configType) -}}
{{- end -}}
{{- $partitions := get $partitionConfig "partitions" | default list -}}
{{- if and (eq $configType "structured") (hasKey $partitionConfig "partitions") (not $partitions) -}}
  {{- fail "partitionConfiguration.configType is structured but partitionConfiguration.partitions is empty. Provide at least one partition or use configType: default." -}}
{{- end -}}
{{- $defaultPartitions := list
  (dict "name" "main" "isAll" true "config" "Default=YES MaxTime=INFINITE State=UP PriorityTier=10 OverSubscribe=YES")
  (dict "name" "hidden" "isAll" true "config" "Default=NO Hidden=YES MaxTime=INFINITE State=UP PriorityTier=10 PreemptMode=OFF OverSubscribe=YES")
-}}
{{- if and $partitions (ne $configType "structured") (ne (toJson $partitions) (toJson $defaultPartitions)) -}}
  {{- fail (printf "partitionConfiguration.partitions is set but partitionConfiguration.configType is %q. Use configType: structured or remove partitions." $configType) -}}
{{- end -}}
{{- end -}}

{{- define "validateDependencyConfig" -}}
{{- if hasKey .Values "mariadbOperator" -}}
  {{- fail "Top-level mariadbOperator is not supported. Use mariadb-operator.installOperator for the MariaDB Operator subchart and slurmNodes.accounting.mariadbOperator for the SlurmCluster MariaDB CR." -}}
{{- end -}}
{{- end -}}

{{- define "validateNodeSets" -}}
{{- $names := list -}}
{{- range .Values.nodesets -}}
  {{- $name := required ".Values.nodesets[*].name must be provided." .name -}}
  {{- if has $name $names -}}
    {{- fail (printf "Duplicate nodesets name %q." $name) -}}
  {{- end -}}
  {{- $names = append $names $name -}}
{{- end -}}
{{- $partitionConfig := .Values.partitionConfiguration | default dict -}}
{{- $configType := default "structured" $partitionConfig.configType -}}
{{- if eq $configType "structured" -}}
  {{- range $partition := (get $partitionConfig "partitions" | default list) -}}
    {{- $partitionName := required "partitionConfiguration.partitions[*].name is required." $partition.name -}}
    {{- range ($partition.nodeSetRefs | default list) -}}
      {{- if not (has . $names) -}}
        {{- fail (printf "partitionConfiguration.partitions[%s].nodeSetRefs references NodeSet %q, but no nodesets entry with that name exists." $partitionName .) -}}
      {{- end -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- end -}}

{{- define "validateSlurmMemoryConfig" -}}
{{- $customSlurmConfig := default "" .Values.customSlurmConfig -}}
{{- if regexMatch "(?im)^\\s*DefMemPerCPU\\s*=" $customSlurmConfig -}}
  {{- fail "customSlurmConfig contains DefMemPerCPU, but Soperator 3.0.3 defaults slurmConfig.defMemPerNode=0 and Slurm treats DefMemPerCPU and DefMemPerNode as mutually exclusive. For GPU workers, use DefMemPerGPU with DefCpuPerGPU instead." -}}
{{- end -}}
{{- end -}}

{{- define "validateK8sNodeFilters" -}}
{{- $names := list -}}
{{- range .Values.k8sNodeFilters -}}
  {{- $name := required ".Values.k8sNodeFilters[*].name must be provided." .name -}}
  {{- if has $name $names -}}
    {{- fail (printf "Duplicate k8sNodeFilters name %q." $name) -}}
  {{- end -}}
  {{- $names = append $names $name -}}
{{- end -}}
{{- $checks := list
  (dict "path" "populateJail.k8sNodeFilterName" "name" .Values.populateJail.k8sNodeFilterName)
  (dict "path" "slurmNodes.accounting.k8sNodeFilterName" "name" .Values.slurmNodes.accounting.k8sNodeFilterName)
  (dict "path" "slurmNodes.controller.k8sNodeFilterName" "name" .Values.slurmNodes.controller.k8sNodeFilterName)
  (dict "path" "slurmNodes.login.k8sNodeFilterName" "name" .Values.slurmNodes.login.k8sNodeFilterName)
  (dict "path" "slurmNodes.exporter.k8sNodeFilterName" "name" .Values.slurmNodes.exporter.k8sNodeFilterName)
  (dict "path" "slurmNodes.rest.k8sNodeFilterName" "name" .Values.slurmNodes.rest.k8sNodeFilterName)
  (dict "path" "sConfigController.node.k8sNodeFilterName" "name" .Values.sConfigController.node.k8sNodeFilterName)
-}}
{{- range $check := $checks -}}
  {{- $name := default "" (get $check "name") -}}
  {{- if and $name (not (has $name $names)) -}}
    {{- fail (printf "%s references k8sNodeFilterName %q, but no k8sNodeFilters entry with that name exists." (get $check "path") $name) -}}
  {{- end -}}
{{- end -}}
{{- end -}}

{{/*
Create the name of the service account to use for sconfigcontroller
*/}}
{{- define "slurm-cluster.sconfigcontroller.serviceAccountName" -}}
{{- if .Values.sConfigController.serviceAccount.create -}}
    {{- default (printf "%s-sconfigcontroller" (include "slurm-cluster.name" .)) .Values.sConfigController.serviceAccount.name }}
{{- else -}}
    {{- default "default" .Values.sConfigController.serviceAccount.name }}
{{- end -}}
{{- end -}}

{{/*
Create the name of the role for sconfigcontroller
*/}}
{{- define "slurm-cluster.sconfigcontroller.roleName" -}}
{{- printf "%s-sconfigcontroller" (include "slurm-cluster.name" .) }}
{{- end -}}

{{/*
Create the name of the role binding for sconfigcontroller
*/}}
{{- define "slurm-cluster.sconfigcontroller.roleBindingName" -}}
{{- printf "%s-sconfigcontroller" (include "slurm-cluster.name" .) }}
{{- end -}}

{{/*
Create the name of the service account to use for exporter
*/}}
{{- define "slurm-cluster.exporter.serviceAccountName" -}}
{{- if .Values.slurmNodes.exporter.serviceAccount.create -}}
    {{- default (printf "%s-slurm-exporter" (include "slurm-cluster.name" .)) .Values.slurmNodes.exporter.serviceAccount.name }}
{{- else -}}
    {{- default "default" .Values.slurmNodes.exporter.serviceAccount.name }}
{{- end -}}
{{- end -}}

{{/*
Create the name of the role for exporter
*/}}
{{- define "slurm-cluster.exporter.roleName" -}}
{{- printf "%s-exporter-role" (include "slurm-cluster.name" .) }}
{{- end -}}

{{/*
Create the name of the role binding for exporter
*/}}
{{- define "slurm-cluster.exporter.roleBindingName" -}}
{{- printf "%s-exporter-role-binding" (include "slurm-cluster.name" .) }}
{{- end -}}

{{/*
Create the name of the service account to use for slurm-controller
*/}}
{{- define "slurm-cluster.controller.serviceAccountName" -}}
{{- if .Values.slurmNodes.controller.serviceAccount.create -}}
    {{- default (printf "%s-slurm-controller" (include "slurm-cluster.name" .)) .Values.slurmNodes.controller.serviceAccount.name }}
{{- else -}}
    {{- default "default" .Values.slurmNodes.controller.serviceAccount.name }}
{{- end -}}
{{- end -}}

{{/*
Create the name of the role for slurm-controller
*/}}
{{- define "slurm-cluster.controller.roleName" -}}
{{- printf "%s-slurm-controller" (include "slurm-cluster.name" .) }}
{{- end -}}

{{/*
Create the name of the role binding for slurm-controller
*/}}
{{- define "slurm-cluster.controller.roleBindingName" -}}
{{- printf "%s-slurm-controller" (include "slurm-cluster.name" .) }}
{{- end -}}
