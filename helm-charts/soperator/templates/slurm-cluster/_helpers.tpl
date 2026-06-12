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
  (dict "name" "main" "isAll" true "policy" (dict "default" true "maxTime" "INFINITE" "state" "UP" "priorityTier" 10 "overSubscribe" "YES"))
  (dict "name" "hidden" "isAll" true "policy" (dict "default" false "hidden" true "maxTime" "INFINITE" "state" "UP" "priorityTier" 10 "preemptMode" "OFF" "overSubscribe" "YES"))
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
  {{- fail "customSlurmConfig contains DefMemPerCPU, but Soperator 4.0.1 defaults slurmConfig.defMemPerNode=0 and Slurm treats DefMemPerCPU and DefMemPerNode as mutually exclusive. For GPU workers, use DefMemPerGPU with DefCpuPerGPU instead." -}}
{{- end -}}
{{- end -}}

{{/*
Render one safely shell-quoted argv token for generated Bash source.
*/}}
{{- define "qosShellQuote" -}}
{{- $value := printf "%v" . -}}
'{{ $value | replace "'" "'\\''" }}'
{{- end -}}

{{/*
Render a Bash reconcile script body from .Values.qosConfiguration. The
script is mounted into the post-install/post-upgrade Job, then streamed into
the accounting pod with `kubectl exec -i` so `sacctmgr` uses the same SlurmDBD
auth path as the running cluster without relying on `kubectl cp`. All operations are idempotent:
`sacctmgr -i show` decides whether to `add` or `modify`. The script exits
non-zero on any sacctmgr failure so the Helm hook surfaces errors clearly.
*/}}
{{- define "renderQosReconcileScript" -}}
{{- $cfg := .Values.qosConfiguration | default dict -}}
#!/usr/bin/env bash
set -euo pipefail

if [ -f /etc/profile.d/slurm.sh ]; then
  # shellcheck disable=SC1091
  . /etc/profile.d/slurm.sh
fi

{{- with (get $cfg "preReconcileScript") }}
# --- preReconcileScript ---
{{ . }}
# --- end preReconcileScript ---
{{- end }}

echo "[qos-reconcile] waiting for slurmdbd to be reachable via sacctmgr ..."
for i in $(seq 1 60); do
  if sacctmgr -i show cluster -n >/dev/null 2>&1; then
    echo "[qos-reconcile] slurmdbd is reachable"
    break
  fi
  sleep 5
  if [ "$i" -eq 60 ]; then
    echo "[qos-reconcile] slurmdbd not reachable after 5 minutes" >&2
    exit 1
  fi
done

apply_account() {
  local name="$1"; shift
  local args=("$@")
  if sacctmgr -nP -i show account "$name" format=Account | grep -qx "$name"; then
    if [ "${#args[@]}" -gt 0 ]; then
      echo "[qos-reconcile] modify account ${name}: ${args[*]}"
      sacctmgr -i modify account "$name" set "${args[@]}"
    else
      echo "[qos-reconcile] account ${name} already present"
    fi
  else
    echo "[qos-reconcile] add account ${name} ${args[*]}"
    sacctmgr -i add account "$name" "${args[@]}"
  fi
}

apply_qos() {
  local name="$1"; shift
  local args=("$@")
  if sacctmgr -nP -i show qos "$name" format=Name | grep -qx "$name"; then
    if [ "${#args[@]}" -gt 0 ]; then
      echo "[qos-reconcile] modify qos ${name}: ${args[*]}"
      sacctmgr -i modify qos "$name" set "${args[@]}"
    else
      echo "[qos-reconcile] qos ${name} already present"
    fi
  else
    echo "[qos-reconcile] add qos ${name} ${args[*]}"
    sacctmgr -i add qos "$name" "${args[@]}"
  fi
}

apply_association() {
  local user="$1"; local account="$2"; shift 2
  local args=("$@")
  if sacctmgr -nP -i show association user="$user" account="$account" format=User,Account 2>/dev/null | grep -qx "${user}|${account}"; then
    if [ "${#args[@]}" -gt 0 ]; then
      echo "[qos-reconcile] modify association user=${user} account=${account}: ${args[*]}"
      sacctmgr -i modify user "$user" where account="$account" set "${args[@]}"
    else
      echo "[qos-reconcile] association user=${user} account=${account} already present"
    fi
  else
    echo "[qos-reconcile] add association user=${user} account=${account} ${args[*]}"
    sacctmgr -i add user "$user" account="$account" "${args[@]}"
  fi
}

# --- accounts ---
{{- range $a := get $cfg "accounts" | default list }}
{{- $args := list -}}
{{- with (get $a "organization") }}{{- $args = append $args (printf "Organization=%s" .) }}{{- end }}
{{- with (get $a "description") }}{{- $args = append $args (printf "Description=%s" .) }}{{- end }}
{{- with (get $a "parent") }}{{- $args = append $args (printf "Parent=%s" .) }}{{- end }}
apply_account {{ include "qosShellQuote" $a.name }}{{- range $args }} {{ include "qosShellQuote" . }}{{- end }}
{{- end }}

# --- qos ---
{{- range $q := get $cfg "qos" | default list }}
{{- $args := list -}}
{{- if and (hasKey $q "priority") (ne (get $q "priority") nil) }}{{- $args = append $args (printf "Priority=%d" (int (get $q "priority"))) }}{{- end }}
{{- if and (hasKey $q "maxJobs") (ne (get $q "maxJobs") nil) }}{{- $args = append $args (printf "MaxJobs=%d" (int (get $q "maxJobs"))) }}{{- end }}
{{- if and (hasKey $q "maxWallSeconds") (ne (get $q "maxWallSeconds") nil) -}}
  {{- $w := int (get $q "maxWallSeconds") -}}
  {{- $hh := div $w 3600 -}}
  {{- $mm := mod (div $w 60) 60 -}}
  {{- $ss := mod $w 60 -}}
  {{- $args = append $args (printf "MaxWall=%02d:%02d:%02d" $hh $mm $ss) -}}
{{- end }}
{{- with (get $q "maxTRES") }}{{- $args = append $args (printf "MaxTRES=%s" .) }}{{- end }}
{{- with (get $q "maxTRESPerJob") }}{{- $args = append $args (printf "MaxTRESPerJob=%s" .) }}{{- end }}
{{- $flags := get $q "flags" | default list }}
{{- if $flags }}{{- $args = append $args (printf "Flags=%s" (join "," $flags)) }}{{- end }}
apply_qos {{ include "qosShellQuote" $q.name }}{{- range $args }} {{ include "qosShellQuote" . }}{{- end }}
{{- end }}

# --- qos preemption relationships ---
{{- range $q := get $cfg "qos" | default list }}
{{- $args := list -}}
{{- $preempt := get $q "preempt" | default list }}
{{- if $preempt }}{{- $args = append $args (printf "Preempt=%s" (join "," $preempt)) }}{{- end }}
{{- with (get $q "preemptMode") }}{{- $args = append $args (printf "PreemptMode=%s" .) }}{{- end }}
{{- if $args }}
apply_qos {{ include "qosShellQuote" $q.name }}{{- range $args }} {{ include "qosShellQuote" . }}{{- end }}
{{- end }}
{{- end }}

# --- associations ---
{{- range $assoc := get $cfg "associations" | default list }}
{{- $args := list -}}
{{- with (get $assoc "defaultQos") }}{{- $args = append $args (printf "DefaultQOS=%s" .) }}{{- end }}
{{- $aqos := get $assoc "qos" | default list }}
{{- if $aqos }}{{- $args = append $args (printf "Qos=%s" (join "," $aqos)) }}{{- end }}
{{- with (get $assoc "partition") }}{{- $args = append $args (printf "Partition=%s" .) }}{{- end }}
{{- with (get $assoc "fairshare") }}{{- $args = append $args (printf "Fairshare=%s" .) }}{{- end }}
apply_association {{ include "qosShellQuote" $assoc.user }} {{ include "qosShellQuote" $assoc.account }}{{- range $args }} {{ include "qosShellQuote" . }}{{- end }}
{{- end }}

echo "[qos-reconcile] done"
{{- end -}}

{{/*
Map of typed schedulingConfig fields to their Slurm.conf key names. Order in
the rendered output is intentional and matches typical slurm.conf grouping.
The output is a multi-line string; empty/null fields are skipped.
Each line is "<Key>=<value>" with no surrounding whitespace.
*/}}
{{- define "renderSchedulingConfig" -}}
{{- $cfg := .Values.schedulingConfig | default dict -}}
{{- $lines := list -}}
{{- with (get $cfg "preemptType") -}}
  {{- $lines = append $lines (printf "PreemptType=%s" .) -}}
{{- end -}}
{{- $ase := get $cfg "accountingStorageEnforce" | default list -}}
{{- if $ase -}}
  {{- $lines = append $lines (printf "AccountingStorageEnforce=%s" (join "," $ase)) -}}
{{- end -}}
{{- with (get $cfg "enforcePartLimits") -}}
  {{- $lines = append $lines (printf "EnforcePartLimits=%s" .) -}}
{{- end -}}
{{- with (get $cfg "preemptMode") -}}
  {{- $lines = append $lines (printf "PreemptMode=%s" .) -}}
{{- end -}}
{{- $pp := get $cfg "preemptParameters" | default list -}}
{{- if $pp -}}
  {{- $lines = append $lines (printf "PreemptParameters=%s" (join "," $pp)) -}}
{{- end -}}
{{- if and (hasKey $cfg "jobRequeue") (ne (get $cfg "jobRequeue") nil) -}}
  {{- $lines = append $lines (printf "JobRequeue=%d" (int (get $cfg "jobRequeue"))) -}}
{{- end -}}
{{- with (get $cfg "schedulerType") -}}
  {{- $lines = append $lines (printf "SchedulerType=%s" .) -}}
{{- end -}}
{{- $sp := get $cfg "schedulerParameters" | default list -}}
{{- if $sp -}}
  {{- $lines = append $lines (printf "SchedulerParameters=%s" (join "," $sp)) -}}
{{- end -}}
{{- with (get $cfg "priorityType") -}}
  {{- $lines = append $lines (printf "PriorityType=%s" .) -}}
{{- end -}}
{{- $pw := get $cfg "priorityWeights" | default dict -}}
{{- range $weight := list
  (dict "field" "age" "key" "PriorityWeightAge")
  (dict "field" "assoc" "key" "PriorityWeightAssoc")
  (dict "field" "fairshare" "key" "PriorityWeightFairshare")
  (dict "field" "jobSize" "key" "PriorityWeightJobSize")
  (dict "field" "partition" "key" "PriorityWeightPartition")
  (dict "field" "qos" "key" "PriorityWeightQOS")
-}}
  {{- $field := get $weight "field" -}}
  {{- if and (hasKey $pw $field) (ne (get $pw $field) nil) -}}
    {{- $lines = append $lines (printf "%s=%d" (get $weight "key") (int (get $pw $field))) -}}
  {{- end -}}
{{- end -}}
{{- with (get $pw "tres") -}}
  {{- $lines = append $lines (printf "PriorityWeightTRES=%s" .) -}}
{{- end -}}
{{- join "\n" $lines -}}
{{- end -}}

{{/*
Hard-fail if a typed schedulingConfig key is also present in the raw
customSlurmConfig string. This prevents silent overrides when the operator
applies merged slurm.conf lines.
Matches "<Key>=" at start of any line (case-insensitive, ignoring leading
whitespace).
*/}}
{{- define "validateSchedulingOverlap" -}}
{{- $cfg := .Values.schedulingConfig | default dict -}}
{{- $raw := default "" .Values.customSlurmConfig -}}
{{- $checks := list
  (dict "field" "preemptType"               "key" "PreemptType"             "value" (get $cfg "preemptType"))
  (dict "field" "accountingStorageEnforce"  "key" "AccountingStorageEnforce" "value" (get $cfg "accountingStorageEnforce"))
  (dict "field" "enforcePartLimits"         "key" "EnforcePartLimits"       "value" (get $cfg "enforcePartLimits"))
  (dict "field" "preemptMode"               "key" "PreemptMode"             "value" (get $cfg "preemptMode"))
  (dict "field" "preemptParameters"         "key" "PreemptParameters"       "value" (get $cfg "preemptParameters"))
  (dict "field" "jobRequeue"                "key" "JobRequeue"              "value" (get $cfg "jobRequeue"))
  (dict "field" "schedulerType"             "key" "SchedulerType"           "value" (get $cfg "schedulerType"))
  (dict "field" "schedulerParameters"       "key" "SchedulerParameters"     "value" (get $cfg "schedulerParameters"))
  (dict "field" "priorityType"              "key" "PriorityType"            "value" (get $cfg "priorityType"))
  (dict "field" "priorityWeights.age"       "key" "PriorityWeightAge"       "value" (get (get $cfg "priorityWeights" | default dict) "age"))
  (dict "field" "priorityWeights.assoc"     "key" "PriorityWeightAssoc"     "value" (get (get $cfg "priorityWeights" | default dict) "assoc"))
  (dict "field" "priorityWeights.fairshare" "key" "PriorityWeightFairshare" "value" (get (get $cfg "priorityWeights" | default dict) "fairshare"))
  (dict "field" "priorityWeights.partition" "key" "PriorityWeightPartition" "value" (get (get $cfg "priorityWeights" | default dict) "partition"))
  (dict "field" "priorityWeights.jobSize"   "key" "PriorityWeightJobSize"   "value" (get (get $cfg "priorityWeights" | default dict) "jobSize"))
  (dict "field" "priorityWeights.qos"       "key" "PriorityWeightQOS"       "value" (get (get $cfg "priorityWeights" | default dict) "qos"))
  (dict "field" "priorityWeights.tres"      "key" "PriorityWeightTRES"      "value" (get (get $cfg "priorityWeights" | default dict) "tres"))
-}}
{{- range $check := $checks -}}
  {{- $v := get $check "value" -}}
  {{- $isSet := false -}}
  {{- if kindIs "slice" $v -}}
    {{- if gt (len $v) 0 -}}{{- $isSet = true -}}{{- end -}}
  {{- else if not (kindIs "invalid" $v) -}}
    {{- if ne (printf "%v" $v) "" -}}{{- $isSet = true -}}{{- end -}}
  {{- end -}}
  {{- if $isSet -}}
    {{- $pattern := printf "(?im)^\\s*%s\\s*=" (get $check "key") -}}
    {{- if regexMatch $pattern $raw -}}
      {{- fail (printf "schedulingConfig.%s is set as a typed field, but customSlurmConfig also contains a raw %s= line. Pick one source: either remove %s= from customSlurmConfig, or unset schedulingConfig.%s." (get $check "field") (get $check "key") (get $check "key") (get $check "field")) -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- end -}}

{{/*
Render a partition's typed .policy block into a space-separated string of
Slurm.conf key=value tokens. Empty/null fields are skipped. Output is safe to
concatenate with the partition's raw .config escape hatch.
*/}}
{{- define "renderPartitionPolicy" -}}
{{- $p := .policy | default dict -}}
{{- $tokens := list -}}
{{- if and (hasKey $p "default") (ne (get $p "default") nil) -}}
  {{- if (get $p "default") -}}
    {{- $tokens = append $tokens "Default=YES" -}}
  {{- else -}}
    {{- $tokens = append $tokens "Default=NO" -}}
  {{- end -}}
{{- end -}}
{{- if and (hasKey $p "hidden") (ne (get $p "hidden") nil) -}}
  {{- if (get $p "hidden") -}}
    {{- $tokens = append $tokens "Hidden=YES" -}}
  {{- else -}}
    {{- $tokens = append $tokens "Hidden=NO" -}}
  {{- end -}}
{{- end -}}
{{- with (get $p "state") -}}
  {{- $tokens = append $tokens (printf "State=%s" .) -}}
{{- end -}}
{{- with (get $p "maxTime") -}}
  {{- $tokens = append $tokens (printf "MaxTime=%s" .) -}}
{{- end -}}
{{- with (get $p "defaultTime") -}}
  {{- $tokens = append $tokens (printf "DefaultTime=%s" .) -}}
{{- end -}}
{{- if and (hasKey $p "priorityTier") (ne (get $p "priorityTier") nil) -}}
  {{- $tokens = append $tokens (printf "PriorityTier=%d" (int (get $p "priorityTier"))) -}}
{{- end -}}
{{- with (get $p "preemptMode") -}}
  {{- $tokens = append $tokens (printf "PreemptMode=%s" .) -}}
{{- end -}}
{{- if and (hasKey $p "defMemPerNode") (ne (get $p "defMemPerNode") nil) -}}
  {{- $tokens = append $tokens (printf "DefMemPerNode=%d" (int (get $p "defMemPerNode"))) -}}
{{- end -}}
{{- if and (hasKey $p "defMemPerCPU") (ne (get $p "defMemPerCPU") nil) -}}
  {{- $tokens = append $tokens (printf "DefMemPerCPU=%d" (int (get $p "defMemPerCPU"))) -}}
{{- end -}}
{{- if and (hasKey $p "defMemPerGPU") (ne (get $p "defMemPerGPU") nil) -}}
  {{- $tokens = append $tokens (printf "DefMemPerGPU=%d" (int (get $p "defMemPerGPU"))) -}}
{{- end -}}
{{- if and (hasKey $p "defCpuPerGPU") (ne (get $p "defCpuPerGPU") nil) -}}
  {{- $tokens = append $tokens (printf "DefCpuPerGPU=%d" (int (get $p "defCpuPerGPU"))) -}}
{{- end -}}
{{- with (get $p "overSubscribe") -}}
  {{- $tokens = append $tokens (printf "OverSubscribe=%s" .) -}}
{{- end -}}
{{- $aa := get $p "allowAccounts" | default list -}}
{{- if $aa -}}
  {{- $tokens = append $tokens (printf "AllowAccounts=%s" (join "," $aa)) -}}
{{- end -}}
{{- $aq := get $p "allowQos" | default list -}}
{{- if $aq -}}
  {{- $tokens = append $tokens (printf "AllowQos=%s" (join "," $aq)) -}}
{{- end -}}
{{- $da := get $p "denyAccounts" | default list -}}
{{- if $da -}}
  {{- $tokens = append $tokens (printf "DenyAccounts=%s" (join "," $da)) -}}
{{- end -}}
{{- $dq := get $p "denyQos" | default list -}}
{{- if $dq -}}
  {{- $tokens = append $tokens (printf "DenyQos=%s" (join "," $dq)) -}}
{{- end -}}
{{- join " " $tokens -}}
{{- end -}}

{{/*
Hard-fail if a partition's typed .policy field is also present in the same
partition's raw .config string. Same rationale as validateSchedulingOverlap:
prevent silent overrides at slurm.conf merge time.
*/}}
{{- define "validatePartitionPolicyOverlap" -}}
{{- $partitionConfig := .Values.partitionConfiguration | default dict -}}
{{- $configType := default "structured" $partitionConfig.configType -}}
{{- if ne $configType "structured" -}}{{- /* policy only applies in structured mode */ -}}
{{- else -}}
{{- range $partition := (get $partitionConfig "partitions" | default list) -}}
  {{- $policy := get $partition "policy" | default dict -}}
  {{- $raw := default "" (get $partition "config") -}}
  {{- $pname := get $partition "name" -}}
  {{- $checks := list
    (dict "field" "default"        "key" "Default"        "value" (get $policy "default"))
    (dict "field" "hidden"         "key" "Hidden"         "value" (get $policy "hidden"))
    (dict "field" "state"          "key" "State"          "value" (get $policy "state"))
    (dict "field" "maxTime"        "key" "MaxTime"        "value" (get $policy "maxTime"))
    (dict "field" "defaultTime"    "key" "DefaultTime"    "value" (get $policy "defaultTime"))
    (dict "field" "priorityTier"   "key" "PriorityTier"   "value" (get $policy "priorityTier"))
    (dict "field" "preemptMode"    "key" "PreemptMode"    "value" (get $policy "preemptMode"))
    (dict "field" "defMemPerNode"  "key" "DefMemPerNode"  "value" (get $policy "defMemPerNode"))
    (dict "field" "defMemPerCPU"   "key" "DefMemPerCPU"   "value" (get $policy "defMemPerCPU"))
    (dict "field" "defMemPerGPU"   "key" "DefMemPerGPU"   "value" (get $policy "defMemPerGPU"))
    (dict "field" "defCpuPerGPU"   "key" "DefCpuPerGPU"   "value" (get $policy "defCpuPerGPU"))
    (dict "field" "overSubscribe"  "key" "OverSubscribe"  "value" (get $policy "overSubscribe"))
    (dict "field" "allowAccounts"  "key" "AllowAccounts"  "value" (get $policy "allowAccounts"))
    (dict "field" "allowQos"       "key" "AllowQos"       "value" (get $policy "allowQos"))
    (dict "field" "denyAccounts"   "key" "DenyAccounts"   "value" (get $policy "denyAccounts"))
    (dict "field" "denyQos"        "key" "DenyQos"        "value" (get $policy "denyQos"))
  -}}
  {{- range $check := $checks -}}
    {{- $v := get $check "value" -}}
    {{- $isSet := false -}}
    {{- if kindIs "slice" $v -}}
      {{- if gt (len $v) 0 -}}{{- $isSet = true -}}{{- end -}}
    {{- else if not (kindIs "invalid" $v) -}}
      {{- if ne (printf "%v" $v) "" -}}{{- $isSet = true -}}{{- end -}}
    {{- end -}}
    {{- if $isSet -}}
      {{- $pattern := printf "(?i)(^|\\s)%s\\s*=" (get $check "key") -}}
      {{- if regexMatch $pattern $raw -}}
        {{- fail (printf "partitionConfiguration.partitions[name=%q].policy.%s is set as a typed field, but the same partition's .config also contains a raw %s= token. Pick one source: either remove %s= from .config, or unset .policy.%s." $pname (get $check "field") (get $check "key") (get $check "key") (get $check "field")) -}}
      {{- end -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
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
