{{/* Works as the sprig/kebabcase without putting dash before the first digit in the word */}}
{{- define "mashedkebab" -}}
    {{- /* CamelCase -> kebab */ -}}
    {{- $s := regexReplaceAll "([a-z])([A-Z])" . "${1}-${2}" -}}
    {{- $s = regexReplaceAll "([A-Z])([A-Z][a-z])" $s "${1}-${2}" -}}
    {{- /* Lower uppercases */ -}}
    {{- $s = lower $s -}}
    {{- /* Turn alphanumericals into dash */ -}}
    {{- $s = regexReplaceAll "[^a-z0-9]+" $s "-" -}}
    {{- /* Compress sequence of dashes into one dash */ -}}
    {{- regexReplaceAll "-{2,}" $s "-" -}}
{{- end }}

{{/*
---
*/}}

{{/* Cluster-scoped ConfigMap name for storage mount helper scripts. */}}
{{- define "slurm-cluster-storage.mountScriptsConfigMapName" -}}
{{- printf "%s-mount-scripts" (include "slurm-cluster.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Local storage class */}}
{{- define "slurm-cluster-storage.class.local.name" -}}
    {{- required "Local storage class name is required." .Values.storageClass.local.name | trim | include "mashedkebab" | quote -}}
{{- end }}

{{/* Mount DaemonSet image */}}
{{- define "slurm-cluster-storage.mount.image" -}}
    {{- $image := .Values.storage.mountImage | default dict -}}
    {{- $repository := default "cr.eu-north1.nebius.cloud/soperator/busybox" $image.repository -}}
    {{- $tag := default "latest" $image.tag -}}
    {{- printf "%s:%s" $repository $tag -}}
{{- end }}

{{/* Mount DaemonSet image pull policy */}}
{{- define "slurm-cluster-storage.mount.imagePullPolicy" -}}
    {{- $image := .Values.storage.mountImage | default dict -}}
    {{- default "IfNotPresent" $image.pullPolicy -}}
{{- end }}

{{/*
---
*/}}

{{/* Active/passive jail rootfs strategy. */}}
{{- define "slurm-cluster-storage.jailRootfs.strategy" -}}
    {{- default "activePassive" .Values.jailRootfs.strategy | trim -}}
{{- end }}

{{/* Whether active/passive jail rootfs storage is enabled. */}}
{{- define "slurm-cluster-storage.jailRootfs.activePassive.enabled" -}}
    {{- if eq (include "slurm-cluster-storage.jailRootfs.strategy" .) "activePassive" -}}true{{- else -}}false{{- end -}}
{{- end }}

{{/* Active/passive jail store mount path on Kubernetes hosts. */}}
{{- define "slurm-cluster-storage.jailRootfs.store.path" -}}
    {{- default "/mnt/jail-store" .Values.jailRootfs.store.mountPath | trim -}}
{{- end }}

{{/* Active/passive jail rootfs generations path on Kubernetes hosts. */}}
{{- define "slurm-cluster-storage.jailRootfs.rootfs.path" -}}
    {{- default (printf "%s/rootfs" (include "slurm-cluster-storage.jailRootfs.store.path" .)) .Values.jailRootfs.store.rootfsPath | trim -}}
{{- end }}

{{/* Active/passive slot local path. Usage: include helper with (list $ "slot-a"). */}}
{{- define "slurm-cluster-storage.jailRootfs.slot.path" -}}
    {{- $root := index . 0 -}}
    {{- $slotName := index . 1 -}}
    {{- $slots := default dict $root.Values.jailRootfs.slots -}}
    {{- $slot := get $slots $slotName | default dict -}}
    {{- $rootfsPath := include "slurm-cluster-storage.jailRootfs.rootfs.path" $root -}}
    {{- $managedDefault := printf "/mnt/jail-store/rootfs/%s" $slotName -}}
    {{- $configured := default "" $slot.localPath | trim -}}
    {{- if or (not $configured) (eq $configured $managedDefault) -}}
        {{- printf "%s/%s" $rootfsPath $slotName -}}
    {{- else -}}
        {{- $configured -}}
    {{- end -}}
{{- end }}

{{/* Active/passive slot volume source name. Usage: include helper with (list $ "slot-a"). */}}
{{- define "slurm-cluster-storage.jailRootfs.slot.volumeSourceName" -}}
    {{- $root := index . 0 -}}
    {{- $slotName := index . 1 -}}
    {{- $slots := default dict $root.Values.jailRootfs.slots -}}
    {{- $slot := get $slots $slotName | default dict -}}
    {{- default (printf "jail-rootfs-%s" $slotName) $slot.volumeSourceName | trim -}}
{{- end }}

{{/* Active/passive slot PVC name. Usage: include helper with (list $ "slot-a"). */}}
{{- define "slurm-cluster-storage.jailRootfs.slot.pvc" -}}
    {{- $root := index . 0 -}}
    {{- $slotName := index . 1 -}}
    {{- $slots := default dict $root.Values.jailRootfs.slots -}}
    {{- $slot := get $slots $slotName | default dict -}}
    {{- default (printf "jail-rootfs-%s-pvc" $slotName) $slot.pvcName | trim | quote -}}
{{- end }}

{{/* Active/passive slot PV name. Usage: include helper with (list $ "slot-a"). */}}
{{- define "slurm-cluster-storage.jailRootfs.slot.pv" -}}
    {{- cat (include "slurm-cluster-storage.jailRootfs.slot.volumeSourceName" .) "pv" | include "mashedkebab" | quote -}}
{{- end }}

{{/* Persistent jail mount stable volume source name. Usage: include helper with (list $ mount). */}}
{{- define "slurm-cluster-storage.jailPersistentMount.name" -}}
    {{- $mount := index . 1 -}}
    {{- $path := required "jailPersistentMounts[].mountPath is required." $mount.mountPath -}}
    {{- $slug := trimAll "-" (regexReplaceAll "[^a-z0-9]+" (lower (trimAll "/" $path)) "-") -}}
    {{- $slug = default "root" $slug -}}
    {{- $base := printf "jail-persistent-%s" $slug -}}
    {{- if gt (len $base) 52 -}}
        {{- printf "%s-%s" (trimSuffix "-" (trunc 43 $base)) (trunc 8 (sha1sum $path)) -}}
    {{- else -}}
        {{- $base -}}
    {{- end -}}
{{- end }}

{{/* Persistent jail mount PVC name. Usage: include helper with (list $ mount). */}}
{{- define "slurm-cluster-storage.jailPersistentMount.pvc" -}}
    {{- printf "%s-pvc" (include "slurm-cluster-storage.jailPersistentMount.name" .) | quote -}}
{{- end }}

{{/* Persistent jail mount PV name. Usage: include helper with (list $ mount). */}}
{{- define "slurm-cluster-storage.jailPersistentMount.pv" -}}
    {{- printf "%s-pv" (include "slurm-cluster-storage.jailPersistentMount.name" .) | quote -}}
{{- end }}

{{/* Persistent jail mount local path. Usage: include helper with (list $ mount). */}}
{{- define "slurm-cluster-storage.jailPersistentMount.path" -}}
    {{- $mount := index . 1 -}}
    {{- trimSuffix "/" (required "jailPersistentMounts[].localPath is required." $mount.localPath | trim) -}}
{{- end }}

{{/* Jail volume */}}
{{- define "slurm-cluster-storage.volume.jail.name" -}}
    {{- required "Jail volume name is required." .Values.volume.jail.name | trim | include "mashedkebab" -}}
{{- end }}

{{/* Jail PVC name */}}
{{- define "slurm-cluster-storage.volume.jail.pvc" -}}
    {{- cat (include "slurm-cluster-storage.volume.jail.name" .) "pvc" | include "mashedkebab" | quote -}}
{{- end }}

{{/* Jail PV name */}}
{{- define "slurm-cluster-storage.volume.jail.pv" -}}
    {{- cat (include "slurm-cluster-storage.volume.jail.name" .) "pv" | include "mashedkebab" | quote -}}
{{- end }}

{{/* Jail mount name */}}
{{- define "slurm-cluster-storage.volume.jail.mount" -}}
    {{- cat (include "slurm-cluster-storage.volume.jail.name" .) "mount" | include "mashedkebab" | quote -}}
{{- end }}

{{/* Jail storage class name */}}
{{- define "slurm-cluster-storage.volume.jail.storageClass" -}}
    {{- include "slurm-cluster-storage.class.local.name" . -}}
{{- end }}

{{/* Jail size */}}
{{- define "slurm-cluster-storage.volume.jail.size" -}}
    {{- $sfs := get .Values "sfs" | default dict -}}
    {{- $filesystems := get $sfs "filesystems" | default dict -}}
    {{- $filesystem := get $filesystems "jail" | default dict -}}
    {{- $size := coalesce .Values.volume.jail.size (get $filesystem "size_gib") -}}
    {{- if kindIs "float64" $size -}}
        {{- printf "%.0fGi" $size -}}
    {{- else if kindIs "int" $size -}}
        {{- printf "%dGi" $size -}}
    {{- else -}}
        {{- required "Jail volume size is required." $size -}}
    {{- end -}}
{{- end }}

{{/* Jail storage type */}}
{{- define "slurm-cluster-storage.volume.jail.type" -}}
    {{- if not (or (eq .Values.volume.jail.type "filestore") (eq .Values.volume.jail.type "glusterfs") (eq .Values.volume.jail.type "local")) -}}
        {{- fail "Jail volume type must be one of 'filestore', 'glusterfs', or 'local'." -}}
    {{- end }}
    {{- required "Jail volume type is required." .Values.volume.jail.type | trim -}}
{{- end }}

{{/* Jail local path */}}
{{- define "slurm-cluster-storage.volume.jail.path" -}}
    {{- default "/mnt/jail" .Values.volume.jail.localPath | trim -}}
{{- end }}

{{/* Jail filestore device name */}}
{{- define "slurm-cluster-storage.volume.jail.device" -}}
    {{- if eq .Values.volume.jail.type "filestore" -}}
        {{- $sfs := get .Values "sfs" | default dict -}}
        {{- $filesystems := get $sfs "filesystems" | default dict -}}
        {{- $filesystem := get $filesystems "jail" | default dict -}}
        {{- coalesce .Values.volume.jail.filestoreDeviceName (get $filesystem "mount_tag") | required "Jail volume filestore device name is required." | trim | include "mashedkebab" -}}
    {{- else }}
        {{- "" -}}
    {{- end }}
{{- end }}

{{/* Jail GlusterFS host name */}}
{{- define "slurm-cluster-storage.volume.jail.hostname" -}}
    {{- if eq .Values.volume.jail.type "glusterfs" -}}
        {{- required "Jail volume GlusterFS hostname is required." .Values.volume.jail.glusterfsHostName | trim | include "mashedkebab" -}}
    {{- else }}
        {{- "" -}}
    {{- end }}
{{- end }}

{{/*
---
*/}}

{{/* Controller spool volume */}}
{{- define "slurm-cluster-storage.volume.controller-spool.name" -}}
    {{- required "Controller spool volume name is required." .Values.volume.controllerSpool.name | trim | include "mashedkebab" -}}
{{- end }}

{{/* Controller spool PVC name */}}
{{- define "slurm-cluster-storage.volume.controller-spool.pvc" -}}
    {{- cat (include "slurm-cluster-storage.volume.controller-spool.name" .) "pvc" | include "mashedkebab" | quote -}}
{{- end }}

{{/* Controller spool PV name */}}
{{- define "slurm-cluster-storage.volume.controller-spool.pv" -}}
    {{- cat (include "slurm-cluster-storage.volume.controller-spool.name" .) "pv" | include "mashedkebab" | quote -}}
{{- end }}

{{/* Controller spool mount name */}}
{{- define "slurm-cluster-storage.volume.controller-spool.mount" -}}
    {{- cat (include "slurm-cluster-storage.volume.controller-spool.name" .) "mount" | include "mashedkebab" | quote -}}
{{- end }}

{{/* Controller spool storage class name */}}
{{- define "slurm-cluster-storage.volume.controller-spool.storageClass" -}}
    {{- include "slurm-cluster-storage.class.local.name" . -}}
{{- end }}

{{/* Controller spool size */}}
{{- define "slurm-cluster-storage.volume.controller-spool.size" -}}
    {{- $sfs := get .Values "sfs" | default dict -}}
    {{- $filesystems := get $sfs "filesystems" | default dict -}}
    {{- $filesystem := get $filesystems "controller-spool" | default dict -}}
    {{- $size := coalesce .Values.volume.controllerSpool.size (get $filesystem "size_gib") -}}
    {{- if kindIs "float64" $size -}}
        {{- printf "%.0fGi" $size -}}
    {{- else if kindIs "int" $size -}}
        {{- printf "%dGi" $size -}}
    {{- else -}}
        {{- required "Controller spool volume size is required." $size -}}
    {{- end -}}
{{- end }}

{{/* Controller spool device name */}}
{{- define "slurm-cluster-storage.volume.controller-spool.device" -}}
    {{- $sfs := get .Values "sfs" | default dict -}}
    {{- $filesystems := get $sfs "filesystems" | default dict -}}
    {{- $filesystem := get $filesystems "controller-spool" | default dict -}}
    {{- coalesce .Values.volume.controllerSpool.filestoreDeviceName (get $filesystem "mount_tag") | required "Controller spool Filestore device name is required." | trim | include "mashedkebab" -}}
{{- end }}

{{/* Controller spool storage type */}}
{{- define "slurm-cluster-storage.volume.controller-spool.type" -}}
    {{- $type := default "filestore" .Values.volume.controllerSpool.type -}}
    {{- if not (or (eq $type "filestore") (eq $type "local")) -}}
        {{- fail "Controller spool volume type must be one of 'filestore' or 'local'." -}}
    {{- end }}
    {{- $type | trim -}}
{{- end }}

{{/* Controller spool local path */}}
{{- define "slurm-cluster-storage.volume.controller-spool.path" -}}
    {{- default "/mnt/controller-spool" .Values.volume.controllerSpool.localPath | trim -}}
{{- end }}

{{/*
---
*/}}

{{/* Accounting database volume */}}
{{- define "slurm-cluster-storage.volume.accounting.name" -}}
    {{- required "Accounting volume name is required." .Values.volume.accounting.name | trim | include "mashedkebab" -}}
{{- end }}

{{/* Accounting database  PV name */}}
{{- define "slurm-cluster-storage.volume.accounting.pv" -}}
    {{- cat (include "slurm-cluster-storage.volume.accounting.name" .) "pv" | include "mashedkebab" | quote -}}
{{- end }}

{{/* Accounting database  mount name */}}
{{- define "slurm-cluster-storage.volume.accounting.mount" -}}
    {{- cat (include "slurm-cluster-storage.volume.accounting.name" .) "mount" | include "mashedkebab" | quote -}}
{{- end }}

{{/* Accounting database  storage class name */}}
{{- define "slurm-cluster-storage.volume.accounting.storageClass" -}}
    {{- include "slurm-cluster-storage.class.local.name" . -}}
{{- end }}

{{/* Accounting database  size */}}
{{- define "slurm-cluster-storage.volume.accounting.size" -}}
    {{- $sfs := get .Values "sfs" | default dict -}}
    {{- $filesystems := get $sfs "filesystems" | default dict -}}
    {{- $filesystem := get $filesystems "accounting" | default dict -}}
    {{- $size := coalesce .Values.volume.accounting.size (get $filesystem "size_gib") -}}
    {{- if kindIs "float64" $size -}}
        {{- printf "%.0fGi" $size -}}
    {{- else if kindIs "int" $size -}}
        {{- printf "%dGi" $size -}}
    {{- else -}}
        {{- required "Accounting volume size is required." $size -}}
    {{- end -}}
{{- end }}

{{/* Accounting database  device name */}}
{{- define "slurm-cluster-storage.volume.accounting.device" -}}
    {{- $sfs := get .Values "sfs" | default dict -}}
    {{- $filesystems := get $sfs "filesystems" | default dict -}}
    {{- $filesystem := get $filesystems "accounting" | default dict -}}
    {{- coalesce .Values.volume.accounting.filestoreDeviceName (get $filesystem "mount_tag") | required "Accounting Filestore device name is required." | trim | include "mashedkebab" -}}
{{- end }}

{{/* Accounting database storage type */}}
{{- define "slurm-cluster-storage.volume.accounting.type" -}}
    {{- $type := default "filestore" .Values.volume.accounting.type -}}
    {{- if not (or (eq $type "filestore") (eq $type "local")) -}}
        {{- fail "Accounting volume type must be one of 'filestore' or 'local'." -}}
    {{- end }}
    {{- $type | trim -}}
{{- end }}

{{/* Accounting database local path */}}
{{- define "slurm-cluster-storage.volume.accounting.path" -}}
    {{- default "/mnt/accounting" .Values.volume.accounting.localPath | trim -}}
{{- end }}

{{/*
---
*/}}

{{/* Jail submount volume */}}
{{- define "slurm-cluster-storage.volume.jail-submount.name" -}}
    {{- cat "jail-submount" (required "Jail submount name is required." .name) | trim | include "mashedkebab" -}}
{{- end }}

{{/* Jail submount PVC name */}}
{{- define "slurm-cluster-storage.volume.jail-submount.pvc" -}}
  {{- cat (include "slurm-cluster-storage.volume.jail-submount.name" .) "pvc" | include "mashedkebab" | quote -}}
{{- end }}

{{/* Jail submount PV name */}}
{{- define "slurm-cluster-storage.volume.jail-submount.pv" -}}
    {{- cat (include "slurm-cluster-storage.volume.jail-submount.name" .) "pv" | include "mashedkebab" | quote -}}
{{- end }}

{{/* Jail submount mount name */}}
{{- define "slurm-cluster-storage.volume.jail-submount.mount" -}}
    {{- cat (include "slurm-cluster-storage.volume.jail-submount.name" .) "mount" | include "mashedkebab" | quote -}}
{{- end }}

{{/* Jail submount storage class name */}}
{{- define "slurm-cluster-storage.volume.jail-submount.storageClass" -}}
    {{- include "slurm-cluster-storage.class.local.name" . -}}
{{- end }}

{{/* Jail submount size */}}
{{- define "slurm-cluster-storage.volume.jail-submount.size" -}}
    {{- required "Jail submount volume size is required." .size -}}
{{- end }}

{{/* Jail submount device name */}}
{{- define "slurm-cluster-storage.volume.jail-submount.device" -}}
    {{- required "Jail submount Filestore device name is required." .filestoreDeviceName | trim | include "mashedkebab" -}}
{{- end }}
