from __future__ import annotations

import copy

import pytest

from nebius_cxcli.soperator_jail_gpu_validation import (
    GPU_JAIL_PASSIVE_ROOTFS_MOUNT,
    GPU_JAIL_POST_ACTIVATION_SCHEMA,
    GPU_JAIL_POST_POPULATION_SCRIPT,
    GPU_JAIL_POST_POPULATION_SCRIPT_KEY,
    JailGpuPostActivationError,
    build_jail_gpu_post_population_resources,
    validate_jail_gpu_post_activation,
)


def _successful_post_activation_evidence() -> dict[str, object]:
    return {
        "expected_node": "worker-gpu-0",
        "expected_gpu_count": 8,
        "full_health_checker": {
            "status": "passed",
            "node_name": "worker-gpu-0",
            "gpu_count": 8,
            "failed_checks": [],
        },
        "pinned_h100_smoke": {
            "status": "passed",
            "node_name": "worker-gpu-0",
            "pinned": True,
            "gpu_product": "NVIDIA H100 80GB HBM3",
            "gpu_count": 8,
        },
        "post_epilog": {
            "status": "passed",
            "node_name": "worker-gpu-0",
            "completed": True,
            "exit_code": 0,
        },
        "slurm_node": {
            "node_name": "worker-gpu-0",
            "state": "IDLE+DYNAMIC_NORM",
            "reason": "",
        },
    }


def test_build_jail_gpu_post_population_resources_uses_exact_upstream_image() -> None:
    image = "cr.example.invalid/soperator/populate-jail@sha256:" + "a" * 64
    scheduling = {
        "nodeSelector": {"slurm.nebius.ai/nodeset-name": "worker-gpu"},
        "tolerations": [
            {
                "key": "slurm.nebius.ai/nodeset-name",
                "operator": "Equal",
                "value": "worker-gpu",
                "effect": "NoSchedule",
            }
        ],
        "priorityClassName": "soperator-slurm-populate-jail",
    }
    scheduling_before = copy.deepcopy(scheduling)

    resources = build_jail_gpu_post_population_resources(
        namespace="soperator",
        target_ref="training",
        runner_image=image,
        passive_pvc="jail-rootfs-slot-b-pvc",
        scheduling=scheduling,
    )

    assert scheduling == scheduling_before
    assert resources.config_map["data"] == {
        GPU_JAIL_POST_POPULATION_SCRIPT_KEY: GPU_JAIL_POST_POPULATION_SCRIPT
    }
    assert resources.config_map["metadata"]["annotations"] == {
        "nebius.ai/script-sha256": resources.script_sha256
    }
    pod_spec = resources.job["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    assert container["image"] == image
    assert container["command"] == [
        "/bin/bash",
        "/opt/nebius-cxcli/post-populate-gpu-jail.sh",
    ]
    assert container["resources"] == {}
    assert container["env"] == [
        {"name": "NVIDIA_VISIBLE_DEVICES", "value": "all"},
        {"name": "NVIDIA_DRIVER_CAPABILITIES", "value": "compute,utility"},
    ]
    assert pod_spec["runtimeClassName"] == "nvidia"
    assert pod_spec["nodeSelector"] == scheduling["nodeSelector"]
    assert pod_spec["tolerations"] == scheduling["tolerations"]
    assert "priorityClassName" not in pod_spec
    assert resources.as_manifest_list()["items"] == [resources.config_map, resources.job]


def test_gpu_post_population_job_is_deterministic_and_security_scoped() -> None:
    kwargs = {
        "namespace": "soperator",
        "target_ref": "training",
        "runner_image": ("registry.example.invalid/populate-jail@sha256:" + "b" * 64),
        "passive_pvc": "jail-rootfs-slot-b-pvc",
    }

    first = build_jail_gpu_post_population_resources(**kwargs)
    second = build_jail_gpu_post_population_resources(**kwargs)

    assert first == second
    pod_spec = first.job["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["securityContext"] == {"seccompProfile": {"type": "RuntimeDefault"}}
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "runAsNonRoot": False,
        "runAsUser": 0,
        "capabilities": {"drop": ["ALL"]},
    }
    mounts = {mount["name"]: mount for mount in container["volumeMounts"]}
    assert mounts["passive-rootfs"] == {
        "name": "passive-rootfs",
        "mountPath": GPU_JAIL_PASSIVE_ROOTFS_MOUNT,
    }
    assert "host-driver-root" not in mounts
    volumes = {volume["name"]: volume for volume in pod_spec["volumes"]}
    assert "host-driver-root" not in volumes
    assert all("hostPath" not in volume for volume in volumes.values())
    assert volumes["passive-rootfs"]["persistentVolumeClaim"] == {
        "claimName": "jail-rootfs-slot-b-pvc"
    }


def test_gpu_post_population_script_is_atomic_idempotent_and_fail_closed() -> None:
    script = GPU_JAIL_POST_POPULATION_SCRIPT

    assert "set -euo pipefail" in script
    assert "PopulateJail image must run on the locked linux/amd64 platform" in script
    assert "aarch64" not in script
    assert "mixed host GPU driver versions are not allowed" in script
    assert "mixed runtime GPU driver libraries:" in script
    assert 'exec 9<"${marker_dir}"' in script
    assert "flock -x 9" in script
    assert "gpu-driver-jail.lock" not in script
    assert "env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin" in script
    assert "chroot" not in script
    assert "driver_root" not in script
    assert 'runtime_nvidia_smi_hash="$(sha256sum -- "${runtime_nvidia_smi_source}"' in script
    assert "ldconfig -p | awk" in script
    assert "arch=${arch}" in script
    assert '"${soname}"|"${expected_versioned}"' not in script
    assert '"${expected_versioned}") ;;' in script
    assert "libcuda_sha256=${cuda_hash}" in script
    assert "libnvidia_ml_sha256=${nvml_hash}" in script
    assert "nvidia_smi_sha256=${runtime_nvidia_smi_hash}" in script
    assert 'nvidia_smi_tmp="$(mktemp "${jail_bin_dir}/.cxcli-nvidia-smi.XXXXXX")"' in script
    assert 'cp -- "${runtime_nvidia_smi_source}" "${nvidia_smi_tmp}"' in script
    assert 'chmod 0755 -- "${nvidia_smi_tmp}"' in script
    assert 'mv -f -- "${nvidia_smi_tmp}" "${jail_nvidia_smi}"' in script
    assert "staged nvidia-smi hash mismatch" in script
    assert "existing passive-rootfs GPU driver evidence does not match" in script
    assert 'staging="$(mktemp -d "${jail_lib_dir}/.cxcli-gpu-driver.XXXXXX")"' in script
    assert 'mv -Tf -- "${staged_link}" "${jail_lib_dir}/${name}"' in script
    assert 'atomic_symlink "${cuda_base}" libcuda.so.1' in script
    assert "atomic_symlink libcuda.so.1 libcuda.so" in script
    assert 'atomic_symlink "${nvml_base}" libnvidia-ml.so.1' in script
    assert "atomic_symlink libnvidia-ml.so.1 libnvidia-ml.so" in script
    assert "passive-rootfs symlink resolves outside the passive rootfs" in script
    assert "broken passive-rootfs symlink" in script
    assert 'ldconfig -r "${jail}"' in script
    assert "grep -F 'libcuda.so.1'" in script
    assert "grep -F 'libnvidia-ml.so.1'" in script
    assert "passive-rootfs nvidia-smi resolves outside the passive rootfs" in script
    assert '"${jail_nvidia_smi_source}" -L' in script
    assert "GPU driver evidence marker validation failed" in script


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("namespace", "", "namespace must be"),
        ("namespace", "Soperator", "namespace must be"),
        ("passive_pvc", "bad/pvc", "passive_pvc must be"),
        ("target_ref", "", "target_ref must be non-empty"),
        ("runner_image", " image:tag", "exact non-empty target controller image"),
        ("runner_image", "image:tag", "immutable target"),
    ],
)
def test_build_jail_gpu_post_population_resources_rejects_invalid_inputs(
    field: str,
    value: str,
    message: str,
) -> None:
    kwargs = {
        "namespace": "soperator",
        "target_ref": "training",
        "runner_image": ("registry.example.invalid/controller_slurmctld@sha256:" + "a" * 64),
        "passive_pvc": "jail-rootfs-slot-b-pvc",
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=message):
        build_jail_gpu_post_population_resources(**kwargs)


def test_validate_jail_gpu_post_activation_requires_every_gate() -> None:
    evidence = _successful_post_activation_evidence()

    result = validate_jail_gpu_post_activation(**evidence)

    assert result.passed is True
    assert result.status == "passed"
    assert result.node_name == "worker-gpu-0"
    assert result.gpu_count == 8
    assert result.as_payload() == {
        "schema": GPU_JAIL_POST_ACTIVATION_SCHEMA,
        "status": "passed",
        "passed": True,
        "node_name": "worker-gpu-0",
        "gpu_count": 8,
        "checks": [
            {"name": "full health checker", "status": "passed"},
            {"name": "pinned H100 smoke", "status": "passed"},
            {"name": "post-epilog validation", "status": "passed"},
            {"name": "Slurm IDLE+DYNAMIC_NORM", "status": "passed"},
        ],
    }


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("full_health_checker", "status"), "pending", "status must be exactly 'passed'"),
        (("full_health_checker", "failed_checks"), ["dcgm"], "explicit empty sequence"),
        (("full_health_checker", "gpu_count"), 7, "must match expected_gpu_count"),
        (("pinned_h100_smoke", "node_name"), "worker-gpu-1", "pinned node"),
        (("pinned_h100_smoke", "pinned"), False, "pinned must be true"),
        (("pinned_h100_smoke", "gpu_product"), "NVIDIA A100", "identify an H100"),
        (("pinned_h100_smoke", "gpu_count"), 1, "must match expected_gpu_count"),
        (("post_epilog", "completed"), False, "completed must be true"),
        (("post_epilog", "exit_code"), True, "exit_code must be integer 0"),
        (("slurm_node", "state"), "IDLE", "exactly 'IDLE\\+DYNAMIC_NORM'"),
        (("slurm_node", "state"), " IDLE+DYNAMIC_NORM ", "exactly 'IDLE\\+DYNAMIC_NORM'"),
        (("slurm_node", "reason"), "Epilog error", "reason must be empty"),
        (("slurm_node", "reason"), None, "reason must be empty"),
    ],
)
def test_validate_jail_gpu_post_activation_fails_closed(
    path: tuple[str, str],
    value: object,
    message: str,
) -> None:
    evidence = _successful_post_activation_evidence()
    section = evidence[path[0]]
    assert isinstance(section, dict)
    section[path[1]] = value

    with pytest.raises(JailGpuPostActivationError, match=message):
        validate_jail_gpu_post_activation(**evidence)


@pytest.mark.parametrize("expected_gpu_count", [0, True, "unknown"])
def test_validate_jail_gpu_post_activation_rejects_invalid_expected_gpu_count(
    expected_gpu_count: object,
) -> None:
    evidence = _successful_post_activation_evidence()
    evidence["expected_gpu_count"] = expected_gpu_count

    with pytest.raises(JailGpuPostActivationError, match="positive integer"):
        validate_jail_gpu_post_activation(**evidence)
