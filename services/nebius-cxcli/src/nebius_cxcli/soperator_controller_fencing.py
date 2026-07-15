"""Fail-closed runtime fencing contracts for Soperator controller handoffs."""

from __future__ import annotations

import hashlib
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

CONTROLLER_FENCE_SCHEMA = "nebius-cxcli-controller-runtime-fence/v1"
CONTROLLER_CENSUS_SCHEMA = "nebius-cxcli-controller-runtime-census/v1"
CONTROLLER_AUTHORITY_LEASE = "cxcli-slurm-controller-authority"
CONTROLLER_FENCE_LABEL = "nebius.ai/cxcli-controller-runtime-fence"
CONTROLLER_CENSUS_LABEL = "nebius.ai/cxcli-controller-runtime-census"
CONTROLLER_INSPECTOR_NAMESPACE = "cxcli-soperator-upgrade-inspectors"
CONTROLLER_INSPECTOR_LABEL = "nebius.ai/cxcli-controller-inspector"

_DIGEST_IMAGE = re.compile(r"^[^\s@]+(?:/[^\s@]+)*@sha256:[0-9a-f]{64}$")
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")
_CONTAINER_ID = re.compile(r"^[a-z0-9][a-z0-9.+_-]*://([0-9a-f]{32,128})$")


def _required_text(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must be non-empty.")
    return text


def _safe_label(value: object, *, field: str) -> str:
    text = _required_text(value, field=field)
    if not _DNS_LABEL.fullmatch(text):
        raise ValueError(f"{field} must be a Kubernetes DNS label.")
    return text


def _sha256(value: object, *, field: str) -> str:
    text = _required_text(value, field=field)
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise ValueError(f"{field} must be a lowercase SHA-256 value.")
    return text


@dataclass(frozen=True)
class ControllerFenceTarget:
    node_name: str
    node_uid: str
    state_markers: tuple[str, ...]

    def __post_init__(self) -> None:
        _required_text(self.node_name, field="controller fence node_name")
        _required_text(self.node_uid, field="controller fence node_uid")
        if not self.state_markers or any(
            not str(marker or "").strip()
            or str(marker) != str(marker).strip()
            or re.search(r"[\s\\]", str(marker)) is not None
            for marker in self.state_markers
        ):
            raise ValueError(
                "controller fence state_markers must be non-empty unescaped mountinfo tokens."
            )


@dataclass(frozen=True)
class ControllerProcessBinding:
    pod_namespace: str
    pod_name: str
    pod_uid: str
    container_name: str
    container_id: str
    image_id: str

    def __post_init__(self) -> None:
        for field in (
            "pod_namespace",
            "pod_name",
            "pod_uid",
            "container_name",
            "image_id",
        ):
            value = _required_text(getattr(self, field), field=f"controller process {field}")
            if re.search(r"[\r\n]", value):
                raise ValueError(f"controller process {field} must be one line.")
        if not _CONTAINER_ID.fullmatch(str(self.container_id or "")):
            raise ValueError(
                "controller process container_id must be an exact CRI runtime identifier."
            )

    @property
    def runtime_id(self) -> str:
        match = _CONTAINER_ID.fullmatch(self.container_id)
        if match is None:  # pragma: no cover - dataclass validation is authoritative
            raise AssertionError
        return match.group(1)

    def as_payload(self) -> dict[str, str]:
        return {
            "pod_namespace": self.pod_namespace,
            "pod_name": self.pod_name,
            "pod_uid": self.pod_uid,
            "container_name": self.container_name,
            "container_id": self.container_id,
            "image_id": self.image_id,
        }


@dataclass(frozen=True)
class ControllerRuntimeCensusTarget:
    node_name: str
    node_uid: str
    node_resource_version: str
    provider_id: str
    system_uuid: str
    expected_processes: tuple[ControllerProcessBinding, ...] = ()

    def __post_init__(self) -> None:
        for field in (
            "node_name",
            "node_uid",
            "node_resource_version",
            "provider_id",
            "system_uuid",
        ):
            value = _required_text(getattr(self, field), field=f"controller census {field}")
            if re.search(r"[\r\n]", value):
                raise ValueError(f"controller census {field} must be one line.")
        runtime_ids = [binding.runtime_id for binding in self.expected_processes]
        if len(runtime_ids) != len(set(runtime_ids)):
            raise ValueError("controller census expected container runtime IDs must be distinct.")

    @property
    def expected_bindings_sha256(self) -> str:
        payloads = sorted(
            "\0".join(
                (
                    binding.pod_namespace,
                    binding.pod_name,
                    binding.pod_uid,
                    binding.container_name,
                    binding.container_id,
                    binding.image_id,
                )
            )
            for binding in self.expected_processes
        )
        return hashlib.sha256("\n".join(payloads).encode()).hexdigest()

    @property
    def node_identity_sha256(self) -> str:
        return hashlib.sha256(
            "\0".join((self.node_uid, self.provider_id, self.system_uuid)).encode()
        ).hexdigest()


@dataclass(frozen=True)
class ControllerRuntimeCensusEvidence:
    node_name: str
    node_uid: str
    expected_process_count: int
    slurmctld_count: int
    matched_expected_process_count: int
    unexpected_process_count: int
    missing_expected_process_count: int
    ambiguous_process_count: int
    inspected_process_count: int
    unreadable_process_count: int
    expected_bindings_sha256: str

    @property
    def exclusive(self) -> bool:
        return (
            self.slurmctld_count == self.expected_process_count
            and self.matched_expected_process_count == self.expected_process_count
            and self.unexpected_process_count == 0
            and self.missing_expected_process_count == 0
            and self.ambiguous_process_count == 0
            and self.inspected_process_count > 0
            and self.unreadable_process_count == 0
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "schema": CONTROLLER_CENSUS_SCHEMA,
            "node_name": self.node_name,
            "node_uid": self.node_uid,
            "expected_process_count": self.expected_process_count,
            "slurmctld_count": self.slurmctld_count,
            "matched_expected_process_count": self.matched_expected_process_count,
            "unexpected_process_count": self.unexpected_process_count,
            "missing_expected_process_count": self.missing_expected_process_count,
            "ambiguous_process_count": self.ambiguous_process_count,
            "inspected_process_count": self.inspected_process_count,
            "unreadable_process_count": self.unreadable_process_count,
            "expected_bindings_sha256": self.expected_bindings_sha256,
            "exclusive": self.exclusive,
        }


@dataclass(frozen=True)
class ControllerRuntimeFenceEvidence:
    node_name: str
    node_uid: str
    slurmctld_count: int
    writable_state_mount_count: int
    inspected_process_count: int
    unreadable_process_count: int
    marker_sha256: str

    @property
    def fenced(self) -> bool:
        return (
            self.slurmctld_count == 0
            and self.writable_state_mount_count == 0
            and self.inspected_process_count > 0
            and self.unreadable_process_count == 0
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "schema": CONTROLLER_FENCE_SCHEMA,
            "node_name": self.node_name,
            "node_uid": self.node_uid,
            "slurmctld_count": self.slurmctld_count,
            "writable_state_mount_count": self.writable_state_mount_count,
            "inspected_process_count": self.inspected_process_count,
            "unreadable_process_count": self.unreadable_process_count,
            "marker_sha256": self.marker_sha256,
            "fenced": self.fenced,
        }


def controller_fence_marker_sha256(markers: Sequence[str]) -> str:
    normalized = tuple(sorted({_required_text(marker, field="state marker") for marker in markers}))
    if not normalized:
        raise ValueError("controller fence requires at least one state marker.")
    return hashlib.sha256("\0".join(normalized).encode()).hexdigest()


def _controller_runtime_census_script(
    target: ControllerRuntimeCensusTarget,
    *,
    proc_root: str = "/proc",
) -> str:
    """Return the host-PID census script; proc_root is injectable only for unit tests."""

    if not proc_root.startswith("/") or re.search(r"[\s\r\n]", proc_root):
        raise ValueError("controller census proc_root must be an absolute whitespace-free path.")
    expected_ids = " ".join(binding.runtime_id for binding in target.expected_processes)
    return f"""
set -eu
schema={shlex.quote(CONTROLLER_CENSUS_SCHEMA)}
node_name={shlex.quote(target.node_name)}
node_uid={shlex.quote(target.node_uid)}
expected_bindings_sha256={shlex.quote(target.expected_bindings_sha256)}
expected_process_count={len(target.expected_processes)}
expected_container_ids={shlex.quote(expected_ids)}
proc_root={shlex.quote(proc_root)}
slurmctld_count=0
matched_expected_process_count=0
unexpected_process_count=0
missing_expected_process_count=0
ambiguous_process_count=0
inspected_process_count=0
unreadable_process_count=0
seen_container_ids=""
for process in "$proc_root"/[0-9]*; do
  test -d "$process" || continue
  if ! comm=$(cat "$process/comm" 2>/dev/null); then
    if test -d "$process"; then
      inspected_process_count=$((inspected_process_count + 1))
      unreadable_process_count=$((unreadable_process_count + 1))
    fi
    continue
  fi
  test -d "$process" || continue
  inspected_process_count=$((inspected_process_count + 1))
  exe=""
  case "$comm" in
    slurmctld) ;;
    *)
      if exe=$(readlink "$process/exe" 2>/dev/null); then
        :
      elif ! test -d "$process"; then
        continue
      else
        cmdline=$(tr '\\000' ' ' <"$process/cmdline" 2>/dev/null || true)
        if ! test -d "$process"; then
          continue
        fi
        if test -n "$cmdline"; then
          unreadable_process_count=$((unreadable_process_count + 1))
        fi
      fi
      ;;
  esac
  exe_base=${{exe##*/}}
  case "$comm:$exe_base" in
    slurmctld:*|*:slurmctld|*:slurmctld\\ \\(deleted\\)) ;;
    *) continue ;;
  esac
  scriptd_child=0
  if test "$comm:$exe_base" = "slurmscriptd:slurmctld"; then
    if ! cmdline=$(tr '\\000' ' ' <"$process/cmdline" 2>/dev/null); then
      if ! test -d "$process"; then
        continue
      fi
      unreadable_process_count=$((unreadable_process_count + 1))
      continue
    fi
    test -d "$process" || continue
    if test "$cmdline" = "slurmctld: slurmscriptd "; then
      scriptd_child=1
    fi
  fi
  if ! cgroup=$(cat "$process/cgroup" 2>/dev/null); then
    if ! test -d "$process"; then
      continue
    fi
    slurmctld_count=$((slurmctld_count + 1))
    unreadable_process_count=$((unreadable_process_count + 1))
    unexpected_process_count=$((unexpected_process_count + 1))
    continue
  fi
  test -d "$process" || continue
  if test "$scriptd_child" -eq 1; then
    parent_pid=""
    if status=$(cat "$process/status" 2>/dev/null); then
      while read -r status_key status_value ignored_status_fields; do
        case "$status_key" in
          PPid:) parent_pid=$status_value ;;
        esac
      done <<CXCLI_STATUS_EOF
$status
CXCLI_STATUS_EOF
    fi
    parent_process="$proc_root/$parent_pid"
    if test -n "$parent_pid" && test -d "$parent_process"; then
      parent_comm=$(cat "$parent_process/comm" 2>/dev/null || true)
      parent_cgroup=$(cat "$parent_process/cgroup" 2>/dev/null || true)
      if test "$parent_comm" = "slurmctld" && test "$parent_cgroup" = "$cgroup"; then
        continue
      fi
    fi
  fi
  slurmctld_count=$((slurmctld_count + 1))
  match_count=0
  matched_id=""
  for expected_id in $expected_container_ids; do
    if printf '%s\n' "$cgroup" | grep -Eq "(^|[^0-9a-f])${{expected_id}}([^0-9a-f]|$)"; then
      match_count=$((match_count + 1))
      matched_id="$expected_id"
    fi
  done
  case "$match_count" in
    0)
      unexpected_process_count=$((unexpected_process_count + 1))
      ;;
    1)
      case " $seen_container_ids " in
        *" $matched_id "*) ambiguous_process_count=$((ambiguous_process_count + 1)) ;;
        *)
          seen_container_ids="$seen_container_ids $matched_id"
          matched_expected_process_count=$((matched_expected_process_count + 1))
          ;;
      esac
      ;;
    *)
      ambiguous_process_count=$((ambiguous_process_count + 1))
      ;;
  esac
done
for expected_id in $expected_container_ids; do
  case " $seen_container_ids " in
    *" $expected_id "*) ;;
    *) missing_expected_process_count=$((missing_expected_process_count + 1)) ;;
  esac
done
printf 'schema=%s\nnode_name=%s\nnode_uid=%s\nexpected_process_count=%s\nslurmctld_count=%s\nmatched_expected_process_count=%s\nunexpected_process_count=%s\nmissing_expected_process_count=%s\nambiguous_process_count=%s\ninspected_process_count=%s\nunreadable_process_count=%s\nexpected_bindings_sha256=%s\n' \
  "$schema" "$node_name" "$node_uid" "$expected_process_count" \
  "$slurmctld_count" "$matched_expected_process_count" \
  "$unexpected_process_count" "$missing_expected_process_count" \
  "$ambiguous_process_count" "$inspected_process_count" \
  "$unreadable_process_count" "$expected_bindings_sha256"
test "$slurmctld_count" -eq "$expected_process_count"
test "$matched_expected_process_count" -eq "$expected_process_count"
test "$unexpected_process_count" -eq 0
test "$missing_expected_process_count" -eq 0
test "$ambiguous_process_count" -eq 0
test "$inspected_process_count" -gt 0
test "$unreadable_process_count" -eq 0
""".strip()


def controller_runtime_census_pod(
    *,
    campaign_fingerprint: str,
    purpose: str,
    attempt_id: str,
    image: str,
    target: ControllerRuntimeCensusTarget,
) -> dict[str, Any]:
    """Build a fresh host-PID process census bound to exact CRI and node identities."""

    campaign = _sha256(campaign_fingerprint, field="controller census campaign_fingerprint")
    purpose = _safe_label(purpose, field="controller census purpose")
    if not re.fullmatch(r"[0-9a-f]{32}", str(attempt_id or "")):
        raise ValueError("controller census attempt_id must be a 32-character lowercase hex value.")
    if not _DIGEST_IMAGE.fullmatch(str(image or "")):
        raise ValueError("controller census image must be an immutable repository@sha256 digest.")
    suffix = hashlib.sha256(f"{target.node_uid}\0{purpose}\0{attempt_id}".encode()).hexdigest()[:16]
    labels = {
        "app.kubernetes.io/managed-by": "nebius-cxcli",
        CONTROLLER_CENSUS_LABEL: "true",
    }
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "namespace": CONTROLLER_INSPECTOR_NAMESPACE,
            "name": f"cxcli-controller-census-{suffix}",
            "labels": labels,
            "annotations": {
                "nebius.ai/cxcli-campaign-fingerprint": campaign,
                "nebius.ai/cxcli-census-purpose": purpose,
                "nebius.ai/cxcli-census-attempt": attempt_id,
                "nebius.ai/cxcli-node-identity-sha256": target.node_identity_sha256,
                "nebius.ai/cxcli-expected-bindings-sha256": (target.expected_bindings_sha256),
            },
        },
        "spec": {
            "nodeName": target.node_name,
            "hostPID": True,
            "automountServiceAccountToken": False,
            "restartPolicy": "Never",
            "activeDeadlineSeconds": 300,
            "tolerations": [{"operator": "Exists"}],
            "containers": [
                {
                    "name": "inspector",
                    "image": image,
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["/bin/sh", "-ec", _controller_runtime_census_script(target)],
                    "securityContext": {
                        # Host PID visibility alone cannot read every stable
                        # userspace /proc entry from the container user
                        # namespace. The dedicated, network-denied inspector
                        # namespace is the narrow privilege boundary for this
                        # fail-closed node-level proof.
                        "privileged": True,
                        "readOnlyRootFilesystem": True,
                        "runAsNonRoot": False,
                        "runAsUser": 0,
                    },
                    "resources": {
                        "requests": {"cpu": "5m", "memory": "16Mi"},
                        "limits": {"cpu": "100m", "memory": "64Mi"},
                    },
                }
            ],
        },
    }


def parse_controller_runtime_census_evidence(
    output: str,
    *,
    target: ControllerRuntimeCensusTarget,
) -> ControllerRuntimeCensusEvidence:
    values: dict[str, str] = {}
    for line in str(output or "").splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in values:
            raise ValueError("controller runtime census output is malformed or duplicated.")
        values[key] = value
    count_fields = {
        "expected_process_count",
        "slurmctld_count",
        "matched_expected_process_count",
        "unexpected_process_count",
        "missing_expected_process_count",
        "ambiguous_process_count",
        "inspected_process_count",
        "unreadable_process_count",
    }
    required = {
        "schema",
        "node_name",
        "node_uid",
        "expected_bindings_sha256",
        *count_fields,
    }
    if set(values) != required or values["schema"] != CONTROLLER_CENSUS_SCHEMA:
        raise ValueError("controller runtime census output has an invalid schema or field set.")
    if values["node_name"] != target.node_name or values["node_uid"] != target.node_uid:
        raise ValueError("controller runtime census changed the immutable node identity.")
    if values["expected_bindings_sha256"] != target.expected_bindings_sha256:
        raise ValueError("controller runtime census changed the expected process bindings.")

    def count(field: str) -> int:
        value = values[field]
        if not re.fullmatch(r"0|[1-9][0-9]*", value):
            raise ValueError(f"controller runtime census {field} must be non-negative.")
        return int(value)

    evidence = ControllerRuntimeCensusEvidence(
        node_name=target.node_name,
        node_uid=target.node_uid,
        expected_process_count=count("expected_process_count"),
        slurmctld_count=count("slurmctld_count"),
        matched_expected_process_count=count("matched_expected_process_count"),
        unexpected_process_count=count("unexpected_process_count"),
        missing_expected_process_count=count("missing_expected_process_count"),
        ambiguous_process_count=count("ambiguous_process_count"),
        inspected_process_count=count("inspected_process_count"),
        unreadable_process_count=count("unreadable_process_count"),
        expected_bindings_sha256=values["expected_bindings_sha256"],
    )
    if evidence.expected_process_count != len(target.expected_processes) or not evidence.exclusive:
        raise ValueError(
            "controller runtime census did not prove exact process/container exclusivity."
        )
    return evidence


def _controller_runtime_fence_script(
    target: ControllerFenceTarget,
    *,
    proc_root: str = "/proc",
) -> str:
    """Return the host-PID fence script; proc_root is injectable only for unit tests."""

    if not proc_root.startswith("/") or re.search(r"[\s\r\n]", proc_root):
        raise ValueError("controller fence proc_root must be an absolute whitespace-free path.")
    marker_digest = controller_fence_marker_sha256(target.state_markers)
    marker_cases = " ".join(shlex.quote(marker) for marker in sorted(set(target.state_markers)))
    return f"""
set -eu
schema={shlex.quote(CONTROLLER_FENCE_SCHEMA)}
expected_node={shlex.quote(target.node_name)}
expected_uid={shlex.quote(target.node_uid)}
marker_sha256={shlex.quote(marker_digest)}
proc_root={shlex.quote(proc_root)}
slurmctld_count=0
writable_state_mount_count=0
inspected_process_count=0
unreadable_process_count=0
for process in "$proc_root"/[0-9]*; do
  test -d "$process" || continue
  if ! comm=$(cat "$process/comm" 2>/dev/null); then
    if test -d "$process"; then
      inspected_process_count=$((inspected_process_count + 1))
      unreadable_process_count=$((unreadable_process_count + 1))
    fi
    continue
  fi
  test -d "$process" || continue
  inspected_process_count=$((inspected_process_count + 1))
  exe=""
  case "$comm" in
    slurmctld) ;;
    *)
      if exe=$(readlink "$process/exe" 2>/dev/null); then
        :
      elif ! test -d "$process"; then
        continue
      else
        cmdline=$(tr '\\000' ' ' <"$process/cmdline" 2>/dev/null || true)
        if ! test -d "$process"; then
          continue
        fi
        if test -n "$cmdline"; then
          unreadable_process_count=$((unreadable_process_count + 1))
        fi
      fi
      ;;
  esac
  exe_base=${{exe##*/}}
  case "$comm:$exe_base" in
    slurmctld:*|*:slurmctld|*:slurmctld\\ \\(deleted\\))
      slurmctld_count=$((slurmctld_count + 1))
      ;;
  esac
  if ! mountinfo=$(cat "$process/mountinfo" 2>/dev/null); then
    if test -d "$process"; then
      unreadable_process_count=$((unreadable_process_count + 1))
    fi
    continue
  fi
  while IFS= read -r mount_line; do
    test -n "$mount_line" || continue
    case "$mount_line" in
      *" - "*) ;;
      *) unreadable_process_count=$((unreadable_process_count + 1)); continue ;;
    esac
    # mountinfo field 6 is the per-mount option set.  Parse it in the shell:
    # spawning one awk process for every mount of every host PID makes the
    # fail-closed census take minutes on an otherwise small controller node.
    mount_options=$mount_line
    mountinfo_fields_valid=1
    for ignored_mountinfo_field in 1 2 3 4 5; do
      case "$mount_options" in
        *" "*) mount_options=${{mount_options#* }} ;;
        *) mountinfo_fields_valid=0; break ;;
      esac
    done
    if test "$mountinfo_fields_valid" -ne 1; then
      unreadable_process_count=$((unreadable_process_count + 1))
      continue
    fi
    mount_options=${{mount_options%% *}}
    case ",$mount_options," in
      *,rw,*) ;;
      *) continue ;;
    esac
    for marker in {marker_cases}; do
      case "$mount_line" in
        *"$marker"*) writable_state_mount_count=$((writable_state_mount_count + 1)); break ;;
      esac
    done
  done <<CXCLI_MOUNTINFO_EOF
$mountinfo
CXCLI_MOUNTINFO_EOF
done
printf 'schema=%s\nnode_name=%s\nnode_uid=%s\nslurmctld_count=%s\nwritable_state_mount_count=%s\ninspected_process_count=%s\nunreadable_process_count=%s\nmarker_sha256=%s\n' \
  "$schema" "$expected_node" "$expected_uid" "$slurmctld_count" \
  "$writable_state_mount_count" "$inspected_process_count" \
  "$unreadable_process_count" "$marker_sha256"
test "$slurmctld_count" -eq 0
test "$writable_state_mount_count" -eq 0
test "$inspected_process_count" -gt 0
test "$unreadable_process_count" -eq 0
""".strip()


def controller_runtime_fence_pod(
    *,
    campaign_fingerprint: str,
    authority_epoch: str,
    image: str,
    target: ControllerFenceTarget,
) -> dict[str, Any]:
    """Build a short-lived privileged host-PID inspector without host mounts."""

    campaign = _sha256(campaign_fingerprint, field="controller fence campaign_fingerprint")
    epoch = _safe_label(authority_epoch, field="controller fence authority_epoch")
    if not _DIGEST_IMAGE.fullmatch(str(image or "")):
        raise ValueError("controller fence image must be an immutable repository@sha256 digest.")
    marker_digest = controller_fence_marker_sha256(target.state_markers)
    suffix = hashlib.sha256(f"{target.node_uid}\0{epoch}".encode()).hexdigest()[:12]
    name = f"cxcli-controller-fence-{suffix}"
    script = (
        _controller_runtime_fence_script(target)
        + "\ntouch /dev/shm/cxcli-fence-verified\n"
        + "trap 'exit 0' TERM INT\n"
        + "sleep 600"
    )
    labels = {
        "app.kubernetes.io/managed-by": "nebius-cxcli",
        "nebius.ai/cxcli-controller-bridge": "fence",
        CONTROLLER_FENCE_LABEL: "true",
    }
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "namespace": CONTROLLER_INSPECTOR_NAMESPACE,
            "name": name,
            "labels": labels,
            "annotations": {
                "nebius.ai/cxcli-campaign-fingerprint": campaign,
                "nebius.ai/cxcli-authority-epoch": epoch,
                "nebius.ai/cxcli-node-uid": target.node_uid,
                "nebius.ai/cxcli-state-marker-sha256": marker_digest,
            },
        },
        "spec": {
            "nodeName": target.node_name,
            "hostPID": True,
            "automountServiceAccountToken": False,
            "restartPolicy": "Never",
            "tolerations": [{"operator": "Exists"}],
            "containers": [
                {
                    "name": "inspector",
                    "image": image,
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["/bin/sh", "-ec", script],
                    "readinessProbe": {
                        "exec": {"command": ["test", "-f", "/dev/shm/cxcli-fence-verified"]},
                        "periodSeconds": 1,
                        "failureThreshold": 300,
                    },
                    "securityContext": {
                        # Host PID visibility alone cannot read every stable
                        # userspace /proc entry from the container user
                        # namespace.  The dedicated, network-denied inspector
                        # namespace is the narrow privilege boundary for this
                        # fail-closed node-level proof.
                        "privileged": True,
                        "readOnlyRootFilesystem": True,
                        "runAsNonRoot": False,
                        "runAsUser": 0,
                    },
                    "resources": {
                        "requests": {"cpu": "5m", "memory": "16Mi"},
                        "limits": {"cpu": "100m", "memory": "64Mi"},
                    },
                }
            ],
        },
    }


def controller_inspector_namespace_objects(
    *,
    campaign_fingerprint: str,
    cluster_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the dedicated privileged-inspector namespace and its deny-all policy."""

    campaign = _sha256(
        campaign_fingerprint,
        field="controller inspector campaign_fingerprint",
    )
    cluster = _required_text(cluster_id, field="controller inspector cluster_id")
    if re.search(r"[\r\n]", cluster):
        raise ValueError("controller inspector cluster_id must be one line.")
    labels = {
        "app.kubernetes.io/managed-by": "nebius-cxcli",
        "nebius.ai/cxcli-controller-bridge": "true",
        CONTROLLER_INSPECTOR_LABEL: "true",
    }
    ownership_annotations = {
        "nebius.ai/cxcli-campaign-fingerprint": campaign,
        "nebius.ai/cxcli-cluster-id": cluster,
    }
    namespace = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": CONTROLLER_INSPECTOR_NAMESPACE,
            "labels": {
                **labels,
                "pod-security.kubernetes.io/enforce": "privileged",
                "pod-security.kubernetes.io/enforce-version": "latest",
                "pod-security.kubernetes.io/audit": "restricted",
                "pod-security.kubernetes.io/audit-version": "latest",
                "pod-security.kubernetes.io/warn": "restricted",
                "pod-security.kubernetes.io/warn-version": "latest",
            },
            "annotations": {
                **ownership_annotations,
                "nebius.ai/cxcli-pod-security-contract": ("host-pid-inspectors-only:privileged"),
            },
        },
    }
    default_deny = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "namespace": CONTROLLER_INSPECTOR_NAMESPACE,
            "name": "cxcli-controller-inspectors-default-deny",
            "labels": labels,
            "annotations": ownership_annotations,
        },
        "spec": {
            "podSelector": {},
            "policyTypes": ["Ingress", "Egress"],
        },
    }
    return namespace, default_deny


def parse_controller_runtime_fence_evidence(
    output: str,
    *,
    target: ControllerFenceTarget,
) -> ControllerRuntimeFenceEvidence:
    values: dict[str, str] = {}
    for line in str(output or "").splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in values:
            raise ValueError("controller runtime fence output is malformed or duplicated.")
        values[key] = value
    required = {
        "schema",
        "node_name",
        "node_uid",
        "slurmctld_count",
        "writable_state_mount_count",
        "inspected_process_count",
        "unreadable_process_count",
        "marker_sha256",
    }
    if set(values) != required or values["schema"] != CONTROLLER_FENCE_SCHEMA:
        raise ValueError("controller runtime fence output has an invalid schema or field set.")
    if values["node_name"] != target.node_name or values["node_uid"] != target.node_uid:
        raise ValueError("controller runtime fence output changed the immutable node identity.")
    if values["marker_sha256"] != controller_fence_marker_sha256(target.state_markers):
        raise ValueError("controller runtime fence output changed the state-marker binding.")

    def count(field: str) -> int:
        value = values[field]
        if not re.fullmatch(r"0|[1-9][0-9]*", value):
            raise ValueError(f"controller runtime fence {field} must be a non-negative integer.")
        return int(value)

    evidence = ControllerRuntimeFenceEvidence(
        node_name=target.node_name,
        node_uid=target.node_uid,
        slurmctld_count=count("slurmctld_count"),
        writable_state_mount_count=count("writable_state_mount_count"),
        inspected_process_count=count("inspected_process_count"),
        unreadable_process_count=count("unreadable_process_count"),
        marker_sha256=values["marker_sha256"],
    )
    if not evidence.fenced:
        raise ValueError(
            "controller runtime fence did not prove process and writable-mount absence."
        )
    return evidence


def controller_authority_lease(
    *,
    namespace: str,
    campaign_fingerprint: str,
    cluster_id: str,
    authority_epoch: str,
    owner: str,
) -> dict[str, Any]:
    namespace = _safe_label(namespace, field="controller authority namespace")
    campaign = _sha256(campaign_fingerprint, field="controller authority campaign_fingerprint")
    cluster = _required_text(cluster_id, field="controller authority cluster_id")
    if re.search(r"[\r\n]", cluster):
        raise ValueError("controller authority cluster_id must be one line.")
    epoch = _safe_label(authority_epoch, field="controller authority epoch")
    owner = _safe_label(owner, field="controller authority owner")
    return {
        "apiVersion": "coordination.k8s.io/v1",
        "kind": "Lease",
        "metadata": {
            "namespace": namespace,
            "name": CONTROLLER_AUTHORITY_LEASE,
            "labels": {
                "app.kubernetes.io/managed-by": "nebius-cxcli",
                "nebius.ai/cxcli-controller-bridge": "true",
                CONTROLLER_FENCE_LABEL: "authority",
            },
            "annotations": {
                "nebius.ai/cxcli-campaign-fingerprint": campaign,
                "nebius.ai/cxcli-cluster-id": cluster,
                "nebius.ai/cxcli-authority-epoch": epoch,
            },
        },
        "spec": {
            "holderIdentity": f"{epoch}:{owner}",
            "leaseDurationSeconds": 86400,
        },
    }


def validate_controller_authority_lease(
    lease: Mapping[str, Any],
    *,
    namespace: str,
    campaign_fingerprint: str,
    cluster_id: str,
    authority_epoch: str,
    owner: str,
) -> dict[str, str]:
    expected = controller_authority_lease(
        namespace=namespace,
        campaign_fingerprint=campaign_fingerprint,
        cluster_id=cluster_id,
        authority_epoch=authority_epoch,
        owner=owner,
    )
    metadata = lease.get("metadata")
    spec = lease.get("spec")
    if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
        raise ValueError("controller authority Lease must contain metadata and spec mappings.")
    expected_metadata = expected["metadata"]
    if not isinstance(expected_metadata, Mapping):  # pragma: no cover - static construction
        raise AssertionError
    observed_annotations = {
        str(key): str(value)
        for key, value in dict(metadata.get("annotations", {})).items()
        if str(key) != "kubectl.kubernetes.io/last-applied-configuration"
    }
    if (
        str(metadata.get("namespace", "")) != namespace
        or str(metadata.get("name", "")) != CONTROLLER_AUTHORITY_LEASE
        or not str(metadata.get("uid", ""))
        or not str(metadata.get("resourceVersion", ""))
        or dict(metadata.get("labels", {})) != dict(expected_metadata["labels"])
        or observed_annotations != dict(expected_metadata["annotations"])
        or str(spec.get("holderIdentity", "")) != expected["spec"]["holderIdentity"]
    ):
        raise ValueError("controller authority Lease identity or ownership contract drifted.")
    return {
        "uid": str(metadata["uid"]),
        "resource_version": str(metadata["resourceVersion"]),
        "holder_identity": str(spec["holderIdentity"]),
    }
