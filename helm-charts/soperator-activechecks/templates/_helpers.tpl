{{/*
Expand the name of the chart.
*/}}
{{- define "soperator-activechecks.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "soperator-activechecks.fullname" -}}
{{- default .Release.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}
{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "soperator-activechecks.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "soperator-activechecks.labels" -}}
helm.sh/chart: {{ include "soperator-activechecks.chart" . }}
{{ include "soperator-activechecks.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "soperator-activechecks.selectorLabels" -}}
app.kubernetes.io/name: {{ include "soperator-activechecks.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "soperator-activechecks.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "soperator-activechecks.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Pyxis format for active check image.
*/}}
{{- define "activecheck.image.pyxis" -}}
{{- include "activecheck.image.resolve" . -}}
{{- .Values.activeCheckImage -}}
{{- end -}}

{{/*
Resolve active check image when not explicitly set.
*/}}
{{- define "activecheck.image.resolve" -}}
{{- if not .Values.activeCheckImage -}}
{{- $cudaVersion := default "12.9.0" .Values.cudaVersion -}}
{{- $tag := required "activeCheck image tag for the selected CUDA version must be provided." (index .Values.images.activeCheckImageTags (printf "%v" $cudaVersion)) -}}
{{- $repo := required "activeCheck image repository must be provided." .Values.images.activeCheckImageRepository -}}
{{- $_ := set .Values "activeCheckImage" (printf "%s:%s" $repo $tag) -}}
{{- end -}}
{{- end -}}

{{/*
Validate that enabled checks depend only on checks that are also enabled.
Invoke with (dict "name" "<checkKey>" "check" $check "checks" .Values.checks)
*/}}
{{- define "soperator-activechecks.checkDependencies" -}}
{{- $name := .name -}}
{{- $check := default dict .check -}}
{{- $checks := default dict .checks -}}
{{- range $dependency := default (list) $check.dependsOn }}
  {{- $dependencyCheck := index $checks $dependency -}}
  {{- if not $dependencyCheck -}}
    {{- fail (printf "checks.%s.dependsOn references unknown check %q" $name $dependency) -}}
  {{- end -}}
  {{- if not $dependencyCheck.enabled -}}
    {{- fail (printf "checks.%s.dependsOn references disabled check %q" $name $dependency) -}}
  {{- end -}}
{{- end -}}
{{- end -}}


{{/*
Docker format for active check image.
Converts from format "reg#repo:tag" to format "reg/repo:tag".
*/}}
{{- define "activecheck.image.docker" -}}
{{- include "activecheck.image.pyxis" . | replace "#" "/" -}}
{{- end -}}

{{/*
Resolve NCCL tests version from cudaVersion.
If .Values.ncclTestsVersion is non-empty, use it (flat override).
Otherwise, look up from .Values.ncclTestsVersions map.
*/}}
{{- define "soperator-activechecks.ncclTestsVersion" -}}
{{- if .Values.ncclTestsVersion -}}
  {{- .Values.ncclTestsVersion -}}
{{- else -}}
  {{- required (printf "ncclTestsVersions must contain an entry for CUDA %s, or set ncclTestsVersion explicitly" .Values.cudaVersion) (index .Values.ncclTestsVersions (printf "%v" .Values.cudaVersion)) -}}
{{- end -}}
{{- end -}}

{{/*
Validate that a script path references a packaged chart file.
*/}}
{{- define "soperator-activechecks.validateScriptPath" -}}
{{- $field := default "script file" .field -}}
{{- $path := required (printf "%s is required" $field) .path -}}
{{- if eq (len (.ctx.Files.Glob $path)) 0 -}}
{{- fail (printf "%s references missing chart file %q" $field $path) -}}
{{- end -}}
{{- $path -}}
{{- end -}}

{{/*
Render script content from a file with optional tpl evaluation.
*/}}
{{- define "soperator-activechecks.renderScript" -}}
{{- $path := include "soperator-activechecks.validateScriptPath" . | trim -}}
{{- $content := .ctx.Files.Get $path -}}
{{- $headlessSuffix := printf "%s-login-headless-svc.%s.svc.cluster.local" .ctx.Values.slurmClusterRefName .ctx.Release.Namespace -}}
{{- $content = replace "soperator-login-headless-svc.soperator.svc.cluster.local" $headlessSuffix $content -}}
{{- $loginTarget := printf "soperatorchecks@login-${i}.%s hostname" $headlessSuffix -}}
{{- $content = replace "soperatorchecks@login-$i hostname" $loginTarget $content -}}
{{- $srunReadyPartition := default "hidden" .ctx.Values.srunReadyPartition -}}
{{- $content = replace "--partition=hidden" (printf "--partition=%s" $srunReadyPartition) $content -}}
{{- tpl $content .ctx -}}
{{- end -}}

{{/*
Render munge container with defaults that match the existing checks.
*/}}
{{- define "soperator-activechecks.renderMungeContainer" -}}
{{- $ctx := default .ctx .renderCtx -}}
{{- $raw := default dict .container -}}
{{- $container := fromYaml (tpl (toYaml $raw) $ctx) -}}
{{- $image := default $ctx.Values.images.munge $container.image -}}
{{- $appArmor := default "unconfined" $container.appArmorProfile -}}
appArmorProfile: {{ $appArmor }}
image: {{ tpl $image $ctx | quote }}
{{- end -}}

{{/*
Render slurmJobSpec for an ActiveCheck.
*/}}
{{- define "soperator-activechecks.slurmJobSpec" -}}
{{- $ctx := .ctx -}}
{{- $name := .name -}}
{{- $spec := default dict .check.slurmJobSpec -}}
{{- $jobContainerRaw := default dict $spec.jobContainer -}}
{{- $baseContainer := dict "appArmorProfile" "unconfined" "image" $ctx.Values.images.slurmJob "env" $ctx.Values.jobContainer.env "volumeMounts" $ctx.Values.jobContainer.volumeMounts "volumes" $ctx.Values.jobContainer.volumes -}}
{{- $jobContainer := mustMerge (omit $jobContainerRaw "extraEnv" "extraVolumeMounts" "extraVolumes") $baseContainer -}}
{{- $env := default (list) $jobContainer.env -}}
{{- $workingDir := default "" $jobContainer.workingDir -}}
{{- with $jobContainerRaw.extraEnv }}{{- $env = concat $env . -}}{{- end }}
{{- $volumeMounts := default (list) $jobContainer.volumeMounts -}}
{{- with $jobContainerRaw.extraVolumeMounts }}{{- $volumeMounts = concat $volumeMounts . -}}{{- end }}
{{- $volumes := default (list) $jobContainer.volumes -}}
{{- with $jobContainerRaw.extraVolumes }}{{- $volumes = concat $volumes . -}}{{- end }}
{{- $script := "" -}}
{{- $sbatchScriptFileField := printf "checks.%s.slurmJobSpec.sbatchScriptFile" $name -}}
{{- if $spec.sbatchScript }}
{{- $script = tpl $spec.sbatchScript $ctx -}}
{{- if hasKey $spec "sbatchScriptFile" }}
{{- $_ := include "soperator-activechecks.validateScriptPath" (dict "path" $spec.sbatchScriptFile "field" $sbatchScriptFileField "ctx" $ctx) -}}
{{- end }}
{{- else }}
{{- $script = include "soperator-activechecks.renderScript" (dict "path" $spec.sbatchScriptFile "field" $sbatchScriptFileField "ctx" $ctx) -}}
{{- end }}
sbatchScript: |
{{ $script | indent 2 }}
{{- if hasKey $spec "eachWorkerJobs" }}
eachWorkerJobs: {{ $spec.eachWorkerJobs }}
{{- end }}
{{- with $spec.maxNumberOfJobs }}
maxNumberOfJobs: {{ . }}
{{- end }}
jobContainer:
{{- if $workingDir }}
  workingDir: {{ $workingDir | quote }}
{{- end }}
  appArmorProfile: {{ $jobContainer.appArmorProfile }}
  image: {{ tpl $jobContainer.image $ctx | quote }}
{{- with $jobContainer.command }}
  command:
{{ toYaml . | indent 4 }}
{{- end }}
{{- with $jobContainer.args }}
  args:
{{ toYaml . | indent 4 }}
{{- end }}
{{- if $env }}
  env:
{{ toYaml $env | indent 4 }}
{{- end }}
{{- if $volumeMounts }}
  volumeMounts:
{{ toYaml $volumeMounts | indent 4 }}
{{- end }}
{{- if $volumes }}
  volumes:
{{ toYaml $volumes | indent 4 }}
{{- end }}
mungeContainer:
{{ include "soperator-activechecks.renderMungeContainer" (dict "ctx" $ctx "container" $spec.mungeContainer) | indent 2 }}
{{- end -}}

{{/*
Render k8sJobSpec for an ActiveCheck.
*/}}
{{- define "soperator-activechecks.k8sJobSpec" -}}
{{- $ctx := .ctx -}}
{{- $name := .name -}}
{{- $spec := default dict .check.k8sJobSpec -}}
{{- $jobContainerRaw := default dict $spec.jobContainer -}}
{{- $useCommonVolumeMounts := default true $spec.useCommonVolumeMounts -}}
{{- $useCommonVolumes := default true $spec.useCommonVolumes -}}
{{- $includeCommonEnv := default false $spec.includeCommonEnv -}}
{{- $baseContainer := dict "image" $ctx.Values.images.k8sJob -}}
{{- if $useCommonVolumeMounts }}{{- $_ := set $baseContainer "volumeMounts" $ctx.Values.jobContainer.volumeMounts -}}{{- end }}
{{- if $includeCommonEnv }}{{- $_ := set $baseContainer "env" $ctx.Values.jobContainer.env -}}{{- end }}
{{- $jobContainer := mustMerge (omit $jobContainerRaw "extraEnv" "extraVolumeMounts" "extraVolumes") $baseContainer -}}
{{- $env := default (list) $jobContainer.env -}}
{{- $workingDir := default "" $jobContainer.workingDir -}}
{{- with $jobContainerRaw.extraEnv }}{{- $env = concat $env . -}}{{- end }}
{{- if eq $workingDir "/opt/ansible" -}}
  {{- with $ctx.Values.cudaVersion }}{{ $env = concat $env (list (dict "name" "CUDA_VERSION" "value" (printf "%v" .))) -}}{{- end }}
  {{- $env = concat $env (list (dict "name" "NCCL_TESTS_VERSION" "value" (include "soperator-activechecks.ncclTestsVersion" $ctx))) -}}
{{- end -}}
{{- $volumeMounts := default (list) $jobContainer.volumeMounts -}}
{{- with $jobContainerRaw.extraVolumeMounts }}{{- $volumeMounts = concat $volumeMounts . -}}{{- end }}
{{- $volumes := list -}}
{{- if $useCommonVolumes }}{{- $volumes = $ctx.Values.jobContainer.volumes -}}{{- end }}
{{- if $spec.volumes }}{{- $volumes = $spec.volumes -}}{{- end }}
{{- with $spec.extraVolumes }}{{- $volumes = concat $volumes . -}}{{- end }}
{{- $command := $jobContainer.command -}}
{{- if hasKey $spec "scriptFile" }}
{{- $field := printf "checks.%s.k8sJobSpec.scriptFile" $name -}}
{{- if not $command }}
{{- $command = list "bash" "-c" (include "soperator-activechecks.renderScript" (dict "path" $spec.scriptFile "field" $field "ctx" $ctx)) -}}
{{- else }}
{{- $_ := include "soperator-activechecks.validateScriptPath" (dict "path" $spec.scriptFile "field" $field "ctx" $ctx) -}}
{{- end }}
{{- end }}
{{- if hasKey $spec "pythonScriptFile" }}
{{- $field := printf "checks.%s.k8sJobSpec.pythonScriptFile" $name -}}
{{- if not $command }}
{{- $command = list "bash" "-c" (printf "python3 - <<'PY'\n%s\nPY" (include "soperator-activechecks.renderScript" (dict "path" $spec.pythonScriptFile "field" $field "ctx" $ctx))) -}}
{{- else }}
{{- $_ := include "soperator-activechecks.validateScriptPath" (dict "path" $spec.pythonScriptFile "field" $field "ctx" $ctx) -}}
{{- end }}
{{- end }}
{{- $args := $jobContainer.args -}}
{{- $image := tpl (default $ctx.Values.images.k8sJob $jobContainer.image) $ctx }}
jobContainer:
{{- if $workingDir }}
  workingDir: {{ $workingDir | quote }}
{{- end }}
  image: {{ $image | quote }}
{{- with $jobContainer.appArmorProfile }}
  appArmorProfile: {{ . }}
{{- end }}
{{- if $command }}
  command:
{{ toYaml $command | indent 4 }}
{{- end }}
{{- if $args }}
  args:
{{ toYaml $args | indent 4 }}
{{- end }}
{{- if $env }}
  env:
{{ tpl (toYaml $env) .ctx | indent 4 }}
{{- end }}
{{- if $volumeMounts }}
  volumeMounts:
{{ toYaml $volumeMounts | indent 4 }}
{{- end }}
{{- if $volumes }}
  volumes:
{{ toYaml $volumes | indent 4 }}
{{- end }}
{{- if and $spec.mungeContainer $spec.mungeContainer.enabled }}
mungeContainer:
{{ include "soperator-activechecks.renderMungeContainer" (dict "ctx" $ctx "container" $spec.mungeContainer) | indent 2 }}
{{- end }}
{{- end -}}

{{/*
Validate that a check does not enable both commentSlurmNode and drainSlurmNode
under `failureReactions` for a single check. Invoke with (dict "name" "<checkKey>" "vals" .Values)
*/}}
{{- define "soperator-activechecks.checkReactionsConflict" -}}
{{- $name := .name -}}
{{- $vals := .vals -}}
{{- if $vals }}
  {{- $checks := index $vals "checks" -}}
  {{- if $checks }}
    {{- $check := index $checks $name -}}
    {{- if $check }}
      {{- $fr := index $check "failureReactions" -}}
      {{- if $fr }}
        {{- $commentVal := default "" (index (default dict (index $fr "commentSlurmNode")) "commentPrefix") -}}
        {{- $drainVal := default "" (index (default dict (index $fr "drainSlurmNode")) "drainReasonPrefix") -}}
        {{- if and (ne $commentVal "") (ne $drainVal "") -}}
          {{- fail (printf "checks.%s.failureReactions: cannot set both commentSlurmNode and drainSlurmNode simultaneously" $name) -}}
        {{- end -}}
      {{- end -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- end -}}
