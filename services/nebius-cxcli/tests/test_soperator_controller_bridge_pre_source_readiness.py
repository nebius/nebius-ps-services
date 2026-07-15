from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from nebius_cxcli import soperator_migration as migration

_DIGEST = "a" * 64


def _source() -> migration.BridgeSourceBinding:
    return migration.BridgeSourceBinding(
        namespace="soperator",
        slurmcluster_name="old-cluster",
        slurmcluster_uid="slurmcluster-uid",
        controller_workload_name="controller",
        controller_workload_uid="controller-workload-uid",
        controller_pod_name="controller-0",
        controller_pod_uid="controller-pod-uid",
        controller_pvc_name="controller-spool-controller-0",
        controller_pvc_uid="controller-pvc-uid",
        controller_pv_name="controller-pv",
        controller_pv_uid="controller-pv-uid",
        jail_pvc_name="jail-rootfs",
        jail_pvc_uid="jail-pvc-uid",
        jail_pv_name="jail-pv",
        jail_pv_uid="jail-pv-uid",
        jail_filesystem_id="filesystem-jail",
        slurm_image_digest=f"registry.example/slurmctld@sha256:{_DIGEST}",
        slurm_version="24.11.6",
        configuration_fingerprint="b" * 64,
        munge_fingerprint="c" * 64,
        jwt_fingerprint="d" * 64,
    )


def _plan() -> migration.BridgePlan:
    template = {
        "metadata": {"labels": {"customer-label": "kept"}},
        "resources": {"platform": "cpu-d3", "preset": "8vcpu-32gb"},
    }
    return migration.BridgePlan(
        campaign_fingerprint=_DIGEST,
        cluster_id="cluster-1",
        cluster_name="old-cluster",
        source_kubernetes_version="1.31",
        source_slurm_image=_source().slurm_image_digest,
        target_slurm_image=f"registry.example/slurmctld@sha256:{'e' * 64}",
        source_slurm_version="24.11.6",
        target_slurm_version="25.11.3",
        state_save_location="/mnt/controller-spool/current",
        controller_spool_attachment={
            "attach_mode": "READ_WRITE",
            "mount_tag": "controller-spool",
            "existing_filesystem": {"id": "filesystem-controller-spool"},
        },
        jail_attachment={
            "attach_mode": "READ_WRITE",
            "mount_tag": "jail",
            "existing_filesystem": {"id": "filesystem-jail"},
        },
        placement_domains=(
            migration.BridgePlacementDomain.external(
                name="cxcli-bridge-a", role="external-a", template=template
            ),
            migration.BridgePlacementDomain.external(
                name="cxcli-bridge-b", role="external-b", template=template
            ),
        ),
    )


def _journal() -> dict[str, Any]:
    journal = migration.new_bridge_journal(
        source=_source(),
        plan=_plan(),
        authority_epoch="source-epoch",
        created_at="2026-07-13T10:00:00Z",
    )
    for index, group in enumerate(journal["node_groups"]):
        group_id = f"bridge-node-group-{index}"
        group.update(
            {
                "id": group_id,
                "created": True,
                "scheduling_failure_domain": {
                    "topology_key": "nebius.com/node-group-id",
                    "node_group_id": group_id,
                },
            }
        )
    journal["kubernetes_resources"] = [
        {
            "api_version": "v1",
            "kind": "PersistentVolume",
            "namespace": "",
            "name": "cxcli-controller-bridge-state-pv",
            "uid": "bridge-state-pv-uid",
            "resource_version": "10",
        },
        {
            "api_version": "v1",
            "kind": "PersistentVolumeClaim",
            "namespace": migration.CONTROLLER_BRIDGE_NAMESPACE,
            "name": "cxcli-controller-bridge-state",
            "uid": "bridge-state-pvc-uid",
            "resource_version": "11",
        },
        {
            "api_version": "v1",
            "kind": "PersistentVolume",
            "namespace": "",
            "name": "cxcli-controller-bridge-jail-pv",
            "uid": "bridge-jail-pv-uid",
            "resource_version": "12",
        },
        {
            "api_version": "v1",
            "kind": "PersistentVolumeClaim",
            "namespace": migration.CONTROLLER_BRIDGE_NAMESPACE,
            "name": "cxcli-controller-bridge-jail",
            "uid": "bridge-jail-pvc-uid",
            "resource_version": "13",
        },
    ]
    return journal


def _result(
    args: Sequence[str],
    *,
    returncode: int = 0,
) -> migration.SoperatorMigrationCommandResult:
    return migration.SoperatorMigrationCommandResult(
        args=tuple(args),
        returncode=returncode,
        stdout="",
        stderr="canary failure" if returncode else "",
    )


class _CanaryRunner:
    def __init__(self, *, fail_exec_number: int = 0) -> None:
        self.fail_exec_number = fail_exec_number
        self.exec_count = 0

    def __call__(
        self,
        args: Sequence[str],
        **_kwargs: Any,
    ) -> migration.SoperatorMigrationCommandResult:
        selected = tuple(str(item) for item in args)
        if "exec" in selected and "canary" in selected:
            self.exec_count += 1
            return _result(
                selected,
                returncode=int(self.exec_count == self.fail_exec_number),
            )
        return _result(selected)


def test_pre_authority_source_restart_invalidates_owned_precopy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    journal = _journal()
    journal["stage"] = migration.BridgeStage.SOURCE_PRECOPIED.value
    journal["source_configuration"] = {"client_propagation": {"status": "verified"}}
    journal["state_precopy"] = {
        "archive_path": str(tmp_path / "source-epoch.precopy.tar"),
        "incremental_snapshot_path": str(tmp_path / "source-epoch.precopy.snar"),
        "remote_path": "/shared/epochs/source-epoch.precopy",
        "source_pod_uid": "controller-pod-uid",
        "transfer_token": "1" * 32,
    }
    journal["source_fence_intent"] = {
        "state": "dispatching",
        "source_pod_uid": "controller-pod-uid",
    }
    journal["fencing"]["source"].update(
        {
            "proven": False,
            "pod_uid": "controller-pod-uid",
            "workload_uid": "controller-workload-uid",
        }
    )
    journal["shared_mount_canaries"] = [
        {"purpose": migration.CONTROLLER_BRIDGE_PRE_SOURCE_FENCE_CANARY}
    ]
    for name in ("source-epoch.precopy.tar", "source-epoch.precopy.snar"):
        (tmp_path / name).write_text("stale", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def runner(
        args: Sequence[str],
        **_kwargs: Any,
    ) -> migration.SoperatorMigrationCommandResult:
        calls.append(tuple(str(item) for item in args))
        return _result(args)

    monkeypatch.setattr(migration, "validate_bridge_journal", lambda _journal: None)
    replacement = replace(_source(), controller_pod_uid="replacement-controller-pod-uid")
    checkpoints: list[dict[str, Any]] = []

    lines = migration._recover_controller_bridge_pre_authority_source_restart(  # noqa: SLF001
        checkpoint={"controller_bridge": journal},
        plan=_plan(),
        source_binding=replacement,
        source={},
        artifact_dir=tmp_path,
        kube_context="context",
        command_runner=runner,
        checkpoint_writer=lambda: checkpoints.append(copy.deepcopy(journal)),
    )

    assert journal["stage"] == migration.BridgeStage.SOURCE_CONFIGURED.value
    assert journal["source_binding"]["controller_pod_uid"] == ("replacement-controller-pod-uid")
    assert journal["state_precopy"] == {}
    assert journal["source_fence_intent"] == {}
    assert journal["source_configuration"]["client_propagation"] is None
    assert journal["shared_mount_canaries"] == []
    assert journal["source_restart_recoveries"][0]["prior_source_pod_uid"] == ("controller-pod-uid")
    assert not any(tmp_path.iterdir())
    assert len(calls) == 1 and "cxcli-controller-bridge-stager" in calls[0]
    assert checkpoints
    assert "invalidated dependent fence proofs" in lines[0]


def _promoted_restart_journal(tmp_path: Path) -> dict[str, Any]:
    journal = _journal()
    epoch = str(journal["authority"]["epoch"])
    journal["stage"] = migration.BridgeStage.STATE_PROMOTED.value
    journal["source_configuration"] = {"client_propagation": {"status": "verified"}}
    journal["state_precopy"] = {
        "archive_path": str(tmp_path / f"{epoch}.precopy.tar"),
        "incremental_snapshot_path": str(tmp_path / f"{epoch}.precopy.snar"),
        "remote_path": f"/shared/epochs/{epoch}.precopy",
        "source_pod_uid": "controller-pod-uid",
        "transfer_token": "1" * 32,
    }
    journal["source_fence_intent"] = {
        "state": "accepted",
        "source_pod_uid": "controller-pod-uid",
    }
    journal["fencing"]["source"].update(
        {
            "proven": True,
            "pod_uid": "controller-pod-uid",
            "workload_uid": "controller-workload-uid",
            "process_absent": True,
            "writable_mount_absent": True,
        }
    )
    journal["state_manifest"] = {
        "epoch_directory": f"/mnt/controller-spool/epochs/{epoch}",
        "stable_path": "/mnt/controller-spool/current",
        "sha256": "e" * 64,
        "file_count": 4,
        "fsynced": True,
        "promoted": True,
    }
    journal["cold_reader"] = {
        "authority_epoch": epoch,
        "transfer_token": "2" * 32,
        "status": "absent",
        "mount_absent": True,
        "remote_delta_applied": True,
        "remote_epoch_path": f"/shared/epochs/{epoch}",
        "delta_archive_path": str(tmp_path / f"{epoch}.cold-delta.tar"),
        "incremental_snapshot_path": str(tmp_path / f"{epoch}.cold.snar"),
        "source_manifest_path": str(tmp_path / f"{epoch}.cold-source.tree.tsv"),
    }
    journal["runtime_fence_proofs"] = [{"purpose": "source-singleton-stopped"}]
    journal["shared_mount_canaries"] = [
        {"purpose": "pre-source-mutation"},
        {"purpose": migration.CONTROLLER_BRIDGE_PRE_SOURCE_FENCE_CANARY},
    ]
    for suffix in (
        "precopy.tar",
        "precopy.snar",
        "cold-delta.tar",
        "cold.snar",
        "cold-source.tree.tsv",
    ):
        (tmp_path / f"{epoch}.{suffix}").write_text("stale", encoding="utf-8")
    return journal


def _replacement_source(replacement: migration.BridgeSourceBinding) -> dict[str, Any]:
    return {
        "controller_workload": {
            "api_version": "apps.kruise.io/v1beta1",
            "kind": "StatefulSet",
            "namespace": replacement.namespace,
            "name": replacement.controller_workload_name,
            "uid": replacement.controller_workload_uid,
        },
        "controller_pod": {
            "namespace": replacement.namespace,
            "name": replacement.controller_pod_name,
            "uid": replacement.controller_pod_uid,
        },
    }


def test_pre_authority_source_restart_discards_stale_promoted_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    journal = _promoted_restart_journal(tmp_path)
    replacement = replace(_source(), controller_pod_uid="replacement-controller-pod-uid")
    proof_calls: list[dict[str, Any]] = []
    commands: list[tuple[str, ...]] = []
    checkpoints: list[dict[str, Any]] = []

    def prove(**kwargs: Any) -> list[dict[str, Any]]:
        proof_calls.append(kwargs)
        return [{"status": "verified", "slurmctld_count": 1}]

    def runner(
        args: Sequence[str],
        **_kwargs: Any,
    ) -> migration.SoperatorMigrationCommandResult:
        commands.append(tuple(str(item) for item in args))
        return _result(args)

    monkeypatch.setattr(migration, "validate_bridge_journal", lambda _journal: None)
    monkeypatch.setattr(migration, "_prove_cluster_wide_slurmctld_exclusivity", prove)

    lines = migration._recover_controller_bridge_pre_authority_source_restart(  # noqa: SLF001
        checkpoint={"controller_bridge": journal},
        plan=_plan(),
        source_binding=replacement,
        source=_replacement_source(replacement),
        artifact_dir=tmp_path,
        kube_context="context",
        command_runner=runner,
        checkpoint_writer=lambda: checkpoints.append(copy.deepcopy(journal)),
    )

    assert proof_calls[0]["expected_pods"] == (
        ("soperator", "controller-0", "replacement-controller-pod-uid"),
    )
    assert proof_calls[0]["expected_workload"] == (
        "statefulsets.apps.kruise.io",
        "soperator",
        "controller",
        "controller-workload-uid",
    )
    assert len(commands) == 2
    assert "test -L /shared/current" in commands[0][-1]
    assert "rm -f -- /shared/current" in commands[1][-1]
    assert "rm -rf -- /shared/epochs/source-epoch" in commands[1][-1]
    assert journal["stage"] == migration.BridgeStage.SOURCE_CONFIGURED.value
    assert journal["source_binding"]["controller_pod_uid"] == ("replacement-controller-pod-uid")
    assert journal["state_precopy"] == {}
    assert journal["cold_reader"] == {}
    assert journal["runtime_fence_proofs"] == []
    assert journal["source_fence_intent"] == {}
    assert journal["fencing"]["source"]["proven"] is False
    assert journal["source_configuration"]["client_propagation"] is None
    assert journal["shared_mount_canaries"] == [{"purpose": "pre-source-mutation"}]
    assert journal["source_restart_recoveries"][-1]["reason"] == (
        "operator-restored-source-after-state-promotion-before-first-bridge-write"
    )
    assert "source_restart_recovery_intent" not in journal
    assert not any(tmp_path.iterdir())
    assert len(checkpoints) == 2
    assert "sole live controller" in lines[0]


def test_promoted_state_restart_cleanup_resumes_after_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    journal = _promoted_restart_journal(tmp_path)
    replacement = replace(_source(), controller_pod_uid="replacement-controller-pod-uid")
    commands: list[tuple[str, ...]] = []

    def runner(
        args: Sequence[str],
        **_kwargs: Any,
    ) -> migration.SoperatorMigrationCommandResult:
        commands.append(tuple(str(item) for item in args))
        if len(commands) == 2:
            raise RuntimeError("simulated cleanup interruption")
        return _result(args)

    monkeypatch.setattr(migration, "validate_bridge_journal", lambda _journal: None)
    monkeypatch.setattr(
        migration,
        "_prove_cluster_wide_slurmctld_exclusivity",
        lambda **_kwargs: [{"status": "verified"}],
    )

    kwargs = {
        "checkpoint": {"controller_bridge": journal},
        "plan": _plan(),
        "source_binding": replacement,
        "source": _replacement_source(replacement),
        "artifact_dir": tmp_path,
        "kube_context": "context",
        "command_runner": runner,
        "checkpoint_writer": lambda: None,
    }
    with pytest.raises(RuntimeError, match="simulated cleanup interruption"):
        migration._recover_controller_bridge_pre_authority_source_restart(  # noqa: SLF001
            **kwargs
        )

    assert journal["source_restart_recovery_intent"]["state"] == "cleanup-dispatching"
    migration._recover_controller_bridge_pre_authority_source_restart(  # noqa: SLF001
        **kwargs
    )
    assert len(commands) == 3
    assert journal["stage"] == migration.BridgeStage.SOURCE_CONFIGURED.value


def test_promoted_state_restart_recovery_refuses_after_bridge_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    journal = _promoted_restart_journal(tmp_path)
    journal["authority"]["first_bridge_write_at"] = "2026-07-14T12:00:00Z"
    replacement = replace(_source(), controller_pod_uid="replacement-controller-pod-uid")
    monkeypatch.setattr(migration, "validate_bridge_journal", lambda _journal: None)
    monkeypatch.setattr(
        migration,
        "_prove_cluster_wide_slurmctld_exclusivity",
        lambda **_kwargs: pytest.fail("unsafe recovery must not prove or mutate live state"),
    )

    assert (
        migration._recover_controller_bridge_pre_authority_source_restart(  # noqa: SLF001
            checkpoint={"controller_bridge": journal},
            plan=_plan(),
            source_binding=replacement,
            source=_replacement_source(replacement),
            artifact_dir=tmp_path,
            kube_context="context",
            command_runner=lambda *_args, **_kwargs: pytest.fail(
                "unsafe recovery must not run commands"
            ),
            checkpoint_writer=None,
        )
        == []
    )


def test_checkpoint_source_binding_uses_validated_dispatch_without_source_pod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = _journal()
    journal["stage"] = migration.BridgeStage.SOURCE_PRECOPIED.value
    journal["source_fence_intent"] = {
        "state": "dispatching",
        "source_workload_uid": "controller-workload-uid",
        "source_pod_uid": "controller-pod-uid",
    }
    journal["fencing"]["source"].update(
        {
            "proven": False,
            "workload_uid": "controller-workload-uid",
            "pod_uid": "controller-pod-uid",
            "runtime_target": {
                "node_name": "controller-node",
                "node_uid": "controller-node-uid",
                "state_markers": ["controller-pvc-uid"],
            },
        }
    )
    monkeypatch.setattr(migration, "validate_bridge_journal", lambda _journal: None)

    recovered = migration._controller_bridge_checkpoint_source_binding(  # noqa: SLF001
        {"controller_bridge": journal}
    )

    assert recovered is not None
    binding, source = recovered
    assert binding == _source()
    assert source["controller_workload"] == {
        "name": "controller",
        "uid": "controller-workload-uid",
    }
    assert source["controller_pod"] == {
        "name": "controller-0",
        "uid": "controller-pod-uid",
        "node_name": "controller-node",
    }

    journal["stage"] = migration.BridgeStage.SOURCE_FENCED.value
    journal["source_fence_intent"]["state"] = "accepted"
    journal["fencing"]["source"].update(
        {
            "proven": True,
            "process_absent": True,
            "writable_mount_absent": True,
        }
    )
    assert (
        migration._controller_bridge_checkpoint_source_binding(  # noqa: SLF001
            {"controller_bridge": journal}
        )
        is not None
    )


def _canary_json(args: Sequence[str]) -> Mapping[str, Any]:
    selected = tuple(str(item) for item in args)
    if "pod" in selected:
        pod_name = selected[selected.index("pod") + 1]
        slot = int(pod_name.rsplit("-", 1)[-1])
        return {
            "metadata": {"name": pod_name, "uid": f"canary-pod-uid-{slot}"},
            "spec": {"nodeName": f"bridge-node-{slot}"},
        }
    if "node" in selected:
        node_name = selected[selected.index("node") + 1]
        slot = int(node_name.rsplit("-", 1)[-1])
        return {
            "metadata": {
                "name": node_name,
                "uid": f"bridge-node-uid-{slot}",
                "labels": {
                    "nebius.com/node-group-id": f"bridge-node-group-{slot}",
                    "topology.kubernetes.io/zone": (f"eu-north1-{chr(ord('a') + slot)}"),
                },
            }
        }
    pytest.fail(f"unexpected canary JSON command: {selected}")


def _install_canary_runtime_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(migration, "_kubectl_apply_objects", lambda **_kwargs: None)
    monkeypatch.setattr(migration, "_kubectl_wait", lambda **_kwargs: None)
    monkeypatch.setattr(
        migration,
        "_delete_owned_controller_bridge_canary",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda _runner, args, **_kwargs: _canary_json(args),
    )


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ("Ready", "--for=condition=Ready"),
        ("condition=Complete", "--for=condition=Complete"),
        ("delete", "--for=delete"),
        ("jsonpath={.status.phase}=Succeeded", "--for=jsonpath={.status.phase}=Succeeded"),
    ],
)
def test_kubectl_wait_builds_supported_wait_selector(
    condition: str,
    expected: str,
) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(args: Sequence[str], **_kwargs: Any) -> migration.SoperatorMigrationCommandResult:
        calls.append(tuple(str(item) for item in args))
        return _result(args)

    migration._kubectl_wait(  # noqa: SLF001
        command_runner=runner,
        kube_context="context",
        namespace="namespace",
        resource="pod/example",
        condition=condition,
        timeout="10m",
        timeout_seconds=660,
    )

    assert calls == [
        (
            "kubectl",
            "--context",
            "context",
            "-n",
            "namespace",
            "wait",
            expected,
            "pod/example",
            "--timeout=10m",
        )
    ]


def _bind_bridge_nodes(journal: dict[str, Any]) -> None:
    for slot, group in enumerate(journal["node_groups"]):
        group["scheduling_failure_domain"].update(
            {
                "node_name": f"bridge-node-{slot}",
                "node_uid": f"bridge-node-uid-{slot}",
                "topology_value": str(group["id"]),
            }
        )


def _live_bridge_node_groups(plan: migration.BridgePlan) -> dict[str, dict[str, Any]]:
    live: dict[str, dict[str, Any]] = {}
    for slot, payload in enumerate(migration.bridge_node_group_payloads(plan)):
        item = copy.deepcopy(payload)
        metadata = item["metadata"]
        node_group_id = f"bridge-node-group-{slot}"
        metadata.update({"id": node_group_id, "resource_version": 17 + slot})
        live[node_group_id] = item
    return live


def test_pre_source_canary_pod_is_tokenless_and_runtime_hardened() -> None:
    pod = migration._bridge_mount_canary_pod(  # noqa: SLF001
        namespace=migration.CONTROLLER_BRIDGE_NAMESPACE,
        slot=0,
        image=_plan().source_slurm_image,
        campaign_fingerprint=_plan().campaign_fingerprint,
        cluster_id=_plan().cluster_id,
    )
    spec = pod["spec"]
    security = spec["containers"][0]["securityContext"]

    assert spec["automountServiceAccountToken"] is False
    assert spec["enableServiceLinks"] is False
    assert spec["activeDeadlineSeconds"] == 900
    assert spec["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert security["allowPrivilegeEscalation"] is False
    assert security["capabilities"]["drop"] == ["ALL"]
    assert security["readOnlyRootFilesystem"] is True


def test_pre_source_canary_is_idempotently_replaced_on_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = _journal()
    _install_canary_runtime_stubs(monkeypatch)
    checkpoints: list[dict[str, Any]] = []

    for _attempt in range(2):
        migration._prove_controller_bridge_shared_mount(  # noqa: SLF001
            journal=journal,
            image=_plan().source_slurm_image,
            purpose=migration.CONTROLLER_BRIDGE_PRE_SOURCE_MUTATION_CANARY,
            kube_context="context",
            command_runner=_CanaryRunner(),
            checkpoint_writer=lambda: checkpoints.append(copy.deepcopy(journal)),
        )

    assert len(journal["shared_mount_canaries"]) == 1
    proof = migration.pre_source_mutation_mount_canary(journal)
    assert proof["bidirectional"] is True
    assert {pod["failure_domain"] for pod in proof["pods"]} == {
        "bridge-node-group-0",
        "bridge-node-group-1",
    }
    assert len(checkpoints) == 2


def test_partial_scheduling_domain_checkpoint_is_repaired_from_live_canary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = _journal()
    _install_canary_runtime_stubs(monkeypatch)
    runner = _CanaryRunner()
    migration._prove_controller_bridge_shared_mount(  # noqa: SLF001
        journal=journal,
        image=_plan().source_slurm_image,
        purpose=migration.CONTROLLER_BRIDGE_PRE_SOURCE_MUTATION_CANARY,
        kube_context="context",
        command_runner=runner,
        checkpoint_writer=lambda: None,
    )
    group = journal["node_groups"][0]
    group["scheduling_failure_domain"] = {
        "topology_key": "nebius.com/node-group-id",
        "node_group_id": group["id"],
    }
    checkpoints: list[dict[str, Any]] = []

    migration._reconcile_controller_bridge_scheduling_domains_from_live_canary(  # noqa: SLF001
        journal=journal,
        kube_context="context",
        command_runner=runner,
        checkpoint_writer=lambda: checkpoints.append(copy.deepcopy(journal)),
    )

    assert len(checkpoints) == 1
    assert migration.pre_source_mutation_mount_canary(journal)["bidirectional"] is True
    assert group["scheduling_failure_domain"] == {
        "topology_key": "nebius.com/node-group-id",
        "topology_value": "bridge-node-group-0",
        "node_group_id": "bridge-node-group-0",
        "node_name": "bridge-node-0",
        "node_uid": "bridge-node-uid-0",
    }


def test_pre_source_canary_failure_leaves_singleton_authoritative_and_unfenced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = _journal()
    _install_canary_runtime_stubs(monkeypatch)

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="bidirectional two-node read/write canary",
    ):
        migration._prove_controller_bridge_shared_mount(  # noqa: SLF001
            journal=journal,
            image=_plan().source_slurm_image,
            purpose=migration.CONTROLLER_BRIDGE_PRE_SOURCE_MUTATION_CANARY,
            kube_context="context",
            command_runner=_CanaryRunner(fail_exec_number=2),
            checkpoint_writer=lambda: None,
        )

    assert journal["stage"] == migration.BridgeStage.PLANNED.value
    assert journal["shared_mount_canaries"] == []
    assert journal["source_configuration"] == {}
    assert journal["authority"]["owner"] == "source-singleton"
    assert journal["authority"]["first_bridge_write_at"] == ""
    assert journal["authority"]["source_restart_prohibited"] is False
    assert journal["fencing"]["source"]["proven"] is False
    assert journal["state_manifest"]["promoted"] is False


def test_execute_stops_before_source_mutators_when_substrate_canary_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    journal = _journal()
    plan = _plan()
    checkpoint: dict[str, Any] = {"phase_state": {}}
    calls: list[str] = []

    monkeypatch.setattr(
        migration,
        "_controller_bridge_plan",
        lambda **_kwargs: (plan, _source(), {}, {}),
    )
    monkeypatch.setattr(
        migration,
        "_controller_bridge_journal",
        lambda **_kwargs: journal,
    )
    monkeypatch.setattr(
        migration,
        "_preflight_controller_inspector_namespace",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(migration, "ensure_slurm_action_journal", lambda _state: {})
    monkeypatch.setattr(migration, "_protect_upgrade_login_sessions", lambda **_kwargs: [])
    monkeypatch.setattr(migration, "_bridge_pause_all_partitions", lambda **_kwargs: [])
    monkeypatch.setattr(
        migration,
        "_capture_controller_bridge_preservation_jobs",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        migration,
        "_create_controller_bridge_node_groups",
        lambda **_kwargs: (False, []),
    )
    monkeypatch.setattr(
        migration,
        "_create_controller_bridge_kubernetes_substrate",
        lambda **_kwargs: calls.append("substrate") or [],
    )

    def fail_readiness(**_kwargs: Any) -> list[str]:
        calls.append("canary")
        raise migration.SoperatorMigrationPhasePending("shared-SFS canary failed")

    monkeypatch.setattr(
        migration,
        "_establish_controller_bridge_pre_source_mutation_readiness",
        fail_readiness,
    )
    for name in (
        "_suspend_source_reconciliation_for_bridge",
        "_configure_source_controller_for_bridge",
        "_fence_source_controller_for_bridge",
        "_cold_copy_and_promote_controller_state",
    ):
        monkeypatch.setattr(
            migration,
            name,
            lambda _name=name, **_kwargs: calls.append(_name) or [],
        )

    with pytest.raises(migration.SoperatorMigrationPhasePending, match="shared-SFS canary"):
        migration._execute_controller_ha_bridge_phase(  # noqa: SLF001
            checkpoint=checkpoint,
            payload={},
            source_report={},
            target_ref="target",
            kube_context="context",
            nebius_api=object(),  # type: ignore[arg-type]
            command_runner=_CanaryRunner(),
            artifact_dir=tmp_path,
            checkpoint_writer=lambda: None,
        )

    assert calls == ["substrate", "canary"]


def test_reused_bridge_groups_preserve_proven_runtime_scheduling_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = _journal()
    plan = _plan()
    expected_domains: list[dict[str, str]] = []
    provider_groups: list[dict[str, Any]] = []
    for index, record in enumerate(journal["node_groups"]):
        group_id = str(record["id"])
        domain = {
            "topology_key": "nebius.com/node-group-id",
            "topology_value": group_id,
            "node_group_id": group_id,
            "node_name": f"bridge-node-{index}",
            "node_uid": f"bridge-node-uid-{index}",
        }
        record["scheduling_failure_domain"] = domain
        expected_domains.append(copy.deepcopy(domain))
        provider_groups.append(
            {"metadata": {"id": group_id, "name": plan.placement_domains[index].name}}
        )

    monkeypatch.setattr(
        migration,
        "_list_node_groups",
        lambda **_kwargs: provider_groups,
    )
    monkeypatch.setattr(
        migration,
        "_node_group_payload_by_id",
        lambda **kwargs: next(
            item for item in provider_groups if item["metadata"]["id"] == kwargs["node_group_id"]
        ),
    )
    monkeypatch.setattr(
        migration,
        "_validate_reused_controller_bridge_quota_group",
        lambda **kwargs: str(kwargs["node_group"]["metadata"]["id"]),
    )
    checkpoint_calls: list[str] = []

    mutated, lines = migration._create_controller_bridge_node_groups(  # noqa: SLF001
        checkpoint={"phase_state": {}},
        journal=journal,
        plan=plan,
        nebius_api=object(),  # type: ignore[arg-type]
        checkpoint_writer=lambda: checkpoint_calls.append("checkpoint"),
    )

    assert mutated is False
    assert len(lines) == 2
    assert checkpoint_calls == []
    assert [record["scheduling_failure_domain"] for record in journal["node_groups"]] == (
        expected_domains
    )
    assert journal["authority"]["owner"] == "source-singleton"
    assert journal["fencing"]["source"]["proven"] is False


def test_managed_bridge_binds_existing_domains_without_provider_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = replace(
        _plan(),
        placement_domains=(
            migration.BridgePlacementDomain.managed(
                name="controller-live",
                role="controller",
                node_group_id="group-controller",
                selector={"nebius.ai/soperator-bridge-domain": "controller"},
                live_node_uid="controller-node-uid",
                ready_capacity=2,
            ),
            migration.BridgePlacementDomain.managed(
                name="system-live",
                role="system",
                node_group_id="group-system",
                selector={"nebius.ai/soperator-bridge-domain": "system"},
                live_node_uid="system-node-uid",
                ready_capacity=3,
            ),
        ),
    )
    journal = migration.new_bridge_journal(
        source=_source(),
        plan=plan,
        authority_epoch="source-epoch",
        created_at="2026-07-13T10:00:00Z",
    )
    live_groups = {
        domain.node_group_id: {
            "metadata": {
                "id": domain.node_group_id,
                "name": domain.name,
                "resource_version": 17,
            },
            "spec": {
                "version": "1.31",
                "fixed_node_count": domain.ready_capacity,
            },
        }
        for domain in plan.placement_domains
    }
    monkeypatch.setattr(
        migration,
        "_node_group_payload_by_id",
        lambda **kwargs: live_groups[kwargs["node_group_id"]],
    )

    class _NoCreateApi:
        def create_node_group(self, **_kwargs: Any) -> Mapping[str, Any]:
            raise AssertionError("managed bridge must not create provider node groups")

    checkpoints: list[str] = []
    mutated, lines = migration._create_controller_bridge_node_groups(  # noqa: SLF001
        checkpoint={"phase_state": {}},
        journal=journal,
        plan=plan,
        nebius_api=_NoCreateApi(),  # type: ignore[arg-type]
        checkpoint_writer=lambda: checkpoints.append("checkpoint"),
    )

    assert mutated is False
    assert len(lines) == 2
    assert checkpoints == ["checkpoint"]
    assert all(record["bound"] is True for record in journal["node_groups"])
    assert all(record["created"] is False for record in journal["node_groups"])


@pytest.mark.parametrize("intent_state", ["intent-recorded", "dispatching"])
def test_stale_pre_fence_proof_is_rerun_before_source_scale(
    monkeypatch: pytest.MonkeyPatch,
    intent_state: str,
) -> None:
    journal = _journal()
    journal["stage"] = migration.BridgeStage.SOURCE_PRECOPIED.value
    journal["source_configuration"] = {
        "client_config_contract": {"controller_hosts": []},
        "client_propagation": {
            "status": "verified",
            "live_rpc_verified": True,
            "proof_stage": "before-source-fence",
        },
    }
    journal["fencing"]["source"]["runtime_target"] = {
        "node_name": "source-controller-node",
        "node_uid": "source-controller-node-uid",
        "state_markers": ["controller-pvc-uid"],
    }
    stale_canary = {
        "purpose": migration.CONTROLLER_BRIDGE_PRE_SOURCE_FENCE_CANARY,
        "observed_at": "2026-07-13T10:01:00Z",
    }
    journal["shared_mount_canaries"].append(stale_canary)
    journal["pre_source_fence_readiness"] = {
        "status": "verified",
        "node_groups_sha256": migration._fingerprint([]),  # noqa: SLF001
        "verified_at": "2026-07-13T10:01:00Z",
    }
    journal["source_fence_intent"] = {
        "state": intent_state,
        "intent_at": "2026-07-13T10:01:01Z",
    }
    source = {
        "controller_workload": {
            "name": "controller",
            "uid": "controller-workload-uid",
        },
        "controller_pod": {"name": "controller-0", "uid": "controller-pod-uid"},
    }
    canary_attempts: list[str] = []
    scale_attempts: list[int] = []

    monkeypatch.setattr(migration, "pre_source_mutation_mount_canary", lambda _journal: {})
    monkeypatch.setattr(migration, "validate_bridge_journal", lambda _journal: None)
    monkeypatch.setattr(
        migration,
        "_suspend_source_reconciliation_for_bridge",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        migration,
        "_prove_controller_bridge_client_configuration",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        migration,
        "_reassert_controller_bridge_partition_pause_after_reconfigure",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda _runner, _args, **_kwargs: {
            "metadata": {
                "uid": "controller-workload-uid",
                "resourceVersion": "31",
            },
            "spec": {"replicas": 1},
        },
    )
    monkeypatch.setattr(
        migration,
        "_source_controller_pre_fence_live_binding",
        lambda **_kwargs: (
            {
                "metadata": {
                    "uid": "controller-workload-uid",
                    "resourceVersion": "31",
                },
                "spec": {"replicas": 1},
            },
            {"metadata": {"uid": "controller-pod-uid"}},
        ),
    )
    monkeypatch.setattr(
        migration,
        "_controller_bridge_pre_source_fence_node_groups",
        lambda **_kwargs: [],
    )

    def fail_fresh_canary(**kwargs: Any) -> None:
        canary_attempts.append(str(kwargs["purpose"]))
        raise migration.SoperatorMigrationPhasePending("fresh pre-fence canary failed")

    monkeypatch.setattr(migration, "_prove_controller_bridge_shared_mount", fail_fresh_canary)
    monkeypatch.setattr(
        migration,
        "_kubectl_scale_namespace_resource",
        lambda **kwargs: scale_attempts.append(int(kwargs["replicas"])),
    )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="fresh pre-fence canary failed",
    ):
        migration._fence_source_controller_for_bridge(  # noqa: SLF001
            checkpoint={},
            journal=journal,
            plan=_plan(),
            source_report={},
            source=source,
            target_ref="target",
            target_version="4.0.2-ps.4",
            kube_context="context",
            nebius_api=object(),  # type: ignore[arg-type]
            command_runner=_CanaryRunner(),
            checkpoint_writer=lambda: None,
        )

    assert canary_attempts == [migration.CONTROLLER_BRIDGE_PRE_SOURCE_FENCE_CANARY]
    assert scale_attempts == []
    assert journal["source_fence_intent"]["state"] == intent_state
    assert "action_broker_mode" not in journal
    assert journal["authority"]["owner"] == "source-singleton"
    assert journal["fencing"]["source"]["proven"] is False


def test_dispatching_source_at_zero_reuses_durable_rpc_and_pause_proofs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = _journal()
    journal["stage"] = migration.BridgeStage.SOURCE_PRECOPIED.value
    contract = {
        "config_sha256": "a" * 64,
        "cluster_name": journal["cluster_name"],
        "controller_hosts": ["controller", "bridge-a", "bridge-b"],
        "timeouts": {"SlurmdTimeout": "3600", "SlurmctldTimeout": "3600"},
    }
    journal["source_configuration"] = {
        "client_config_contract": contract,
        "client_propagation": {
            "status": "verified",
            "live_rpc_verified": True,
            "proof_stage": "before-source-fence",
            "config_sha256": contract["config_sha256"],
            "controller_hosts": contract["controller_hosts"],
            "timeouts": contract["timeouts"],
        },
    }
    journal["fencing"]["source"].update(
        {
            "proven": False,
            "runtime_target": {
                "node_name": "source-controller-node",
                "node_uid": "source-controller-node-uid",
                "state_markers": ["controller-pvc-uid"],
            },
        }
    )
    authority_epoch = journal["authority"]["epoch"]
    journal["source_fence_intent"] = {
        "state": "dispatching",
        "authority_epoch": authority_epoch,
        "source_workload_uid": "controller-workload-uid",
        "source_pod_uid": "controller-pod-uid",
        "dispatching_at": "2026-07-14T08:52:39Z",
    }
    source = {
        "controller_workload": {
            "name": "controller",
            "uid": "controller-workload-uid",
        },
        "controller_pod": {"name": "controller-0", "uid": "controller-pod-uid"},
    }

    monkeypatch.setattr(migration, "pre_source_mutation_mount_canary", lambda _journal: {})
    monkeypatch.setattr(migration, "validate_bridge_journal", lambda _journal: None)
    monkeypatch.setattr(
        migration,
        "_suspend_source_reconciliation_for_bridge",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda _runner, _args, **_kwargs: {
            "metadata": {"uid": "controller-workload-uid", "resourceVersion": "32"},
            "spec": {"replicas": 0},
        },
    )
    monkeypatch.setattr(
        migration,
        "_prove_controller_bridge_client_configuration",
        lambda **_kwargs: pytest.fail("source-at-zero must not issue live Slurm RPCs"),
    )
    monkeypatch.setattr(
        migration,
        "_reassert_controller_bridge_partition_pause_after_reconfigure",
        lambda **_kwargs: pytest.fail("source-at-zero must not reassert through Slurm RPC"),
    )

    def stop_after_resume_proof(**_kwargs: Any) -> None:
        raise migration.SoperatorMigrationPhasePending("resume reached fence reconciliation")

    monkeypatch.setattr(
        migration,
        "_prepare_controller_bridge_source_fence",
        stop_after_resume_proof,
    )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="resume reached fence reconciliation",
    ):
        migration._fence_source_controller_for_bridge(  # noqa: SLF001
            checkpoint={},
            journal=journal,
            plan=_plan(),
            source_report={},
            source=source,
            target_ref="target",
            target_version="4.0.2-ps.4",
            kube_context="context",
            nebius_api=object(),  # type: ignore[arg-type]
            command_runner=_CanaryRunner(),
            checkpoint_writer=lambda: None,
        )


def test_pre_fence_node_recreation_is_rejected_before_fence_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = _journal()
    journal["stage"] = migration.BridgeStage.SOURCE_PRECOPIED.value
    _bind_bridge_nodes(journal)
    live_groups = _live_bridge_node_groups(_plan())
    monkeypatch.setattr(
        migration,
        "_node_group_payload_by_id",
        lambda *, node_group_id, **_kwargs: live_groups[node_group_id],
    )

    def recreated_node(
        _runner: migration.SoperatorMigrationCommandRunner,
        args: Sequence[str],
        **_kwargs: Any,
    ) -> Mapping[str, Any]:
        node_name = args[args.index("node") + 1]
        slot = int(node_name.rsplit("-", 1)[-1])
        return {
            "metadata": {
                "uid": f"recreated-bridge-node-uid-{slot}",
                "labels": {
                    "nebius.com/node-group-id": f"bridge-node-group-{slot}",
                    "topology.kubernetes.io/zone": (f"eu-north1-{chr(ord('a') + slot)}"),
                },
            }
        }

    monkeypatch.setattr(migration, "_json_from_command", recreated_node)

    with pytest.raises(RuntimeError, match="Node UID, node-group, or scheduling-domain"):
        migration._controller_bridge_pre_source_fence_node_groups(  # noqa: SLF001
            journal=journal,
            plan=_plan(),
            nebius_api=object(),  # type: ignore[arg-type]
            kube_context="context",
            command_runner=_CanaryRunner(),
        )

    assert journal["source_fence_intent"] == {}
    assert journal["authority"]["owner"] == "source-singleton"
    assert journal["fencing"]["source"]["proven"] is False


def test_pre_fence_attachment_loss_is_rejected_before_node_or_source_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = _journal()
    journal["stage"] = migration.BridgeStage.SOURCE_PRECOPIED.value
    _bind_bridge_nodes(journal)
    live_groups = _live_bridge_node_groups(_plan())
    first_template = live_groups["bridge-node-group-0"]["spec"]["template"]
    first_template["filesystems"] = [
        item
        for item in first_template["filesystems"]
        if item.get("mount_tag") != "controller-spool"
    ]
    monkeypatch.setattr(
        migration,
        "_node_group_payload_by_id",
        lambda *, node_group_id, **_kwargs: live_groups[node_group_id],
    )
    node_reads: list[Sequence[str]] = []

    def unexpected_node_read(
        _runner: migration.SoperatorMigrationCommandRunner,
        args: Sequence[str],
        **_kwargs: Any,
    ) -> Mapping[str, Any]:
        node_reads.append(args)
        pytest.fail("attachment loss must fail before reading or mutating a Node")

    monkeypatch.setattr(migration, "_json_from_command", unexpected_node_read)

    with pytest.raises(RuntimeError, match="filesystem attachments differ"):
        migration._controller_bridge_pre_source_fence_node_groups(  # noqa: SLF001
            journal=journal,
            plan=_plan(),
            nebius_api=object(),  # type: ignore[arg-type]
            kube_context="context",
            command_runner=_CanaryRunner(),
        )

    assert node_reads == []
    assert journal["source_fence_intent"] == {}
    assert journal["authority"]["owner"] == "source-singleton"
    assert journal["fencing"]["source"]["proven"] is False
