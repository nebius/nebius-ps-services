from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from nebius_cxcli import cli
from nebius_cxcli.mk8s_upgrade import CompatibilityChoice, LiveNodeGroup
from nebius_cxcli.slurm_jobs import (
    parse_scontrol_show_partition_states,
    slurm_partition_pause_records,
)
from nebius_cxcli.soperator_upgrade_progress import bounded_identifier_summary

runner = CliRunner()
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _plain_cli_output(value: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", value)


def test_cross_version_partition_restore_normalizes_alloc_nodes_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = parse_scontrol_show_partition_states(
        "PartitionName=main State=UP AllocNodes=ALL Nodes=worker-[0-1]"
    )[0]
    record = slurm_partition_pause_records(partitions=("main",), states=(previous,))[0]
    observations = iter((previous, previous))
    commands: list[str] = []

    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_partition_state",
        lambda **_kwargs: next(observations),
    )

    def _run(_namespace: str, command: str, **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli, "_run_soperator_upgrade_login_command", _run)

    cli._soperator_upgrade_restore_slurm_partitions(
        namespace="soperator",
        records=(record,),
        allow_topology_migration=True,
    )

    assert commands == ["scontrol update PartitionName=main AllocNodes=ALL"]


def test_cross_version_partition_state_restore_also_normalizes_alloc_nodes_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = parse_scontrol_show_partition_states(
        "PartitionName=main State=UP AllocNodes=ALL Nodes=worker-[0-1]"
    )[0]
    applied = parse_scontrol_show_partition_states(
        "PartitionName=main State=DOWN AllocNodes=ALL Nodes=worker-[0-1]"
    )[0]
    record = slurm_partition_pause_records(partitions=("main",), states=(previous,))[0]
    record = record.with_applied_observation(applied)
    observations = iter((applied, previous))
    commands: list[str] = []

    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_partition_state",
        lambda **_kwargs: next(observations),
    )

    def _run(_namespace: str, command: str, **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli, "_run_soperator_upgrade_login_command", _run)

    cli._soperator_upgrade_restore_slurm_partitions(
        namespace="soperator",
        records=(record,),
        allow_topology_migration=True,
    )

    assert commands == ["scontrol update PartitionName=main State=UP AllocNodes=ALL"]


def test_same_version_partition_restore_does_not_reapply_alloc_nodes_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = parse_scontrol_show_partition_states(
        "PartitionName=main State=UP AllocNodes=ALL Nodes=worker-[0-1]"
    )[0]
    record = slurm_partition_pause_records(partitions=("main",), states=(previous,))[0]
    monkeypatch.setattr(
        cli,
        "_soperator_upgrade_partition_state",
        lambda **_kwargs: previous,
    )
    monkeypatch.setattr(
        cli,
        "_run_soperator_upgrade_login_command",
        lambda *_args, **_kwargs: pytest.fail("same-version restore issued a normalization"),
    )

    cli._soperator_upgrade_restore_slurm_partitions(
        namespace="soperator",
        records=(record,),
        allow_topology_migration=False,
    )


def test_bounded_identifier_summary_limits_high_cardinality_node_output() -> None:
    nodes = tuple(f"worker-{index}" for index in range(1000))

    assert bounded_identifier_summary(nodes) == "worker-0, worker-1, worker-2, +997 more"


def _full_stack_live_group(
    *,
    id: str,
    name: str,
    version: str,
    platform: str,
    gpu: bool,
    drivers_preset: str = "",
    target_node_count: int = 1,
) -> LiveNodeGroup:
    return LiveNodeGroup(
        id=id,
        name=name,
        version=version,
        resource_version=1,
        platform=platform,
        preset="preset-a",
        os="ubuntu22.04",
        drivers_preset=drivers_preset,
        gpu=gpu,
        raw=SimpleNamespace(status=SimpleNamespace(target_node_count=target_node_count)),
    )


def test_full_stack_target_planner_freezes_per_hop_cpu_and_gpu_compatibility() -> None:
    groups = (
        _full_stack_live_group(
            id="cpu-id",
            name="system",
            version="1.32",
            platform="cpu-platform",
            gpu=False,
        ),
        _full_stack_live_group(
            id="gpu-id",
            name="worker",
            version="1.33",
            platform="gpu-l40s",
            gpu=True,
            drivers_preset="cuda12.8",
        ),
    )

    def _choices(*, target_version: str, platform: str):
        assert target_version in {"1.33", "1.34"}
        return (
            CompatibilityChoice(
                platform=platform,
                os="ubuntu24.04",
                drivers_preset="cuda13.0" if platform.startswith("gpu-") else "",
            ),
        )

    targets, rows = cli._soperator_full_stack_node_group_targets(
        live_groups=groups,
        source_kubernetes_version="1.33",
        target_kubernetes_version="1.34",
        kubernetes_hops=("1.34",),
        to_os="auto",
        to_gpu_stack_preset="auto",
        os_overrides={},
        gpu_overrides={},
        compatibility_lookup=_choices,
    )

    assert [(target.key, target.target_os, target.target_drivers_preset) for target in targets] == [
        ("system", "ubuntu24.04", ""),
        ("worker", "ubuntu24.04", "cuda13.0"),
    ]
    assert {(row.group_key, row.kubernetes_version) for row in rows} == {
        ("system", "1.33"),
        ("system", "1.34"),
        ("worker", "1.33"),
        ("worker", "1.34"),
    }


def test_full_stack_campaign_plan_uses_one_row_per_group_and_keeps_each_hop() -> None:
    group = SimpleNamespace(
        key="worker",
        provider_id="gpu-id",
        platform="gpu-h100-sxm",
        gpu=True,
    )
    intent = SimpleNamespace(
        target_ref="cluster-a",
        ownership="onboarded",
        backend="provider-api",
        source_release="1.22.3",
        target_release="4.1.7",
        target_jail_cuda_version="12.9.0",
        source_kubernetes_version="1.33",
        target_kubernetes_version="1.35",
        kubernetes_hops=("1.34", "1.35"),
        supported_kubernetes_versions=("1.33", "1.34", "1.35"),
        node_group_strategy="zero-surge",
        drain_timeout="auto",
        zero_size_gpu_validation="skip-with-proof",
        job_policy="requeue-hold-all",
        segments=("mk8s-hop:1.34", "runtime-readiness:1.34", "mk8s-hop:1.35"),
        node_groups=(group,),
        compatibility_rows=(
            SimpleNamespace(
                group_key="worker",
                kubernetes_version="1.34",
                os="ubuntu22.04",
                drivers_preset="cuda12.8",
            ),
            SimpleNamespace(
                group_key="worker",
                kubernetes_version="1.35",
                os="ubuntu24.04",
                drivers_preset="cuda13.0",
            ),
        ),
    )
    live_group = SimpleNamespace(
        metadata=SimpleNamespace(id="gpu-id"),
        status=SimpleNamespace(target_node_count=1000, ready_node_count=997),
    )

    lines = cli._format_soperator_full_stack_campaign_plan(
        intent,
        dry_run=False,
        live_node_groups=(live_group,),
    )
    table_lines = [line for line in lines if line.startswith("  worker")]

    assert "- frozen Kubernetes hops: 1.33 -> 1.34, 1.34 -> 1.35" in lines
    assert any("K8s target compatibility" in line for line in lines)
    assert len(table_lines) == 1
    assert "997/1000" in table_lines[0]
    assert "1.34=ubuntu22.04/cuda12.8" in table_lines[0]
    assert "1.35=ubuntu24.04/cuda13.0" in table_lines[0]

    intent.compatibility_rows = (
        SimpleNamespace(
            group_key="worker",
            kubernetes_version="1.34",
            os="ubuntu24.04",
            drivers_preset="cuda13.0",
        ),
        SimpleNamespace(
            group_key="worker",
            kubernetes_version="1.35",
            os="ubuntu24.04",
            drivers_preset="cuda13.0",
        ),
    )
    compressed_lines = cli._format_soperator_full_stack_campaign_plan(
        intent,
        dry_run=False,
        live_node_groups=(live_group,),
    )
    compressed_row = next(line for line in compressed_lines if line.startswith("  worker"))

    assert "1.34–1.35: ubuntu24.04/cuda13.0" in compressed_row
    assert "1.34→1.35" not in compressed_row


def test_full_stack_target_planner_records_zero_size_gpu_desired_state_policy() -> None:
    group = _full_stack_live_group(
        id="gpu-id",
        name="worker",
        version="1.33",
        platform="gpu-l40s",
        gpu=True,
        drivers_preset="cuda12.8",
        target_node_count=0,
    )

    targets, _rows = cli._soperator_full_stack_node_group_targets(
        live_groups=(group,),
        source_kubernetes_version="1.33",
        target_kubernetes_version="1.33",
        kubernetes_hops=(),
        to_os="keep",
        to_gpu_stack_preset="keep",
        os_overrides={},
        gpu_overrides={},
        compatibility_lookup=lambda **_kwargs: (
            CompatibilityChoice(
                platform="gpu-l40s",
                os="ubuntu22.04",
                drivers_preset="cuda12.8",
            ),
        ),
    )

    assert targets[0].zero_sized is True


def test_full_stack_auto_selects_latest_os_then_driver_per_group_and_hop() -> None:
    groups = (
        _full_stack_live_group(
            id="gpu-a",
            name="worker-a",
            version="1.33",
            platform="gpu-a",
            gpu=True,
            drivers_preset="cuda12.8",
        ),
        _full_stack_live_group(
            id="gpu-b",
            name="worker-b",
            version="1.33",
            platform="gpu-b",
            gpu=True,
            drivers_preset="cuda12.8",
        ),
    )

    def _choices(*, target_version: str, platform: str):
        if platform == "gpu-a":
            return (
                CompatibilityChoice(platform=platform, os="ubuntu22.04", drivers_preset="cuda13.0"),
                CompatibilityChoice(platform=platform, os="ubuntu24.04", drivers_preset="cuda12.8"),
                CompatibilityChoice(platform=platform, os="ubuntu24.04", drivers_preset="cuda13.0"),
            )
        return (
            CompatibilityChoice(
                platform=platform,
                os="ubuntu22.04" if target_version == "1.33" else "ubuntu24.04",
                drivers_preset="cuda12.8" if target_version == "1.33" else "cuda13.0",
            ),
        )

    targets, rows = cli._soperator_full_stack_node_group_targets(
        live_groups=groups,
        source_kubernetes_version="1.33",
        target_kubernetes_version="1.34",
        kubernetes_hops=("1.34",),
        to_os="auto",
        to_gpu_stack_preset="auto",
        os_overrides={},
        gpu_overrides={},
        compatibility_lookup=_choices,
    )

    assert [(target.key, target.target_os, target.target_drivers_preset) for target in targets] == [
        ("worker-a", "ubuntu24.04", "cuda13.0"),
        ("worker-b", "ubuntu24.04", "cuda13.0"),
    ]
    assert [
        (row.group_key, row.kubernetes_version, row.os, row.drivers_preset) for row in rows
    ] == [
        ("worker-a", "1.34", "ubuntu24.04", "cuda13.0"),
        ("worker-b", "1.34", "ubuntu24.04", "cuda13.0"),
    ]


def test_full_stack_auto_rejects_ambiguous_provider_ordering() -> None:
    group = _full_stack_live_group(
        id="gpu-id",
        name="worker",
        version="1.33",
        platform="gpu-l40s",
        gpu=True,
        drivers_preset="cuda12.8",
    )

    with pytest.raises(RuntimeError, match="version order is ambiguous"):
        cli._soperator_full_stack_node_group_targets(
            live_groups=(group,),
            source_kubernetes_version="1.33",
            target_kubernetes_version="1.33",
            kubernetes_hops=(),
            to_os="auto",
            to_gpu_stack_preset="auto",
            os_overrides={},
            gpu_overrides={},
            compatibility_lookup=lambda **_kwargs: (
                CompatibilityChoice(
                    platform="gpu-l40s", os="custom-stable", drivers_preset="cuda13.0"
                ),
                CompatibilityChoice(
                    platform="gpu-l40s", os="custom-next", drivers_preset="cuda13.0"
                ),
            ),
        )


def test_full_stack_exact_global_gpu_preset_ignores_driverless_groups() -> None:
    groups = (
        _full_stack_live_group(
            id="cpu-id",
            name="system",
            version="1.33",
            platform="cpu-platform",
            gpu=False,
        ),
        _full_stack_live_group(
            id="gpu-id",
            name="worker",
            version="1.33",
            platform="gpu-l40s",
            gpu=True,
            drivers_preset="cuda12.8",
        ),
    )

    targets, _rows = cli._soperator_full_stack_node_group_targets(
        live_groups=groups,
        source_kubernetes_version="1.33",
        target_kubernetes_version="1.33",
        kubernetes_hops=(),
        to_os="ubuntu24.04",
        to_gpu_stack_preset="cuda13.0",
        os_overrides={},
        gpu_overrides={},
        compatibility_lookup=lambda *, platform, **_kwargs: (
            CompatibilityChoice(
                platform=platform,
                os="ubuntu24.04",
                drivers_preset="cuda13.0" if platform == "gpu-l40s" else "",
            ),
        ),
    )

    assert [(target.key, target.target_drivers_preset) for target in targets] == [
        ("system", ""),
        ("worker", "cuda13.0"),
    ]


def test_full_stack_exact_gpu_override_rejects_driverless_group() -> None:
    group = _full_stack_live_group(
        id="cpu-id",
        name="system",
        version="1.33",
        platform="cpu-platform",
        gpu=False,
    )

    with pytest.raises(ValueError, match="cannot accept a Nebius drivers preset override"):
        cli._soperator_full_stack_node_group_targets(
            live_groups=(group,),
            source_kubernetes_version="1.33",
            target_kubernetes_version="1.33",
            kubernetes_hops=(),
            to_os="ubuntu24.04",
            to_gpu_stack_preset="auto",
            os_overrides={},
            gpu_overrides={"system": "cuda13.0"},
            compatibility_lookup=lambda **_kwargs: (
                CompatibilityChoice(platform="cpu-platform", os="ubuntu24.04", drivers_preset=""),
            ),
        )


def test_full_stack_target_order_is_stable_across_provider_permutations() -> None:
    groups = (
        _full_stack_live_group(
            id="gpu-b",
            name="worker-b",
            version="1.33",
            platform="gpu-l40s",
            gpu=True,
            drivers_preset="cuda12.8",
        ),
        _full_stack_live_group(
            id="gpu-a",
            name="worker-a",
            version="1.33",
            platform="gpu-l40s",
            gpu=True,
            drivers_preset="cuda12.8",
        ),
    )
    kwargs = {
        "source_kubernetes_version": "1.33",
        "target_kubernetes_version": "1.33",
        "kubernetes_hops": (),
        "to_os": "ubuntu24.04",
        "to_gpu_stack_preset": "cuda13.0",
        "os_overrides": {},
        "gpu_overrides": {},
        "compatibility_lookup": lambda **_kwargs: (
            CompatibilityChoice(platform="gpu-l40s", os="ubuntu24.04", drivers_preset="cuda13.0"),
        ),
    }

    assert cli._soperator_full_stack_node_group_targets(
        live_groups=groups, **kwargs
    ) == cli._soperator_full_stack_node_group_targets(live_groups=tuple(reversed(groups)), **kwargs)


def test_full_stack_rejects_ambiguous_node_group_override_alias() -> None:
    groups = (
        _full_stack_live_group(
            id="group-a", name="shared", version="1.33", platform="cpu-a", gpu=False
        ),
        _full_stack_live_group(
            id="group-b", name="shared", version="1.33", platform="cpu-b", gpu=False
        ),
    )

    with pytest.raises(ValueError, match="Ambiguous node-group override.*shared"):
        cli._soperator_full_stack_node_group_targets(
            live_groups=groups,
            source_kubernetes_version="1.33",
            target_kubernetes_version="1.33",
            kubernetes_hops=(),
            to_os="auto",
            to_gpu_stack_preset="auto",
            os_overrides={"shared": "ubuntu24.04"},
            gpu_overrides={},
            compatibility_lookup=lambda **_kwargs: (),
        )


def test_upgrade_gpu_validation_keeps_operator_global_and_scopes_cuda_per_active_group(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[dict[str, Any]] = []
    validations = [
        {
            "kind": "mk8s_gpu_operator_readiness",
            "target_ref": "cluster-a",
            "name": "GPU operator readiness",
            "report_file": "operator.json",
        },
        {
            "kind": "mk8s_gpu_visibility",
            "target_ref": "cluster-a",
            "name": "GPU visibility",
            "report_file": "visibility.json",
        },
    ]
    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _config_path: (object(), SimpleNamespace(reports_dir=tmp_path), object()),
    )
    monkeypatch.setattr(cli, "_manifest_deploy_validations", lambda _manifest: validations)
    monkeypatch.setattr(
        cli,
        "_filter_validations_for_target",
        lambda items, **_kwargs: [dict(item) for item in items],
    )

    def _run(items, *, reports_dir, **_kwargs):  # type: ignore[no-untyped-def]
        captured.extend(dict(item) for item in items)
        reports = []
        for item in items:
            report_path = reports_dir / str(item["report_file"])
            report_path.write_text(
                json.dumps(
                    {
                        "kind": item["kind"],
                        "passed": True,
                        "skipped": False,
                        "selected_node_count": 1,
                        "passed_node_count": 1,
                    }
                ),
                encoding="utf-8",
            )
            reports.append(report_path)
        return reports

    monkeypatch.setattr(cli, "_run_deploy_validations", _run)

    outcomes = cli._run_target_upgrade_validations(
        config_path=tmp_path / "config.yaml",
        target_ref="cluster-a",
        kube_env={},
        gpu_node_groups={
            "worker-a": ("worker-a", "node-group-a"),
            "worker-b": ("worker-b", "node-group-b"),
        },
    )

    assert tuple(outcome["kind"] for outcome in outcomes) == (
        "mk8s_gpu_operator_readiness",
        "mk8s_gpu_visibility",
        "mk8s_gpu_visibility",
    )
    assert tuple(outcome["group"] for outcome in outcomes) == (
        "",
        "worker-a",
        "worker-b",
    )
    assert [item.get("node_groups") for item in captured] == [
        None,
        ["worker-a", "node-group-a"],
        ["worker-b", "node-group-b"],
    ]
    assert len({str(item["report_file"]) for item in captured}) == 3
    assert len({outcome["validationRunId"] for outcome in outcomes}) == 1
    for outcome in outcomes:
        report_path = Path(str(outcome["reportFile"]))
        assert outcome["reportSha256"] == (
            "sha256:" + hashlib.sha256(report_path.read_bytes()).hexdigest()
        )


def test_upgrade_validation_archives_canonical_soperator_report_per_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    validation = {
        "kind": "soperator_cluster_smoke",
        "target_ref": "cluster-a",
        "name": "Soperator cluster smoke test",
        "report_file": "deploy-smoke-report-cluster-a.json",
    }
    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _config_path: (object(), SimpleNamespace(reports_dir=tmp_path), object()),
    )
    monkeypatch.setattr(cli, "_manifest_deploy_validations", lambda _manifest: [validation])
    monkeypatch.setattr(
        cli,
        "_filter_validations_for_target",
        lambda items, **_kwargs: [dict(item) for item in items],
    )
    monkeypatch.setattr(cli.secrets, "token_hex", lambda _length: "attempt123")
    captured: list[dict[str, Any]] = []

    def _run(items, *, reports_dir, **_kwargs):  # type: ignore[no-untyped-def]
        captured.extend(dict(item) for item in items)
        report_path = reports_dir / str(items[0]["report_file"])
        report_path.write_text(
            json.dumps({"kind": "soperator_cluster_smoke", "passed": True}),
            encoding="utf-8",
        )
        return [report_path]

    monkeypatch.setattr(cli, "_run_deploy_validations", _run)

    outcomes = cli._run_target_upgrade_validations(
        config_path=tmp_path / "config.yaml",
        target_ref="cluster-a",
        kube_env={},
    )

    assert captured[0]["report_file"] == "deploy-smoke-report-cluster-a.json"
    archived = tmp_path / "deploy-smoke-report-cluster-a-attempt123.json"
    assert Path(str(outcomes[0]["reportFile"])) == archived
    assert archived.is_file()
    assert outcomes[0]["passed"] is True


def test_upgrade_validation_archives_fresh_failed_canonical_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    validation = {
        "kind": "soperator_cluster_smoke",
        "target_ref": "cluster-a",
        "name": "Soperator cluster smoke test",
        "report_file": "deploy-smoke-report-cluster-a.json",
    }
    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _config_path: (object(), SimpleNamespace(reports_dir=tmp_path), object()),
    )
    monkeypatch.setattr(cli, "_manifest_deploy_validations", lambda _manifest: [validation])
    monkeypatch.setattr(
        cli,
        "_filter_validations_for_target",
        lambda items, **_kwargs: [dict(item) for item in items],
    )
    monkeypatch.setattr(cli.secrets, "token_hex", lambda _length: "failedattempt")

    def _run(items, *, reports_dir, **_kwargs):  # type: ignore[no-untyped-def]
        report_path = reports_dir / str(items[0]["report_file"])
        report_path.write_text(
            json.dumps(
                {
                    "kind": "soperator_cluster_smoke",
                    "passed": False,
                    "failure": "injected",
                }
            ),
            encoding="utf-8",
        )
        raise RuntimeError("injected validation failure")

    monkeypatch.setattr(cli, "_run_deploy_validations", _run)

    with pytest.raises(RuntimeError, match="injected validation failure"):
        cli._run_target_upgrade_validations(
            config_path=tmp_path / "config.yaml",
            target_ref="cluster-a",
            kube_env={},
        )

    archived = tmp_path / "deploy-smoke-report-cluster-a-failedattempt.json"
    assert json.loads(archived.read_text(encoding="utf-8"))["passed"] is False


def test_upgrade_validation_does_not_archive_stale_canonical_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    validation = {
        "kind": "soperator_cluster_smoke",
        "target_ref": "cluster-a",
        "name": "Soperator cluster smoke test",
        "report_file": "deploy-smoke-report-cluster-a.json",
    }
    canonical = tmp_path / str(validation["report_file"])
    canonical.write_text(json.dumps({"passed": True, "run": "older"}), encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _config_path: (object(), SimpleNamespace(reports_dir=tmp_path), object()),
    )
    monkeypatch.setattr(cli, "_manifest_deploy_validations", lambda _manifest: [validation])
    monkeypatch.setattr(
        cli,
        "_filter_validations_for_target",
        lambda items, **_kwargs: [dict(item) for item in items],
    )
    monkeypatch.setattr(cli.secrets, "token_hex", lambda _length: "staleattempt")
    monkeypatch.setattr(
        cli,
        "_run_deploy_validations",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("failed before report")),
    )

    with pytest.raises(RuntimeError, match="failed before report"):
        cli._run_target_upgrade_validations(
            config_path=tmp_path / "config.yaml",
            target_ref="cluster-a",
            kube_env={},
        )

    assert not (tmp_path / "deploy-smoke-report-cluster-a-staleattempt.json").exists()


def test_upgrade_gpu_validation_requires_manifest_spec_for_active_group(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _config_path: (object(), SimpleNamespace(reports_dir=tmp_path), object()),
    )
    monkeypatch.setattr(cli, "_manifest_deploy_validations", lambda _manifest: [])
    monkeypatch.setattr(cli, "_filter_validations_for_target", lambda items, **_kwargs: items)

    with pytest.raises(cli.SoperatorSafetyPauseError, match="exactly one target-scoped"):
        cli._run_target_upgrade_validations(
            config_path=tmp_path / "config.yaml",
            target_ref="cluster-a",
            kube_env={},
            gpu_node_groups={"worker-a": ("worker-a", "node-group-a")},
        )


def test_upgrade_gpu_validation_rejects_skipped_cuda_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    validation = {
        "kind": "mk8s_gpu_visibility",
        "target_ref": "cluster-a",
        "name": "GPU visibility",
        "report_file": "visibility.json",
    }
    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _config_path: (object(), SimpleNamespace(reports_dir=tmp_path), object()),
    )
    monkeypatch.setattr(cli, "_manifest_deploy_validations", lambda _manifest: [validation])
    monkeypatch.setattr(
        cli,
        "_filter_validations_for_target",
        lambda items, **_kwargs: [dict(item) for item in items],
    )

    def _run(items, *, reports_dir, **_kwargs):  # type: ignore[no-untyped-def]
        report_path = reports_dir / str(items[0]["report_file"])
        report_path.write_text(
            json.dumps(
                {
                    "kind": "mk8s_gpu_visibility",
                    "passed": True,
                    "skipped": True,
                    "selected_node_count": 0,
                    "passed_node_count": 0,
                }
            ),
            encoding="utf-8",
        )
        return [report_path]

    monkeypatch.setattr(cli, "_run_deploy_validations", _run)

    with pytest.raises(RuntimeError, match="was skipped"):
        cli._run_target_upgrade_validations(
            config_path=tmp_path / "config.yaml",
            target_ref="cluster-a",
            kube_env={},
            gpu_node_groups={"worker-a": ("worker-a", "node-group-a")},
        )


def test_upgrade_gpu_validation_failed_replay_preserves_accepted_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    validation = {
        "kind": "mk8s_gpu_visibility",
        "target_ref": "cluster-a",
        "name": "GPU visibility",
        "report_file": "visibility.json",
    }
    monkeypatch.setattr(
        cli,
        "_load_deploy_context_readonly",
        lambda _config_path: (object(), SimpleNamespace(reports_dir=tmp_path), object()),
    )
    monkeypatch.setattr(cli, "_manifest_deploy_validations", lambda _manifest: [validation])
    monkeypatch.setattr(
        cli,
        "_filter_validations_for_target",
        lambda items, **_kwargs: [dict(item) for item in items],
    )
    run_ids = iter(("acceptedrun", "failedrun"))
    monkeypatch.setattr(cli.secrets, "token_hex", lambda _length: next(run_ids))
    invocation = 0
    attempted_paths: list[Path] = []

    def _run(items, *, reports_dir, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal invocation
        invocation += 1
        report_path = reports_dir / str(items[0]["report_file"])
        attempted_paths.append(report_path)
        report_path.write_text(
            json.dumps(
                {
                    "kind": "mk8s_gpu_visibility",
                    "passed": True,
                    "skipped": False,
                    "selected_node_count": 1,
                    "passed_node_count": 1,
                    "invocation": invocation,
                }
            ),
            encoding="utf-8",
        )
        if invocation == 2:
            raise RuntimeError("injected later validation failure")
        return [report_path]

    monkeypatch.setattr(cli, "_run_deploy_validations", _run)
    accepted = cli._run_target_upgrade_validations(
        config_path=tmp_path / "config.yaml",
        target_ref="cluster-a",
        kube_env={},
        gpu_node_groups={"worker-a": ("worker-a", "node-group-a")},
    )
    accepted_path = Path(str(accepted[0]["reportFile"]))
    accepted_bytes = accepted_path.read_bytes()

    with pytest.raises(RuntimeError, match="later validation failure"):
        cli._run_target_upgrade_validations(
            config_path=tmp_path / "config.yaml",
            target_ref="cluster-a",
            kube_env={},
            gpu_node_groups={"worker-a": ("worker-a", "node-group-a")},
        )

    assert attempted_paths[0] == accepted_path
    assert attempted_paths[1] != accepted_path
    assert accepted_path.read_bytes() == accepted_bytes


def test_upgrade_admission_validates_the_target_scoped_flux_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: v1\n", encoding="utf-8")
    paths = cli.resolve_project_paths(config_path)
    staged_paths = cli.staged_generated_paths(paths)
    calls: list[list[str]] = []
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/bin/kubectl")
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda args, **_kwargs: calls.append(list(args)) or SimpleNamespace(returncode=0),
    )

    cli._validate_rendered_flux_manifests(
        staged_paths,
        command_name="soperator upgrade admission",
        target_ref="cluster-a",
    )

    expected = cli._paths_for_target_flux_dir(
        staged_paths,
        {"target_ref": "cluster-a"},
    )
    assert calls == [["kubectl", "kustomize", str(expected.flux_dir)]]


def test_upgrade_live_release_probe_preserves_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_env: dict[str, str] = {}

    def _run(*_args: object, **kwargs: object) -> SimpleNamespace:
        observed_env.update(kwargs["env"])  # type: ignore[arg-type]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {"items": [{"metadata": {"labels": {"app.kubernetes.io/version": "1.22.3"}}}]}
            ),
            stderr="",
        )

    monkeypatch.setattr(cli.subprocess, "run", _run)

    assert cli._live_soperator_release_for_reconcile(env={"KUBECONFIG": "/tmp/config"}) == (
        "1.22.3"
    )
    assert observed_env["KUBECONFIG"] == "/tmp/config"
    assert observed_env["PATH"] == os.environ["PATH"]


def _soperator_targets(*target_refs: str) -> dict[str, Any]:
    return {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": target_ref,
                    "target_ref": target_ref,
                    "enabled": True,
                    "version": "4.1.7",
                }
                for target_ref in target_refs
            ]
        }
    }


def _onboard_source_payload(*, managed_target_ref: str = "") -> dict[str, Any]:
    infra_rows: list[dict[str, Any]] = []
    if managed_target_ref:
        infra_rows.append(
            {
                "id": "mk8s",
                "instance_id": managed_target_ref,
                "enabled": True,
            }
        )
    return {
        "version": "v1",
        "client_info": {
            "client_name": "client-a",
            "nebius": {
                "tenant_id": "tenant-a",
                "project_id": "project-a",
                "region_id": "eu-north1",
            },
            "notifications": {"email_enabled": False, "email": None},
        },
        "infra": {"components": infra_rows},
        "apps": {"charts": []},
    }


def _valid_onboard_snapshot() -> dict[str, Any]:
    return {
        "cluster_identity": {"kubernetes_uid": "kubernetes-uid-a"},
        "collection_errors": [],
        "helm_releases": [
            {
                "name": "soperator",
                "namespace": "soperator",
                "storage_namespace": "soperator",
                "status": "deployed",
                "chart_version": "soperator-4.1.7",
            }
        ],
    }


def _prepare_onboarding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    snapshot: dict[str, Any],
    source_payload: dict[str, Any] | None = None,
) -> tuple[Path, list[str], list[str]]:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("sentinel: unchanged\n", encoding="utf-8")
    payload = source_payload or _onboard_source_payload()
    effects: list[str] = []
    collection_contexts: list[str] = []

    monkeypatch.setattr(cli, "component_entries", lambda _scope: ())
    monkeypatch.setattr(
        cli,
        "_resolve_soperator_onboard_config_target",
        lambda *_args, **_kwargs: cli.SoperatorOnboardConfigTarget(config_path=config_path),
    )
    monkeypatch.setattr(cli, "load_config", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(cli, "_load_config_payload", lambda _path: copy.deepcopy(payload))

    @contextmanager
    def _status(*_args: Any, **_kwargs: Any):
        yield

    @contextmanager
    def _generated_context(*_args: Any, **_kwargs: Any):
        yield "generated-context"

    def _collect(*, kube_context: str, **_kwargs: Any) -> dict[str, Any]:
        collection_contexts.append(kube_context)
        return copy.deepcopy(snapshot)

    def _unexpected_effect(name: str):
        def _raise(*_args: Any, **_kwargs: Any) -> None:
            effects.append(name)
            raise AssertionError(f"unexpected onboarding effect: {name}")

        return _raise

    metadata = SimpleNamespace(
        release="4.1.7",
        repository="https://github.com/nebius/soperator",
        tag="4.1.7",
        commit="a" * 40,
        tree="b" * 40,
    )
    source = SimpleNamespace(
        source_dir=tmp_path,
        archive_sha256="sha256:" + "c" * 64,
        manifest_sha256="sha256:" + "d" * 64,
    )
    provenance = SimpleNamespace(
        method="official-render-equivalence",
        live_manifest_sha256="sha256:" + "e" * 64,
        rendered_manifest_sha256="sha256:" + "e" * 64,
        owned_object_graph_sha256="sha256:" + "f" * 64,
    )

    monkeypatch.setattr(cli, "_soperator_registration_status", _status)
    monkeypatch.setattr(cli, "_onboarded_soperator_cluster_context", _generated_context)
    monkeypatch.setattr(cli, "collect_kubectl_soperator_snapshot", _collect)
    monkeypatch.setattr(
        cli,
        "_soperator_public_discovery_provider_observation",
        lambda **_kwargs: (
            SimpleNamespace(
                metadata=SimpleNamespace(labels={}),
                spec=SimpleNamespace(),
                status=SimpleNamespace(),
            ),
            {"control_plane_version": "1.33", "node_groups": []},
            [],
        ),
    )

    @contextmanager
    def _provider_context(**_kwargs: Any):
        yield "provider-context", {}, "https://eu-north1.mk8s.example.invalid"

    monkeypatch.setattr(cli, "_soperator_public_discovery_provider_context", _provider_context)
    monkeypatch.setattr(
        cli,
        "_soperator_registration_adoption_values_from_snapshot",
        lambda _snapshot: ({}, ""),
    )
    monkeypatch.setattr(
        cli,
        "inspect_soperator_release_contract",
        lambda _release: (metadata, source, "upstream-flux-v1", "sha256:" + "1" * 64),
    )
    monkeypatch.setattr(
        cli,
        "verify_live_soperator_release_provenance",
        lambda **_kwargs: provenance,
    )
    monkeypatch.setattr(
        cli,
        "soperator_protected_storage_evidence",
        lambda _snapshot: ("sha256:" + "2" * 64, {}),
    )
    monkeypatch.setattr(
        cli,
        "_write_runtime_payload_config",
        _unexpected_effect("write-config"),
    )
    monkeypatch.setattr(
        cli,
        "_run_internal_render_command",
        _unexpected_effect("render"),
    )
    monkeypatch.setattr(
        cli,
        "write_source_soperator_discovery_report",
        _unexpected_effect("write-discovery"),
    )
    return config_path, effects, collection_contexts


def _onboard(
    config_path: Path,
    *,
    cluster_id: str | None = "mk8scluster-a",
    target_id: str | None = "cluster-a",
    kube_context: str | None = None,
    access: str = "external",
    region_id: str | None = None,
    interactive: bool = False,
) -> None:
    cli._register_existing_soperator_target(
        target_path=config_path,
        client_name=None,
        tenant_id=None,
        project_id=None,
        region_id=region_id,
        email=None,
        cluster_id=cluster_id,
        target_id=target_id,
        kube_context=kube_context,
        access=access,
        interactive=interactive,
    )


def test_common_soperator_target_selection_rejects_an_empty_config() -> None:
    with pytest.raises(RuntimeError, match="No enabled apps:soperator rows"):
        cli._prompt_soperator_upgrade_target_if_needed(
            source_payload=_soperator_targets(),
            target_ref=None,
            interactive=False,
        )


def test_common_soperator_target_selection_uses_the_only_target() -> None:
    target = cli._prompt_soperator_upgrade_target_if_needed(
        source_payload=_soperator_targets("cluster-a"),
        target_ref=None,
        interactive=False,
    )

    assert target.target_ref == "cluster-a"


def test_common_soperator_target_selection_requires_an_explicit_noninteractive_choice() -> None:
    with pytest.raises(RuntimeError, match="Multiple Soperator targets.*--target"):
        cli._prompt_soperator_upgrade_target_if_needed(
            source_payload=_soperator_targets("cluster-a", "cluster-b"),
            target_ref=None,
            interactive=False,
        )


def test_common_soperator_target_selection_prompts_for_multiple_interactive_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _choose(*_args: Any, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "apps:soperator@cluster-b"

    monkeypatch.setattr(cli, "_prompt_upgrade_choice", _choose)

    target = cli._prompt_soperator_upgrade_target_if_needed(
        source_payload=_soperator_targets("cluster-a", "cluster-b"),
        target_ref=None,
        interactive=True,
    )

    assert target.target_ref == "cluster-b"
    assert [choice.value for choice in captured["choices"]] == [
        "apps:soperator@cluster-a",
        "apps:soperator@cluster-b",
    ]


def test_common_soperator_target_selection_rejects_a_non_soperator_selector() -> None:
    with pytest.raises(ValueError, match="Soperator upgrades require"):
        cli._prompt_soperator_upgrade_target_if_needed(
            source_payload=_soperator_targets("cluster-a"),
            target_ref="apps:other@cluster-a",
            interactive=False,
        )


def test_common_soperator_target_resolution_rejects_an_unknown_explicit_target() -> None:
    with pytest.raises(ValueError, match="Could not find enabled apps:soperator@missing"):
        cli._resolve_soperator_command_target(
            _soperator_targets("cluster-a"),
            target_ref="missing",
            interactive=False,
        )


def test_common_soperator_target_resolution_accepts_a_known_explicit_target() -> None:
    target, target_row, is_onboarded = cli._resolve_soperator_command_target(
        _soperator_targets("cluster-a"),
        target_ref="cluster-a",
        interactive=False,
    )

    assert target.target_ref == "cluster-a"
    assert target_row is None
    assert not is_onboarded


@pytest.mark.parametrize(
    ("option_name", "kwargs"),
    (
        pytest.param("--client-name", {"client_name": "client-a"}, id="client-name"),
        pytest.param("--tenant-id", {"tenant_id": "tenant-a"}, id="tenant-id"),
        pytest.param("--project-id", {"project_id": "project-a"}, id="project-id"),
        pytest.param("--email", {"email": "ops@example.invalid"}, id="email"),
    ),
)
def test_existing_onboard_config_rejects_project_creation_flags(
    tmp_path: Path,
    option_name: str,
    kwargs: dict[str, str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("version: v1\n", encoding="utf-8")
    arguments: dict[str, Any] = {
        "interactive": False,
        "client_name": None,
        "tenant_id": None,
        "project_id": None,
        "region_id": None,
        "email": None,
        "infra_entries": (),
        "app_entries": (),
        **kwargs,
    }

    with pytest.raises(RuntimeError, match=option_name):
        cli._resolve_soperator_onboard_config_target(config_path, **arguments)


def test_fresh_interactive_onboard_omits_region_prompt_for_live_derivation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tenant-a" / "project-a" / "config.yaml"
    region_calls: list[tuple[str | None, bool]] = []
    scaffold_regions: list[str] = []

    monkeypatch.setattr(cli, "_validate_deployments_root_target", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_resolve_deployments_root", lambda _path: tmp_path)
    monkeypatch.setattr(cli, "_assert_not_nested_deployments_root", lambda _path: None)
    monkeypatch.setattr(
        cli,
        "_validate_tenant_project_ids_or_prompt",
        lambda **_kwargs: ("tenant-a", "project-a"),
    )
    monkeypatch.setattr(cli, "ProviderOptionLookup", lambda: object())
    monkeypatch.setattr(
        cli,
        "_resolve_create_target_folders",
        lambda **_kwargs: ("tenant-a", "project-a"),
    )
    monkeypatch.setattr(cli, "_project_config_path", lambda **_kwargs: config_path)
    monkeypatch.setattr(cli, "_ensure_project_auth_identity", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_region_or_prompt",
        lambda value, *, interactive: (
            region_calls.append((value, interactive)) or cli.DEFAULT_REGION_ID
        ),
    )
    monkeypatch.setattr(cli, "_optional_email_or_prompt", lambda *_args, **_kwargs: None)

    def _scaffold(**kwargs: Any) -> cli.BootstrapResult:
        scaffold_regions.append(kwargs["region_id"])
        assert kwargs["expected_config_absent"] is True
        marker_path = config_path.parent / ".nebius-cxcli" / "soperator-onboard-bootstrap.json"
        assert marker_path.exists()
        config_yaml = kwargs["config_yaml"]
        assert isinstance(config_yaml, str)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(config_yaml, encoding="utf-8")
        return cli.BootstrapResult(
            deployments_root=tmp_path,
            project_path=config_path.parent,
            config_path=config_path,
            wrote_config=True,
        )

    monkeypatch.setattr(cli, "_scaffold_instance", _scaffold)
    monkeypatch.setattr(
        cli,
        "_confirm_soperator_onboard_existing_config",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        cli,
        "_ensure_deployments_gitignore",
        lambda **_kwargs: cli.DeploymentsGitignoreResult(
            path=None,
            wrote=False,
            repo_root=None,
        ),
    )

    resolved = cli._resolve_soperator_onboard_config_target(
        tmp_path,
        interactive=True,
        client_name="client-a",
        tenant_id="tenant-a",
        project_id="project-a",
        region_id=None,
        email=None,
        infra_entries=(),
        app_entries=(),
    )

    assert resolved.config_path == config_path
    assert resolved.region_from_live_cluster is True
    assert region_calls == [(None, False)]
    assert scaffold_regions == [cli.DEFAULT_REGION_ID]
    marker_path = config_path.parent / ".nebius-cxcli" / "soperator-onboard-bootstrap.json"
    assert marker_path.exists()

    retry = cli._resolve_soperator_onboard_config_target(
        tmp_path,
        interactive=True,
        client_name="client-a",
        tenant_id="tenant-a",
        project_id="project-a",
        region_id=None,
        email=None,
        infra_entries=(),
        app_entries=(),
    )

    assert retry.region_from_live_cluster is True

    direct_retry = cli._resolve_soperator_onboard_config_target(
        config_path,
        interactive=False,
        client_name=None,
        tenant_id=None,
        project_id=None,
        region_id=None,
        email=None,
        infra_entries=(),
        app_entries=(),
    )

    assert direct_retry.live_region_config_sha256 == cli._sha256_file(config_path)

    config_path.write_text(config_path.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    assert cli.SoperatorOnboardConfigTarget.pending_live_region_config_sha256(config_path) == ""


def test_expected_absent_config_publish_preserves_a_competing_writer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    original_link = cli.os.link

    def _publish_competing_config(source: str | Path, destination: str | Path) -> None:
        Path(destination).write_text("competitor\n", encoding="utf-8")
        original_link(source, destination)

    monkeypatch.setattr(cli.os, "link", _publish_competing_config)

    with pytest.raises(FileExistsError):
        cli._write_text_atomic(config_path, "cxcli\n", expected_absent=True)

    assert config_path.read_text(encoding="utf-8") == "competitor\n"


def test_deployments_root_onboard_retry_replaces_scaffold_region_from_live_cluster(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path, effects, _collection_contexts = _prepare_onboarding(
        monkeypatch,
        tmp_path,
        snapshot=_valid_onboard_snapshot(),
    )
    live_region_config_sha256 = cli._sha256_file(config_path)
    monkeypatch.setattr(
        cli,
        "_resolve_soperator_onboard_config_target",
        lambda *_args, **_kwargs: cli.SoperatorOnboardConfigTarget(
            config_path=config_path,
            live_region_config_sha256=live_region_config_sha256,
        ),
    )
    monkeypatch.setattr(
        cli.SoperatorOnboardConfigTarget,
        "pending_live_region_config_sha256",
        classmethod(lambda _cls, _path: live_region_config_sha256),
        raising=True,
    )
    monkeypatch.setattr(
        cli,
        "_soperator_public_discovery_region",
        lambda **_kwargs: "eu-west1",
    )
    region_updates: list[tuple[str, str]] = []
    original_set_mapping_path_value = cli._set_mapping_path_value

    def _record_region(payload: dict[str, Any], path: str, value: Any) -> None:
        if path == "client_info.nebius.region_id":
            region_updates.append((path, str(value)))
        original_set_mapping_path_value(payload, path, value)

    monkeypatch.setattr(cli, "_set_mapping_path_value", _record_region)

    def _write_config(*_args: Any, **_kwargs: Any) -> bool:
        effects.append("write-config")
        return True

    def _render(*_args: Any, **_kwargs: Any) -> None:
        effects.append("render")

    def _write_discovery(*_args: Any, **_kwargs: Any) -> Path:
        effects.append("write-discovery")
        return tmp_path / "discovery" / "report.json"

    monkeypatch.setattr(cli, "_write_runtime_payload_config", _write_config)
    monkeypatch.setattr(cli, "_run_internal_render_command", _render)
    monkeypatch.setattr(cli, "write_source_soperator_discovery_report", _write_discovery)

    _onboard(config_path)

    assert region_updates == [("client_info.nebius.region_id", "eu-west1")]
    assert effects == ["write-config", "render", "write-discovery"]


def test_successful_onboard_clears_pending_live_region_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = _onboard_source_payload()
    payload["apps"]["charts"] = [
        {
            "id": "soperator",
            "instance_id": "cluster-a",
            "target_ref": "cluster-a",
            "enabled": True,
            "values": {},
        }
    ]
    config_path, _effects, _collection_contexts = _prepare_onboarding(
        monkeypatch,
        tmp_path,
        snapshot=_valid_onboard_snapshot(),
        source_payload=payload,
    )
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    live_region_config_sha256 = cli._sha256_file(config_path)
    cli.SoperatorOnboardConfigTarget.write_bootstrap_marker(
        config_path,
        tenant_id="tenant-a",
        project_id="project-a",
        config_sha256=live_region_config_sha256,
    )
    marker_path = cli.SoperatorOnboardConfigTarget.bootstrap_marker_path(config_path)
    monkeypatch.setattr(
        cli,
        "_resolve_soperator_onboard_config_target",
        lambda *_args, **_kwargs: cli.SoperatorOnboardConfigTarget(
            config_path=config_path,
            live_region_config_sha256=live_region_config_sha256,
        ),
    )
    monkeypatch.setattr(
        cli, "_ensure_soperator_registration_app_row", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(cli, "_materialize_single_target_app_bindings", lambda _payload: None)
    monkeypatch.setattr(cli, "_materialize_soperator_component_defaults", lambda _payload: None)
    monkeypatch.setattr(cli, "ensure_mk8s_gpu_app_rows", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "materialize_mk8s_gpu_app_values", lambda _payload: None)
    monkeypatch.setattr(cli, "_refresh_soperator_registration_fingerprints", lambda _payload: None)
    monkeypatch.setattr(cli, "_selection_change_issues", lambda _payload: [])

    def _write_config(path: Path, next_payload: dict[str, Any], **_kwargs: Any) -> bool:
        path.write_text(json.dumps(next_payload), encoding="utf-8")
        return True

    monkeypatch.setattr(cli, "_write_runtime_payload_config", _write_config)
    monkeypatch.setattr(cli, "_run_internal_render_command", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "write_source_soperator_discovery_report",
        lambda *_args, **_kwargs: tmp_path / "discovery" / "report.json",
    )

    _onboard(config_path)

    assert not marker_path.exists()


def test_established_name_resolved_onboard_config_keeps_region_authoritative(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "tenant-a" / "project-a" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        """version: v1
client_info:
  client_name: client-a
  nebius:
    tenant_id: tenant-a
    project_id: project-a
    region_id: eu-north1
  notifications:
    email_enabled: false
    email: null
infra:
  components: []
apps:
  charts: []
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "_validate_deployments_root_target", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_resolve_deployments_root", lambda _path: tmp_path)
    monkeypatch.setattr(cli, "_assert_not_nested_deployments_root", lambda _path: None)
    monkeypatch.setattr(
        cli,
        "_validate_tenant_project_ids_or_prompt",
        lambda **_kwargs: ("tenant-a", "project-a"),
    )
    monkeypatch.setattr(cli, "ProviderOptionLookup", lambda: object())
    monkeypatch.setattr(
        cli,
        "_resolve_create_target_folders",
        lambda **_kwargs: ("tenant-a", "project-a"),
    )
    monkeypatch.setattr(cli, "_project_config_path", lambda **_kwargs: config_path)
    monkeypatch.setattr(cli, "_ensure_project_auth_identity", lambda **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "_confirm_soperator_onboard_existing_config",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        cli,
        "_ensure_deployments_gitignore",
        lambda **_kwargs: cli.DeploymentsGitignoreResult(
            path=None,
            wrote=False,
            repo_root=None,
        ),
    )

    resolved = cli._resolve_soperator_onboard_config_target(
        tmp_path,
        interactive=True,
        client_name="client-a",
        tenant_id="tenant-a",
        project_id="project-a",
        region_id=None,
        email=None,
        infra_entries=(),
        app_entries=(),
    )

    assert resolved.config_path == config_path
    assert resolved.region_from_live_cluster is False


def test_onboard_noninteractive_requires_cluster_id_before_provider_or_collection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path, effects, collection_contexts = _prepare_onboarding(
        monkeypatch,
        tmp_path,
        snapshot=_valid_onboard_snapshot(),
    )

    with pytest.raises(RuntimeError, match="requires --cluster-id"):
        _onboard(config_path, cluster_id=None)

    assert collection_contexts == []
    assert effects == []
    assert config_path.read_text(encoding="utf-8") == "sentinel: unchanged\n"


def test_onboard_rejects_invalid_access_before_provider_or_collection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path, effects, collection_contexts = _prepare_onboarding(
        monkeypatch,
        tmp_path,
        snapshot=_valid_onboard_snapshot(),
    )

    with pytest.raises(RuntimeError, match="access must be either external or internal"):
        _onboard(config_path, access="private")

    assert collection_contexts == []
    assert effects == []
    assert config_path.read_text(encoding="utf-8") == "sentinel: unchanged\n"


def test_onboard_interactive_cluster_selection_precedes_live_discovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = _valid_onboard_snapshot()
    snapshot["collection_errors"] = ["collection stopped after selection"]
    config_path, effects, collection_contexts = _prepare_onboarding(
        monkeypatch,
        tmp_path,
        snapshot=snapshot,
    )
    selections: list[str] = []

    def _select(**_kwargs: Any) -> SimpleNamespace:
        selections.append("selected")
        return SimpleNamespace(value="mk8scluster-selected")

    monkeypatch.setattr(cli, "_prompt_project_mk8s_cluster_choice", _select)

    with pytest.raises(RuntimeError, match="complete live discovery"):
        _onboard(config_path, cluster_id=None, interactive=True)

    assert selections == ["selected"]
    assert collection_contexts == ["generated-context"]
    assert effects == []
    assert config_path.read_text(encoding="utf-8") == "sentinel: unchanged\n"


@pytest.mark.parametrize(
    ("case", "message"),
    (
        pytest.param("incomplete", "complete live discovery", id="incomplete-discovery"),
        pytest.param("ambiguous", "exactly one unambiguous", id="ambiguous-release"),
        pytest.param("undeployed", "release to be deployed", id="undeployed-release"),
        pytest.param("non-exact", "exact stable version", id="non-exact-release"),
        pytest.param("malformed-digest", "malformed live OCI digest", id="malformed-digest"),
    ),
)
def test_onboard_rejects_invalid_live_evidence_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    snapshot = _valid_onboard_snapshot()
    if case == "incomplete":
        snapshot["collection_errors"] = ["helm list failed"]
    elif case == "ambiguous":
        snapshot["helm_releases"].append(copy.deepcopy(snapshot["helm_releases"][0]))
    elif case == "undeployed":
        snapshot["helm_releases"][0]["status"] = "failed"
    elif case == "non-exact":
        snapshot["helm_releases"][0]["chart_version"] = "soperator-main"
    else:
        snapshot["helm_releases"][0]["digest"] = "sha256:not-a-digest"
    config_path, effects, collection_contexts = _prepare_onboarding(
        monkeypatch,
        tmp_path,
        snapshot=snapshot,
    )

    with pytest.raises(RuntimeError, match=message):
        _onboard(config_path)

    assert collection_contexts == ["generated-context"]
    assert effects == []
    assert config_path.read_text(encoding="utf-8") == "sentinel: unchanged\n"


def test_onboard_rejects_invalid_provenance_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path, effects, collection_contexts = _prepare_onboarding(
        monkeypatch,
        tmp_path,
        snapshot=_valid_onboard_snapshot(),
    )
    monkeypatch.setattr(
        cli,
        "verify_live_soperator_release_provenance",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("provenance mismatch")),
    )

    with pytest.raises(RuntimeError, match="provenance mismatch"):
        _onboard(config_path)

    assert collection_contexts == ["generated-context"]
    assert effects == []
    assert config_path.read_text(encoding="utf-8") == "sentinel: unchanged\n"


def test_onboard_rejects_configured_region_mismatch_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = _onboard_source_payload()
    payload["client_info"]["nebius"]["region_id"] = "eu-west1"
    config_path, effects, collection_contexts = _prepare_onboarding(
        monkeypatch,
        tmp_path,
        snapshot=_valid_onboard_snapshot(),
        source_payload=payload,
    )

    with pytest.raises(RuntimeError, match="does not match configured/expected region"):
        _onboard(config_path)

    assert collection_contexts == ["generated-context"]
    assert effects == []
    assert config_path.read_text(encoding="utf-8") == "sentinel: unchanged\n"


def test_onboard_rejects_explicit_region_that_conflicts_with_established_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path, effects, collection_contexts = _prepare_onboarding(
        monkeypatch,
        tmp_path,
        snapshot=_valid_onboard_snapshot(),
    )

    with pytest.raises(RuntimeError, match="explicit region.*does not match configured region"):
        _onboard(config_path, region_id="eu-west1")

    assert collection_contexts == []
    assert effects == []
    assert config_path.read_text(encoding="utf-8") == "sentinel: unchanged\n"


def test_onboard_reproves_pending_region_hash_after_live_observation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path, effects, collection_contexts = _prepare_onboarding(
        monkeypatch,
        tmp_path,
        snapshot=_valid_onboard_snapshot(),
    )
    live_region_config_sha256 = cli._sha256_file(config_path)
    monkeypatch.setattr(
        cli,
        "_resolve_soperator_onboard_config_target",
        lambda *_args, **_kwargs: cli.SoperatorOnboardConfigTarget(
            config_path=config_path,
            live_region_config_sha256=live_region_config_sha256,
        ),
    )
    authority_checks = iter((live_region_config_sha256, ""))
    monkeypatch.setattr(
        cli.SoperatorOnboardConfigTarget,
        "pending_live_region_config_sha256",
        classmethod(lambda _cls, _path: next(authority_checks)),
        raising=True,
    )

    with pytest.raises(RuntimeError, match="config changed after.*pending live-region scaffold"):
        _onboard(config_path)

    assert collection_contexts == ["generated-context"]
    assert effects == []
    assert config_path.read_text(encoding="utf-8") == "sentinel: unchanged\n"


def test_onboard_keeps_generated_context_open_through_provenance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path, effects, collection_contexts = _prepare_onboarding(
        monkeypatch,
        tmp_path,
        snapshot=_valid_onboard_snapshot(),
    )
    active = False

    @contextmanager
    def _generated_context(*_args: Any, **_kwargs: Any):
        nonlocal active
        active = True
        try:
            yield "generated-context"
        finally:
            active = False

    def _verify(**_kwargs: Any) -> None:
        assert active is True
        raise RuntimeError("stop after generated context lifetime proof")

    monkeypatch.setattr(cli, "_onboarded_soperator_cluster_context", _generated_context)
    monkeypatch.setattr(cli, "verify_live_soperator_release_provenance", _verify)

    with pytest.raises(RuntimeError, match="generated context lifetime proof"):
        _onboard(config_path)

    assert collection_contexts == ["generated-context"]
    assert effects == []
    assert active is False


def test_onboard_rejects_a_managed_target_collision_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path, effects, collection_contexts = _prepare_onboarding(
        monkeypatch,
        tmp_path,
        snapshot=_valid_onboard_snapshot(),
        source_payload=_onboard_source_payload(managed_target_ref="cluster-a"),
    )

    with pytest.raises(RuntimeError, match="already exists as a Terraform-managed"):
        _onboard(config_path)

    assert collection_contexts == ["generated-context"]
    assert effects == []
    assert config_path.read_text(encoding="utf-8") == "sentinel: unchanged\n"


def test_onboard_explicit_context_identity_match_continues_to_discovery_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshot = _valid_onboard_snapshot()
    snapshot["collection_errors"] = ["stop after identity proof"]
    config_path, effects, collection_contexts = _prepare_onboarding(
        monkeypatch,
        tmp_path,
        snapshot=snapshot,
    )
    provider_lookups: list[str] = []

    def _provider_uid(
        _payload: dict[str, Any],
        *,
        cluster_id: str,
        access: str,
    ) -> str:
        provider_lookups.append(f"{cluster_id}:{access}")
        return "kubernetes-uid-a"

    monkeypatch.setattr(cli, "_provider_generated_mk8s_kubernetes_uid", _provider_uid)

    with pytest.raises(RuntimeError, match="complete live discovery"):
        _onboard(config_path, kube_context="explicit-context")

    assert provider_lookups == ["mk8scluster-a:external"]
    assert collection_contexts == ["explicit-context"]
    assert effects == []
    assert config_path.read_text(encoding="utf-8") == "sentinel: unchanged\n"


def test_onboard_explicit_context_identity_mismatch_fails_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path, effects, collection_contexts = _prepare_onboarding(
        monkeypatch,
        tmp_path,
        snapshot=_valid_onboard_snapshot(),
    )
    monkeypatch.setattr(
        cli,
        "_provider_generated_mk8s_kubernetes_uid",
        lambda *_args, **_kwargs: "different-kubernetes-uid",
    )

    with pytest.raises(RuntimeError, match="different Kubernetes cluster"):
        _onboard(config_path, kube_context="explicit-context")

    assert collection_contexts == ["explicit-context"]
    assert effects == []
    assert config_path.read_text(encoding="utf-8") == "sentinel: unchanged\n"


def test_removed_discovery_redaction_fails_at_parser_before_cluster_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    downstream_calls: list[str] = []
    monkeypatch.setattr(
        cli,
        "_run_soperator_public_discovery_command",
        lambda **_kwargs: downstream_calls.append("collection"),
    )

    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "discover",
            str(tmp_path / "support"),
            "--tenant-id",
            "tenant-a",
            "--project-id",
            "project-a",
            "--cluster-id",
            "mk8scluster-a",
            "--redaction",
            "raw",
        ],
    )

    output = _plain_cli_output(result.output)
    assert result.exit_code == 2
    assert "No such option" in output
    assert "--redaction" in output
    assert downstream_calls == []


@pytest.mark.parametrize(
    ("command", "foreign_option", "foreign_value"),
    (
        pytest.param("install", "--target", "cluster-a", id="install-target"),
        pytest.param("onboard", "--release", "4.1.7", id="onboard-release"),
        pytest.param("upgrade", "--release", "4.1.7", id="upgrade-release"),
        pytest.param("destroy", "--approve", None, id="destroy-approve"),
        pytest.param("discover", "--to-release", "4.1.7", id="discover-to-release"),
        pytest.param("status", "--redaction", "support", id="status-redaction"),
    ),
)
def test_soperator_commands_reject_misrouted_options_at_the_parser(
    tmp_path: Path,
    command: str,
    foreign_option: str,
    foreign_value: str | None,
) -> None:
    argv = ["soperator", command, str(tmp_path / "config.yaml")]
    if command == "destroy":
        argv.extend(["--target", "cluster-a"])
    argv.append(foreign_option)
    if foreign_value is not None:
        argv.append(foreign_value)

    result = runner.invoke(cli.app, argv)

    output = _plain_cli_output(result.output)
    assert result.exit_code == 2
    assert "No such option" in output
    assert foreign_option in output
