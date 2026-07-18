"""Pure GPU validation resources and gates for a populated passive Soperator jail."""

from __future__ import annotations

import copy
import hashlib
import re
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .soperator_populate_jail import active_passive_pod_scheduling_fields

GPU_JAIL_POST_POPULATION_SCRIPT_KEY = "post-populate-gpu-jail.sh"
GPU_JAIL_POST_POPULATION_SCRIPT_MOUNT = "/opt/nebius-cxcli"
GPU_JAIL_PASSIVE_ROOTFS_MOUNT = "/mnt/passive-rootfs"
GPU_JAIL_POST_ACTIVATION_SCHEMA = "nebius-cxcli-soperator-jail-gpu-post-activation/v1"
GPU_JAIL_POST_ACTIVATION_HEALTHY_IDLE_STATES = frozenset(
    {"IDLE+CLOUD", "IDLE+DYNAMIC_NORM"}
)

_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")


GPU_JAIL_POST_POPULATION_SCRIPT = (
    textwrap.dedent(
        r"""
        #!/bin/bash
        set -euo pipefail

        readonly jail=/mnt/passive-rootfs
        readonly marker_schema=nebius-cxcli-soperator-jail-gpu-driver/v1
        readonly allow_driver_refresh="${CXCLI_ALLOW_DRIVER_REFRESH:-false}"

        fail() {
          printf 'gpu-jail-post-population: %s\n' "$*" >&2
          exit 1
        }

        require_command() {
          command -v "$1" >/dev/null 2>&1 || fail "required command is missing: $1"
        }

        for command_name in awk cat chmod cp env flock grep ldconfig ln mkdir mktemp mv \
          nvidia-smi readlink rm sed sha256sum sort uname wc; do
          require_command "${command_name}"
        done
        case "${allow_driver_refresh}" in
          true|false) ;;
          *) fail "CXCLI_ALLOW_DRIVER_REFRESH must be exactly true or false" ;;
        esac

        arch="$(uname -m)"
        [ "${arch}" = x86_64 ] \
          || fail "PopulateJail image must run on the locked linux/amd64 platform"

        readonly jail_lib_dir="${jail}/usr/lib/${arch}-linux-gnu"
        readonly jail_bin_dir="${jail}/usr/bin"
        readonly jail_nvidia_smi="${jail}/usr/bin/nvidia-smi"
        readonly marker_dir="${jail}/etc/nebius-cxcli"
        readonly marker="${marker_dir}/gpu-driver-jail.env"

        [ -d "${jail}/usr" ] || fail "passive rootfs is not populated"
        runtime_nvidia_smi_source="$(readlink -f -- "$(command -v nvidia-smi)")" \
          || fail "runtime-injected nvidia-smi is missing or has a broken link"
        case "${runtime_nvidia_smi_source}" in
          "${jail}"/*) fail "runtime nvidia-smi unexpectedly resolves inside passive rootfs" ;;
        esac
        [ -f "${runtime_nvidia_smi_source}" ] && [ -x "${runtime_nvidia_smi_source}" ] \
          || fail "runtime-injected nvidia-smi target is missing or not executable"
        runtime_nvidia_smi_hash="$(sha256sum -- "${runtime_nvidia_smi_source}" | awk '{print $1}')"
        case "${runtime_nvidia_smi_hash}" in
          ''|*[!0-9a-f]*) fail "could not hash runtime-injected nvidia-smi" ;;
        esac
        [ "${#runtime_nvidia_smi_hash}" -eq 64 ] || fail "invalid runtime nvidia-smi hash"
        if [ -e "${jail_nvidia_smi}" ] || [ -L "${jail_nvidia_smi}" ]; then
          jail_nvidia_smi_source="$(readlink -m -- "${jail_nvidia_smi}")" \
            || fail "passive-rootfs nvidia-smi path could not be resolved"
          case "${jail_nvidia_smi_source}" in
            "${jail}"/*) ;;
            *) fail "passive-rootfs nvidia-smi resolves outside the passive rootfs" ;;
          esac
          [ -f "${jail_nvidia_smi_source}" ] \
            || fail "passive-rootfs nvidia-smi is not a regular file"
        fi

        if ! driver_versions="$(env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
          nvidia-smi \
          --query-gpu=driver_version --format=csv,noheader 2>/dev/null \
          | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' \
          | sed '/^$/d' | sort -u)"; then
          fail "host nvidia-smi could not report GPU driver versions"
        fi
        driver_version_count="$(printf '%s\n' "${driver_versions}" | sed '/^$/d' | wc -l)"
        [ "${driver_version_count}" -eq 1 ] \
          || fail "mixed host GPU driver versions are not allowed: ${driver_versions}"
        driver_version="$(printf '%s\n' "${driver_versions}" | sed -n '1p')"
        case "${driver_version}" in
          ''|*[!0-9.]*|.*|*..*|*.) fail "invalid host GPU driver version: ${driver_version}" ;;
        esac

        resolve_source_library() {
          local soname="$1"
          local candidates source base expected_versioned source_hash
          candidates="$(ldconfig -p | awk -v expected="${soname}" \
            '$1 == expected { print $NF }' | sort -u)"
          [ "$(printf '%s\n' "${candidates}" | sed '/^$/d' | wc -l)" -eq 1 ] \
            || fail "runtime must expose exactly one ${soname} provider"
          source="$(readlink -f -- "${candidates}")" \
            || fail "runtime driver library has a broken link: ${soname}"
          case "${source}" in
            "${jail}"/*) fail "runtime driver library resolves inside passive rootfs: ${soname}" ;;
          esac
          [ -f "${source}" ] && [ -s "${source}" ] \
            || fail "runtime driver library target is missing or empty: ${soname}"
          base="${source##*/}"
          expected_versioned="${soname%.1}.${driver_version}"
          case "${base}" in
            "${expected_versioned}") ;;
            *.so.[0-9]*)
              fail "mixed runtime GPU driver libraries: ${soname} resolves to ${base}, expected ${driver_version}"
              ;;
            *) fail "unexpected host driver library target for ${soname}: ${base}" ;;
          esac
          source_hash="$(sha256sum -- "${source}" | awk '{print $1}')"
          case "${source_hash}" in
            ''|*[!0-9a-f]*) fail "could not hash host driver library: ${soname}" ;;
          esac
          [ "${#source_hash}" -eq 64 ] || fail "invalid source hash for ${soname}"
          printf '%s|%s|%s\n' "${source}" "${base}" "${source_hash}"
        }

        IFS='|' read -r cuda_source cuda_base cuda_hash \
          <<<"$(resolve_source_library libcuda.so.1)"
        IFS='|' read -r nvml_source nvml_base nvml_hash \
          <<<"$(resolve_source_library libnvidia-ml.so.1)"

        assert_path_inside_jail() {
          local candidate="$1"
          local resolved
          resolved="$(readlink -m -- "${candidate}")" \
            || fail "could not resolve passive-rootfs path: ${candidate}"
          case "${resolved}" in
            "${jail}"/*) ;;
            *) fail "passive-rootfs path resolves outside the passive rootfs: ${candidate}" ;;
          esac
        }

        assert_path_inside_jail "${jail_lib_dir}"
        assert_path_inside_jail "${jail_bin_dir}"
        assert_path_inside_jail "${marker_dir}"
        mkdir -p -- "${jail_lib_dir}" "${jail_bin_dir}" "${marker_dir}"
        exec 9<"${marker_dir}"
        flock -x 9

        resolve_jail_link() {
          local link="$1"
          local raw candidate resolved
          raw="$(readlink -- "${link}")" || fail "broken passive-rootfs symlink: ${link}"
          case "${raw}" in
            /*) candidate="${jail}${raw}" ;;
            *) candidate="${link%/*}/${raw}" ;;
          esac
          resolved="$(readlink -m -- "${candidate}")" \
            || fail "could not resolve passive-rootfs symlink: ${link}"
          case "${resolved}" in
            "${jail}"/*) ;;
            *) fail "passive-rootfs symlink resolves outside the passive rootfs: ${link}" ;;
          esac
          [ -e "${resolved}" ] || fail "broken passive-rootfs symlink: ${link}"
          printf '%s\n' "${resolved}"
        }

        for soname in libcuda.so libcuda.so.1 libnvidia-ml.so libnvidia-ml.so.1; do
          existing="${jail_lib_dir}/${soname}"
          if [ -L "${existing}" ]; then
            resolve_jail_link "${existing}" >/dev/null
          fi
        done

        marker_lines="$(cat <<EOF
schema=${marker_schema}
arch=${arch}
driver_version=${driver_version}
libcuda_source=${cuda_base}
libcuda_sha256=${cuda_hash}
libnvidia_ml_source=${nvml_base}
libnvidia_ml_sha256=${nvml_hash}
nvidia_smi_sha256=${runtime_nvidia_smi_hash}
EOF
)"
        if [ -e "${marker}" ]; then
          [ -f "${marker}" ] && [ ! -L "${marker}" ] \
            || fail "GPU driver evidence marker is not a regular file"
          if [ "$(cat "${marker}")" != "${marker_lines}" ]; then
            [ "${allow_driver_refresh}" = true ] \
              || fail "existing passive-rootfs GPU driver evidence does not match the host driver"
          fi
        fi

        staging="$(mktemp -d "${jail_lib_dir}/.cxcli-gpu-driver.XXXXXX")"
        nvidia_smi_tmp=""
        marker_tmp=""
        cleanup() {
          rm -rf -- "${staging}"
          if [ -n "${nvidia_smi_tmp}" ]; then
            rm -f -- "${nvidia_smi_tmp}"
          fi
          if [ -n "${marker_tmp}" ]; then
            rm -f -- "${marker_tmp}"
          fi
        }
        trap cleanup EXIT

        stage_library() {
          local source="$1"
          local base="$2"
          local expected_hash="$3"
          local staged="${staging}/${base}"
          cp -- "${source}" "${staged}"
          chmod 0644 -- "${staged}"
          [ "$(sha256sum -- "${staged}" | awk '{print $1}')" = "${expected_hash}" ] \
            || fail "staged GPU driver library hash mismatch: ${base}"
        }

        stage_library "${cuda_source}" "${cuda_base}" "${cuda_hash}"
        stage_library "${nvml_source}" "${nvml_base}" "${nvml_hash}"
        mv -f -- "${staging}/${cuda_base}" "${jail_lib_dir}/${cuda_base}"
        mv -f -- "${staging}/${nvml_base}" "${jail_lib_dir}/${nvml_base}"

        nvidia_smi_tmp="$(mktemp "${jail_bin_dir}/.cxcli-nvidia-smi.XXXXXX")"
        cp -- "${runtime_nvidia_smi_source}" "${nvidia_smi_tmp}"
        chmod 0755 -- "${nvidia_smi_tmp}"
        [ "$(sha256sum -- "${nvidia_smi_tmp}" | awk '{print $1}')" = "${runtime_nvidia_smi_hash}" ] \
          || fail "staged nvidia-smi hash mismatch"
        mv -f -- "${nvidia_smi_tmp}" "${jail_nvidia_smi}"
        nvidia_smi_tmp=""
        jail_nvidia_smi_source="$(readlink -m -- "${jail_nvidia_smi}")" \
          || fail "installed passive-rootfs nvidia-smi path could not be resolved"
        [ -f "${jail_nvidia_smi_source}" ] && [ -x "${jail_nvidia_smi_source}" ] \
          || fail "installed passive-rootfs nvidia-smi is missing or not executable"

        atomic_symlink() {
          local target="$1"
          local name="$2"
          local staged_link="${staging}/${name}"
          ln -s -- "${target}" "${staged_link}"
          mv -Tf -- "${staged_link}" "${jail_lib_dir}/${name}"
        }

        atomic_symlink "${cuda_base}" libcuda.so.1
        atomic_symlink libcuda.so.1 libcuda.so
        atomic_symlink "${nvml_base}" libnvidia-ml.so.1
        atomic_symlink libnvidia-ml.so.1 libnvidia-ml.so

        ldconfig -r "${jail}"

        assert_link() {
          local name="$1"
          local expected="$2"
          local link="${jail_lib_dir}/${name}"
          [ -L "${link}" ] || fail "${name} is not a passive-rootfs symlink"
          [ "$(readlink -- "${link}")" = "${expected}" ] \
            || fail "${name} has an unexpected target"
          resolve_jail_link "${link}" >/dev/null
        }

        assert_link libcuda.so.1 "${cuda_base}"
        assert_link libcuda.so libcuda.so.1
        assert_link libnvidia-ml.so.1 "${nvml_base}"
        assert_link libnvidia-ml.so libnvidia-ml.so.1

        linker_cache="$(ldconfig -r "${jail}" -p)"
        printf '%s\n' "${linker_cache}" | grep -F 'libcuda.so.1' >/dev/null \
          || fail "passive-rootfs linker cache is missing libcuda.so.1"
        printf '%s\n' "${linker_cache}" | grep -F 'libnvidia-ml.so.1' >/dev/null \
          || fail "passive-rootfs linker cache is missing libnvidia-ml.so.1"

        jail_loader="${jail}/lib64/ld-linux-x86-64.so.2"
        [ -x "${jail_loader}" ] || fail "passive-rootfs dynamic loader is missing"
        jailed_library_path="${jail_lib_dir}:${jail}/lib/${arch}-linux-gnu:${jail}/lib64:${jail}/usr/lib64"
        jailed_gpu_output="$(env -i "${jail_loader}" \
          --library-path "${jailed_library_path}" "${jail_nvidia_smi_source}" -L)" \
          || fail "jailed nvidia-smi -L failed"
        jailed_gpu_count="$(printf '%s\n' "${jailed_gpu_output}" | grep -c '^GPU ')"
        [ "${jailed_gpu_count}" -gt 0 ] || fail "jailed nvidia-smi -L reported no GPUs"

        marker_tmp="$(mktemp "${marker_dir}/.gpu-driver-jail.env.XXXXXX")"
        printf '%s\n' "${marker_lines}" >"${marker_tmp}"
        chmod 0644 -- "${marker_tmp}"
        mv -f -- "${marker_tmp}" "${marker}"
        marker_tmp=""
        [ "$(cat "${marker}")" = "${marker_lines}" ] \
          || fail "GPU driver evidence marker validation failed"

        printf 'gpu-jail-post-population: driver=%s visible_gpus=%s status=passed\n' \
          "${driver_version}" "${jailed_gpu_count}"
        """
    ).strip()
    + "\n"
)


class JailGpuPostActivationError(ValueError):
    """Raised when post-activation GPU evidence does not satisfy every gate."""


@dataclass(frozen=True)
class JailGpuPostPopulationResources:
    """Idempotent ConfigMap and Job manifests for one passive-rootfs validation."""

    config_map: Mapping[str, Any]
    job: Mapping[str, Any]
    script_sha256: str

    def as_manifest_list(self) -> dict[str, Any]:
        return {
            "apiVersion": "v1",
            "kind": "List",
            "items": [copy.deepcopy(dict(self.config_map)), copy.deepcopy(dict(self.job))],
        }


@dataclass(frozen=True)
class JailGpuPostActivationValidation:
    """Canonical successful post-activation gate result."""

    node_name: str
    gpu_count: int
    checks: tuple[Mapping[str, str], ...]
    status: str = "passed"

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def as_payload(self) -> dict[str, Any]:
        return {
            "schema": GPU_JAIL_POST_ACTIVATION_SCHEMA,
            "status": self.status,
            "passed": self.passed,
            "node_name": self.node_name,
            "gpu_count": self.gpu_count,
            "checks": [dict(check) for check in self.checks],
        }


def _resource_name(target_ref: str, suffix: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", str(target_ref or "").strip().lower())
    normalized = normalized.strip("-") or "soperator"
    available = 63 - len(suffix) - 1
    normalized = normalized[:available].rstrip("-") or "soperator"
    return f"{normalized}-{suffix}"


def _require_dns_subdomain(value: str, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a non-empty Kubernetes DNS subdomain.")
    normalized = value.strip()
    labels = normalized.split(".")
    if (
        not normalized
        or normalized != value
        or len(normalized) > 253
        or any(not _DNS_LABEL_RE.fullmatch(label) for label in labels)
    ):
        raise ValueError(f"{field} must be a non-empty Kubernetes DNS subdomain.")
    return normalized


def build_jail_gpu_post_population_resources(
    *,
    namespace: str,
    target_ref: str,
    runner_image: str,
    passive_pvc: str,
    scheduling: Mapping[str, Any] | None = None,
    allow_driver_refresh: bool = False,
) -> JailGpuPostPopulationResources:
    """Build a deterministic Job that validates GPU libraries in a passive rootfs.

    The image value is copied byte-for-byte from the caller's resolved target
    controller image. The generated shell script receives no caller-controlled
    text and all Kubernetes object references are validated separately.
    """

    namespace = _require_dns_subdomain(namespace, field="namespace")
    passive_pvc = _require_dns_subdomain(passive_pvc, field="passive_pvc")
    if not isinstance(runner_image, str):
        raise ValueError("runner_image must be the exact non-empty target controller image.")
    image = runner_image
    if not image or image != image.strip():
        raise ValueError("runner_image must be the exact non-empty target controller image.")
    if not re.fullmatch(r"[^\s@]+(?:/[^\s@]+)*@sha256:[0-9a-f]{64}", image):
        raise ValueError("runner_image must be an immutable target repository@sha256 digest.")
    target = str(target_ref or "").strip()
    if not target:
        raise ValueError("target_ref must be non-empty.")

    job_name = _resource_name(target, "gpu-jail-post-populate")
    config_map_name = _resource_name(target, "gpu-jail-post-populate-script")
    script_sha256 = hashlib.sha256(GPU_JAIL_POST_POPULATION_SCRIPT.encode()).hexdigest()
    labels = {
        "app.kubernetes.io/component": "gpu-jail-post-population",
        "slurm.nebius.ai/jail-rootfs-refresh": "active-passive",
    }
    config_map = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": config_map_name,
            "namespace": namespace,
            "labels": dict(labels),
            "annotations": {"nebius.ai/script-sha256": script_sha256},
        },
        "data": {GPU_JAIL_POST_POPULATION_SCRIPT_KEY: GPU_JAIL_POST_POPULATION_SCRIPT},
    }
    pod_spec: dict[str, Any] = {
        "restartPolicy": "Never",
        "runtimeClassName": "nvidia",
        "automountServiceAccountToken": False,
        "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}},
        "containers": [
            {
                "name": "gpu-jail-post-population",
                "image": image,
                "imagePullPolicy": "IfNotPresent",
                "env": [
                    {"name": "NVIDIA_VISIBLE_DEVICES", "value": "all"},
                    {"name": "NVIDIA_DRIVER_CAPABILITIES", "value": "compute,utility"},
                    *(
                        [{"name": "CXCLI_ALLOW_DRIVER_REFRESH", "value": "true"}]
                        if allow_driver_refresh
                        else []
                    ),
                ],
                "command": [
                    "/bin/bash",
                    f"{GPU_JAIL_POST_POPULATION_SCRIPT_MOUNT}/{GPU_JAIL_POST_POPULATION_SCRIPT_KEY}",
                ],
                # Deliberately request no Kubernetes GPU resource. The NVIDIA runtime
                # exposes devices read-only for validation without preempting the
                # Slurm worker Pod that owns the node's allocatable GPUs.
                "resources": {},
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True,
                    "runAsNonRoot": False,
                    "runAsUser": 0,
                    "capabilities": {"drop": ["ALL"]},
                },
                "volumeMounts": [
                    {
                        "name": "passive-rootfs",
                        "mountPath": GPU_JAIL_PASSIVE_ROOTFS_MOUNT,
                    },
                    {
                        "name": "post-population-script",
                        "mountPath": GPU_JAIL_POST_POPULATION_SCRIPT_MOUNT,
                        "readOnly": True,
                    },
                ],
            }
        ],
        "volumes": [
            {
                "name": "passive-rootfs",
                "persistentVolumeClaim": {"claimName": passive_pvc},
            },
            {
                "name": "post-population-script",
                "configMap": {
                    "name": config_map_name,
                    "defaultMode": 0o555,
                    "items": [
                        {
                            "key": GPU_JAIL_POST_POPULATION_SCRIPT_KEY,
                            "path": GPU_JAIL_POST_POPULATION_SCRIPT_KEY,
                        }
                    ],
                },
            },
        ],
    }
    safe_scheduling = active_passive_pod_scheduling_fields(scheduling)
    safe_scheduling.pop("priorityClassName", None)
    pod_spec.update(safe_scheduling)
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": namespace,
            "labels": dict(labels),
            "annotations": {"nebius.ai/script-sha256": script_sha256},
        },
        "spec": {
            "activeDeadlineSeconds": 600,
            "backoffLimit": 0,
            "template": {
                "metadata": {
                    "labels": dict(labels),
                    "annotations": {"nebius.ai/script-sha256": script_sha256},
                },
                "spec": pod_spec,
            },
        },
    }
    return JailGpuPostPopulationResources(
        config_map=config_map,
        job=job,
        script_sha256=script_sha256,
    )


def _mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise JailGpuPostActivationError(f"{field} evidence must be a mapping.")
    return value


def _positive_int(value: Any, *, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise JailGpuPostActivationError(f"{field} must be a positive integer.")
    return value


def _require_passed(evidence: Mapping[str, Any], *, field: str) -> None:
    if evidence.get("status") != "passed":
        raise JailGpuPostActivationError(f"{field} status must be exactly 'passed'.")


def _require_node(evidence: Mapping[str, Any], *, field: str, expected_node: str) -> None:
    observed = evidence.get("node_name")
    if observed != expected_node:
        raise JailGpuPostActivationError(
            f"{field} node_name must match the pinned node {expected_node!r}; got {observed!r}."
        )


def validate_jail_gpu_post_activation(
    *,
    expected_node: str,
    expected_gpu_count: int,
    full_health_checker: Mapping[str, Any],
    pinned_h100_smoke: Mapping[str, Any],
    post_epilog: Mapping[str, Any],
    slurm_node: Mapping[str, Any],
) -> JailGpuPostActivationValidation:
    """Require every post-activation GPU, epilog, and Slurm gate to pass.

    Evidence uses one canonical schema: each executable gate has ``status``,
    ``node_name``, and its gate-specific fields; the Slurm observation has
    ``node_name``, ``state``, and ``reason``. No partial or unknown evidence is
    treated as success.
    """

    if not isinstance(expected_node, str):
        raise JailGpuPostActivationError("expected_node must be a non-empty canonical node name.")
    node_name = expected_node
    if not node_name or node_name != node_name.strip():
        raise JailGpuPostActivationError("expected_node must be a non-empty canonical node name.")
    gpu_count = _positive_int(expected_gpu_count, field="expected_gpu_count")
    health = _mapping(full_health_checker, field="full_health_checker")
    smoke = _mapping(pinned_h100_smoke, field="pinned_h100_smoke")
    epilog = _mapping(post_epilog, field="post_epilog")
    slurm = _mapping(slurm_node, field="slurm_node")

    _require_passed(health, field="full_health_checker")
    _require_node(health, field="full_health_checker", expected_node=node_name)
    failed_checks = health.get("failed_checks")
    if (
        not isinstance(failed_checks, Sequence)
        or isinstance(failed_checks, (str, bytes, bytearray))
        or failed_checks
    ):
        raise JailGpuPostActivationError(
            "full_health_checker failed_checks must be an explicit empty sequence."
        )
    if _positive_int(health.get("gpu_count"), field="full_health_checker.gpu_count") != gpu_count:
        raise JailGpuPostActivationError(
            "full_health_checker.gpu_count must match expected_gpu_count."
        )

    _require_passed(smoke, field="pinned_h100_smoke")
    _require_node(smoke, field="pinned_h100_smoke", expected_node=node_name)
    if smoke.get("pinned") is not True:
        raise JailGpuPostActivationError("pinned_h100_smoke.pinned must be true.")
    gpu_product = str(smoke.get("gpu_product", "") or "").strip()
    if "h100" not in gpu_product.lower():
        raise JailGpuPostActivationError("pinned_h100_smoke.gpu_product must identify an H100 GPU.")
    if _positive_int(smoke.get("gpu_count"), field="pinned_h100_smoke.gpu_count") != gpu_count:
        raise JailGpuPostActivationError(
            "pinned_h100_smoke.gpu_count must match expected_gpu_count."
        )

    _require_passed(epilog, field="post_epilog")
    _require_node(epilog, field="post_epilog", expected_node=node_name)
    if epilog.get("completed") is not True:
        raise JailGpuPostActivationError("post_epilog.completed must be true.")
    exit_code = epilog.get("exit_code")
    if isinstance(exit_code, bool) or exit_code != 0:
        raise JailGpuPostActivationError("post_epilog.exit_code must be integer 0.")

    _require_node(slurm, field="slurm_node", expected_node=node_name)
    if slurm.get("state") not in GPU_JAIL_POST_ACTIVATION_HEALTHY_IDLE_STATES:
        expected_states = ", ".join(sorted(GPU_JAIL_POST_ACTIVATION_HEALTHY_IDLE_STATES))
        raise JailGpuPostActivationError(
            f"slurm_node.state must be one exact schedulable idle state: {expected_states}."
        )
    if slurm.get("reason") != "":
        raise JailGpuPostActivationError("slurm_node.reason must be empty.")

    return JailGpuPostActivationValidation(
        node_name=node_name,
        gpu_count=gpu_count,
        checks=(
            {"name": "full health checker", "status": "passed"},
            {"name": "pinned H100 smoke", "status": "passed"},
            {"name": "post-epilog validation", "status": "passed"},
            {"name": "Slurm schedulable IDLE state", "status": "passed"},
        ),
    )
