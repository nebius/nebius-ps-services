{{- define "nccl-test.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "nccl-test.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- include "nccl-test.name" . -}}
{{- end -}}
{{- end -}}

{{- define "nccl-test.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "nccl-test.labels" -}}
helm.sh/chart: {{ include "nccl-test.chart" . }}
app.kubernetes.io/name: {{ include "nccl-test.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: nccl-test
{{- end -}}

{{- define "nccl-test.selectorLabels" -}}
app.kubernetes.io/name: {{ include "nccl-test.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "nccl-test.imageRef" -}}
{{- $image := . -}}
{{- $repo := required "image.repository is required" $image.repository -}}
{{- if $image.digest -}}
{{- printf "%s@%s" $repo $image.digest -}}
{{- else -}}
{{- $tag := required "image.tag is required when image.digest is empty" $image.tag -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}

{{- define "nccl-test.imagePullSecrets" -}}
{{- $pullSecrets := concat (default (list) .Values.global.imagePullSecrets) (default (list) .Values.imagePullSecrets) -}}
{{- if $pullSecrets }}
imagePullSecrets:
  {{- range $_, $secret := $pullSecrets }}
  {{- if kindIs "string" $secret }}
  - name: {{ $secret | quote }}
  {{- else }}
  - {{- toYaml $secret | nindent 4 | trim }}
  {{- end }}
  {{- end }}
{{- end -}}
{{- end -}}

{{- define "nccl-test.mpiCommand" -}}
{{- $tokens := list "mpirun" "-np" (printf "%d" (mul (int .Values.worker.replicas) (int .Values.worker.gpus))) -}}
{{- range .Values.benchmark.mpiBaseArgs }}
{{- $tokens = append $tokens . -}}
{{- end -}}
{{- $transport := .Values.benchmark.transport | default dict -}}
{{- $transportMode := lower (default "auto" $transport.mode) -}}
{{- if eq $transportMode "socket" -}}
{{- $tokens = concat $tokens (list "-x" "NCCL_NET=Socket" "-x" "NCCL_IB_DISABLE=1") -}}
{{- else if eq $transportMode "rdma" -}}
{{- $tokens = concat $tokens (list "-x" "NCCL_NET=IB") -}}
{{- else if ne $transportMode "auto" -}}
{{- fail "benchmark.transport.mode must be one of auto, socket, rdma" -}}
{{- end -}}
{{- with $transport.socketIfName }}
{{- $tokens = append $tokens "-x" (printf "NCCL_SOCKET_IFNAME=%s" .) -}}
{{- end -}}
{{- with $transport.ibHca }}
{{- $tokens = append $tokens "-x" (printf "NCCL_IB_HCA=%s" .) -}}
{{- end -}}
{{- range .Values.benchmark.mpiExtraArgs }}
{{- $tokens = append $tokens . -}}
{{- end -}}
{{- $tokens = append $tokens (required "benchmark.binaryPath is required" .Values.benchmark.binaryPath) -}}
{{- range .Values.benchmark.args }}
{{- $tokens = append $tokens . -}}
{{- end -}}
{{- $tokens = append $tokens "-J" -}}
{{- $tokens = append $tokens (required "benchmark.resultJsonPath is required" .Values.benchmark.resultJsonPath) -}}
{{- range $index, $token := $tokens -}}
{{- if gt $index 0 }} {{ end -}}
{{- squote $token -}}
{{- end -}}
{{- end -}}

{{- define "nccl-test.waitForWorkersScript" -}}
{{- $timeout := int .Values.launcher.waitForWorkers.timeoutSeconds -}}
{{- $poll := int .Values.launcher.waitForWorkers.pollSeconds -}}
{{- $namespace := .Release.Namespace -}}
{{- $fullname := include "nccl-test.fullname" . -}}
{{- $replicas := int .Values.worker.replicas -}}
{{- if .Values.launcher.waitForWorkers.enabled }}
deadline=$(( $(date +%s) + {{ $timeout }} ))
for ordinal in $(seq 0 $(( {{ $replicas }} - 1 ))); do
  pod="{{ $fullname }}-worker-${ordinal}"
  while true; do
    phase=$(/opt/kube/kubectl get pod "$pod" -n {{ $namespace | squote }} -o jsonpath='{.status.phase}' 2>/dev/null || true)
    ready=$(/opt/kube/kubectl get pod "$pod" -n {{ $namespace | squote }} -o jsonpath='{.status.containerStatuses[?(@.name=="nccl")].ready}' 2>/dev/null || true)
    if [ "$phase" = "Running" ] && [ "$ready" = "true" ]; then
      break
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "Timed out waiting for NCCL worker pod ${pod} main container readiness." >&2
      exit 1
    fi
    sleep {{ $poll }}
  done
done
{{- end }}
{{- end -}}
