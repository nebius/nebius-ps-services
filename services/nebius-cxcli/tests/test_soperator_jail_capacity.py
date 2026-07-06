from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli.soperator_jail_capacity import (
    GIB,
    evaluate_jail_capacity,
    parse_capacity_probe_output,
    probe_active_passive_jail_capacity,
    recommend_jail_sfs_size_gib,
    required_passive_rootfs_bytes,
    resolve_jail_sfs_resize_policy,
)


def test_required_passive_rootfs_capacity_uses_active_slot_headroom() -> None:
    required, source, degraded = required_passive_rootfs_bytes(80 * GIB)

    assert required == 100 * GIB
    assert source == "measured-active-slot"
    assert degraded is False


def test_required_passive_rootfs_capacity_falls_back_to_minimum_when_degraded() -> None:
    required, source, degraded = required_passive_rootfs_bytes(None)

    assert required == 64 * GIB
    assert source == "fallback-minimum"
    assert degraded is True


def test_evaluate_jail_capacity_reports_shortage() -> None:
    result = evaluate_jail_capacity(
        passive_available_bytes=90 * GIB,
        active_used_bytes=80 * GIB,
    )

    assert result.status == "failed"
    assert result.required_gib == 100
    assert result.passive_available_gib == 90
    assert result.shortage_gib == 10


def test_parse_capacity_probe_output_marks_unknown_active_usage_degraded() -> None:
    result = parse_capacity_probe_output(
        "active_used_kib=\npassive_available_kib=33554432\n"
    )

    assert result.status == "failed"
    assert result.degraded is True
    assert result.required_gib == 64
    assert result.passive_available_gib == 32


def test_resize_recommendation_rounds_shortage_plus_headroom_to_256_gib() -> None:
    assert recommend_jail_sfs_size_gib(current_size_gib=2048, shortage_bytes=10 * GIB) == 2304


def test_resize_recommendation_rejects_shrink_or_too_small_explicit_size() -> None:
    with pytest.raises(ValueError, match="cannot shrink"):
        recommend_jail_sfs_size_gib(
            current_size_gib=2048,
            shortage_bytes=10 * GIB,
            explicit_size_gib=1024,
        )

    with pytest.raises(ValueError, match="too small"):
        recommend_jail_sfs_size_gib(
            current_size_gib=2048,
            shortage_bytes=10 * GIB,
            explicit_size_gib=2050,
        )


def test_resize_policy_defaults_follow_interactivity() -> None:
    assert resolve_jail_sfs_resize_policy(None, interactive=True) == "prompt"
    assert resolve_jail_sfs_resize_policy(None, interactive=False) == "fail"
    assert resolve_jail_sfs_resize_policy("apply", interactive=False) == "apply"


def test_probe_active_passive_jail_capacity_uses_kubectl_job_and_parses_logs() -> None:
    calls: list[tuple[tuple[str, ...], str | None, bool]] = []

    def runner(
        args: tuple[str, ...],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SimpleNamespace:
        del timeout_seconds
        calls.append((tuple(args), input_text, check))
        if "logs" in args:
            return SimpleNamespace(
                stdout="active_used_kib=83886080\npassive_available_kib=104857600\n"
            )
        return SimpleNamespace(stdout="")

    result = probe_active_passive_jail_capacity(
        runner,
        namespace="soperator",
        target_ref="prod",
        image="populate:jail",
        active_pvc="jail-rootfs-slot-a-pvc",
        passive_pvc="jail-rootfs-slot-b-pvc",
        active_rootfs_path="/mnt/jail",
        exclude_paths=("/mnt/jail/.cxcli", "/mnt/jail/home"),
        scheduling={
            "nodeSelector": {"slurm.nebius.ai/nodeset-name": "system"},
            "tolerations": [
                {
                    "key": "slurm.nebius.ai/nodeset-name",
                    "operator": "Equal",
                    "value": "system",
                    "effect": "NoSchedule",
                }
            ],
            "priorityClassName": "prod-slurm-populate-jail",
        },
        kube_context="ctx",
    )

    assert result.sufficient is True
    assert result.required_gib == 100
    assert calls[0][0] == (
        "kubectl",
        "--context",
        "ctx",
        "-n",
        "soperator",
        "delete",
        "job",
        "prod-jail-capacity-probe",
        "--ignore-not-found",
        "--wait=false",
    )
    assert calls[1][0] == ("kubectl", "--context", "ctx", "apply", "-f", "-")
    assert "automountServiceAccountToken: false" in str(calls[1][1])
    assert "jail-rootfs-slot-a-pvc" in str(calls[1][1])
    applied = yaml.safe_load(calls[1][1] or "{}")
    probe_script = applied["items"][0]["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert "subtract_path /mnt/active/.cxcli" in probe_script
    assert "subtract_path /mnt/active/home" in probe_script
    assert "priorityClassName: prod-slurm-populate-jail" in str(calls[1][1])
    assert "slurm.nebius.ai/nodeset-name: system" in str(calls[1][1])
    assert "effect: NoSchedule" in str(calls[1][1])
    assert calls[-1][2] is False


def test_probe_active_passive_jail_capacity_mounts_shared_legacy_pvc_once() -> None:
    calls: list[tuple[tuple[str, ...], str | None, bool]] = []

    def runner(
        args: tuple[str, ...],
        *,
        input_text: str | None = None,
        timeout_seconds: int = 300,
        check: bool = True,
    ) -> SimpleNamespace:
        del timeout_seconds
        calls.append((tuple(args), input_text, check))
        if "logs" in args:
            return SimpleNamespace(
                stdout="active_used_kib=83886080\npassive_available_kib=104857600\n"
            )
        return SimpleNamespace(stdout="")

    result = probe_active_passive_jail_capacity(
        runner,
        namespace="soperator",
        target_ref="prod",
        image="populate:jail",
        active_pvc="jail-pvc",
        passive_pvc="jail-pvc",
        active_rootfs_path="/mnt/jail",
        exclude_paths=("/mnt/jail/.cxcli", "/mnt/jail/home"),
        kube_context="ctx",
    )

    assert result.sufficient is True
    applied = yaml.safe_load(calls[1][1] or "{}")
    pod_spec = applied["items"][0]["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    probe_script = container["command"][2]
    assert pod_spec["volumes"] == [
        {
            "name": "active-rootfs",
            "persistentVolumeClaim": {"claimName": "jail-pvc", "readOnly": True},
        }
    ]
    assert container["volumeMounts"] == [
        {"name": "active-rootfs", "mountPath": "/mnt/active", "readOnly": True}
    ]
    assert "df -Pk /mnt/active" in probe_script
    assert "/mnt/passive" not in probe_script
