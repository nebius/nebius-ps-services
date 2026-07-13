from __future__ import annotations

import copy
import hashlib
import inspect
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from nebius_cxcli import soperator_migration as migration
from nebius_cxcli.soperator_controller_bridge import (
    CONTROLLER_BRIDGE_LABEL,
    BridgeStage,
)

_DIGEST = "a" * 64


def _result(
    args: Sequence[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> migration.SoperatorMigrationCommandResult:
    return migration.SoperatorMigrationCommandResult(
        args=tuple(args),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _source() -> dict[str, Any]:
    return {
        "slurmcluster": {"namespace": "soperator"},
        "controller_pod": {
            "name": "controller-0",
            "uid": "source-controller-pod-uid",
            "node_name": "source-controller-node",
            "container_name": "slurmctld",
        },
        "controller_pvc": {
            "name": "controller-spool-controller-0",
            "uid": "source-controller-pvc-uid",
        },
    }


def _bridge_node_groups() -> list[dict[str, object]]:
    return [
        {
            "scheduling_failure_domain": {
                "node_name": f"bridge-node-{slot}",
                "node_uid": f"bridge-node-uid-{slot}",
            }
        }
        for slot in (0, 1)
    ]


def test_cold_reader_is_exact_source_image_read_only_and_bridge_owned() -> None:
    image = f"registry.example/slurmctld@sha256:{_DIGEST}"

    pod = migration._bridge_cold_reader_pod(  # noqa: SLF001
        source=_source(),
        image=image,
        authority_epoch="source-epoch-1",
    )

    metadata = pod["metadata"]
    spec = pod["spec"]
    reader = spec["containers"][0]
    source_volume = spec["volumes"][0]
    assert metadata["labels"][CONTROLLER_BRIDGE_LABEL] == "true"
    assert metadata["labels"]["nebius.ai/cxcli-controller-bridge-role"] == "cold-reader"
    assert metadata["annotations"]["nebius.ai/cxcli-source-pvc-uid"] == (
        "source-controller-pvc-uid"
    )
    assert spec["nodeName"] == "source-controller-node"
    assert reader["image"] == image
    assert reader["volumeMounts"][0]["readOnly"] is True
    assert source_volume["persistentVolumeClaim"] == {
        "claimName": "controller-spool-controller-0",
        "readOnly": True,
    }
    assert spec["volumes"][1] == {"name": "work", "emptyDir": {}}


def test_incremental_preflight_requires_gnu_tar_metadata_and_reflink_contract() -> None:
    script = migration._bridge_incremental_tool_preflight_script(root="/source")  # noqa: SLF001

    for required in (
        "GNU tar",
        "--listed-incremental",
        "--numeric-owner",
        "--xattrs",
        "--acls",
        "--reflink",
    ):
        assert required in script


def test_incomplete_local_capture_artifacts_are_retried_as_one_owned_set(
    tmp_path: Path,
) -> None:
    first = tmp_path / "capture.tar"
    second = tmp_path / "capture.snar"
    first.write_bytes(b"partial")

    migration._reset_incomplete_bridge_artifacts((first, second))  # noqa: SLF001

    assert not first.exists()
    assert not second.exists()


def test_remote_capture_retries_use_operation_tokens_and_owned_cleanup() -> None:
    precopy = inspect.getsource(migration._precopy_controller_state_to_bridge)  # noqa: SLF001
    cold_copy = inspect.getsource(  # noqa: SLF001
        migration._cold_copy_and_promote_controller_state
    )

    assert "transfer_token" in precopy
    assert 'rm -f -- "$archive" "$snapshot" "$complete"' in precopy
    assert "delta_token" in cold_copy
    assert 'rm -f -- "$delta" "$snapshot"' in cold_copy


def test_cold_delta_promotion_never_deletes_shared_current_or_live_epoch() -> None:
    source = inspect.getsource(migration._bridge_apply_cold_delta)  # noqa: SLF001

    assert "rm " not in source
    assert 'destination = f"/shared/epochs/{authority_epoch}"' in source
    assert 'precopy = f"/shared/epochs/{authority_epoch}.precopy"' in source
    assert 'build = f"/shared/.incoming-' in source
    assert "/shared/current" not in source


def test_target_version_bridge_uses_the_journaled_source_state_mount() -> None:
    journal = {
        "cluster_name": "cluster",
        "state_precopy": {"source_state_save_location": "/var/spool/slurmctld"},
    }

    def runner(
        args: Sequence[str],
        *,
        check: bool = True,
        input_text: str | None = None,
        timeout_seconds: int | None = None,
    ) -> migration.SoperatorMigrationCommandResult:
        del check, input_text, timeout_seconds
        return _result(
            args,
            stdout=(
                '{"data":{"slurm.conf":"ClusterName=cluster\\n'
                'SlurmctldHost=controller-0\\nStateSaveLocation=/mnt/controller-spool/current\\n"}}'
            ),
        )

    name, rendered = migration._controller_bridge_target_config(  # noqa: SLF001
        journal=journal,
        values={"clusterName": "cluster"},
        target_ref="cluster",
        kube_context="ctx",
        command_runner=runner,
    )

    assert name == "cluster-slurm-configs"
    assert "StateSaveLocation=/var/spool/slurmctld" in rendered
    assert "StateSaveLocation=/mnt/controller-spool/current" not in rendered


def test_cold_reader_replacement_uid_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source()
    image = f"registry.example/slurmctld@sha256:{_DIGEST}"
    journal = {
        "cold_reader": {
            "pod_uid": "journaled-reader-uid",
            "authority_epoch": "source-epoch-1",
        }
    }
    replacement = migration._bridge_cold_reader_pod(  # noqa: SLF001
        source=source,
        image=image,
        authority_epoch="source-epoch-1",
    )
    replacement["metadata"]["uid"] = "replacement-reader-uid"
    replacement["metadata"]["resourceVersion"] = "12"

    monkeypatch.setattr(
        migration,
        "_kubectl_get_namespace_resource",
        lambda **_kwargs: (True, replacement),
    )

    with pytest.raises(RuntimeError, match="Pod UID changed"):
        migration._delete_exact_bridge_cold_reader(  # noqa: SLF001
            journal=journal,
            source=source,
            image=image,
            kube_context="context",
            command_runner=lambda args, **_kwargs: _result(args),
            checkpoint_writer=None,
        )


def test_cold_reader_absence_is_checkpointed_before_writer_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source()
    image = f"registry.example/slurmctld@sha256:{_DIGEST}"
    record = {
        "pod_uid": "journaled-reader-uid",
        "authority_epoch": "source-epoch-1",
    }
    journal = {"cold_reader": record}
    checkpoints: list[str] = []
    monkeypatch.setattr(
        migration,
        "_kubectl_get_namespace_resource",
        lambda **_kwargs: (False, {}),
    )
    monkeypatch.setattr(migration, "_bridge_source_pvc_mounts", lambda **_kwargs: ())

    migration._delete_exact_bridge_cold_reader(  # noqa: SLF001
        journal=journal,
        source=source,
        image=image,
        kube_context="context",
        command_runner=lambda args, **_kwargs: _result(args),
        checkpoint_writer=lambda: checkpoints.append(str(record.get("status"))),
    )

    assert record["status"] == "absent"
    assert record["mount_absent"] is True
    assert record["absent_at"]
    assert checkpoints == ["absent"]


@pytest.mark.parametrize("dirty_rollback_fence", (False, True))
def test_source_authority_rollback_requires_a_fresh_three_node_fence(
    monkeypatch: pytest.MonkeyPatch,
    dirty_rollback_fence: bool,
) -> None:
    image = f"registry.example/slurmctld@sha256:{_DIGEST}"
    journal: dict[str, Any] = {
        "stage": BridgeStage.STATE_PROMOTED.value,
        "node_groups": _bridge_node_groups(),
        "fencing": {
            "source": {
                "runtime_evidence": [
                    {"node_name": "source-controller-node", "node_uid": "source-node-uid"}
                ]
            }
        },
        "state_precopy": {"source_state_save_location": "/var/spool/slurmctld"},
        "authority": {
            "epoch": "source-epoch-1",
            "owner": "source-singleton",
            "first_bridge_write_at": "",
            "source_restart_prohibited": False,
            "history": [],
        },
    }
    plan = type(
        "Plan",
        (),
        {
            "namespace": "cxcli-soperator-upgrade-bridge",
            "source_slurm_image": image,
            "campaign_fingerprint": _DIGEST,
        },
    )()
    statefulset = {
        "metadata": {"uid": "bridge-sts-uid", "resourceVersion": "7"},
        "spec": {"replicas": 0},
    }
    monkeypatch.setattr(migration, "_delete_exact_bridge_cold_reader", lambda **_kwargs: None)
    monkeypatch.setattr(
        migration,
        "_revalidate_controller_bridge_mirrored_material",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_prove_controller_bridge_shared_mount",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_revalidate_controller_bridge_security_contract",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_revalidate_controller_runtime_census_nodes",
        lambda **_kwargs: [],
    )
    authority_targets: list[str] = []

    def transition_authority(**kwargs: Any) -> dict[str, object]:
        authority_targets.append(str(kwargs["to_owner"]))
        return {}

    monkeypatch.setattr(
        migration,
        "_transition_controller_authority_lease",
        transition_authority,
    )
    runtime_fence_target_sets: list[set[str]] = []

    def prove_runtime_fence(**kwargs: Any) -> list[dict[str, object]]:
        runtime_fence_target_sets.append({target.node_name for target in kwargs["targets"]})
        if dirty_rollback_fence and len(runtime_fence_target_sets) == 3:
            raise migration.SoperatorMigrationPhasePending(
                "dirty bridge node blocks source authority rollback"
            )
        return []

    monkeypatch.setattr(migration, "_prove_controller_runtime_fence", prove_runtime_fence)
    monkeypatch.setattr(
        migration,
        "_prove_cluster_wide_slurmctld_absence",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_kubectl_get_namespace_resource",
        lambda **_kwargs: (True, statefulset),
    )

    def reject_scale(**_kwargs: Any) -> None:
        raise RuntimeError("scale rejected")

    monkeypatch.setattr(migration, "_kubectl_scale_namespace_resource", reject_scale)

    expected_error = (
        "dirty bridge node blocks source authority rollback"
        if dirty_rollback_fence
        else "scale rejected"
    )
    with pytest.raises(RuntimeError, match=expected_error):
        migration._activate_source_version_controller_bridge(  # noqa: SLF001
            checkpoint={},
            journal=journal,
            plan=plan,
            source=_source(),
            kube_context="context",
            command_runner=lambda args, **_kwargs: _result(args),
            checkpoint_writer=lambda: None,
        )

    assert journal["authority"]["first_bridge_write_at"] == ""
    assert journal["authority"]["source_restart_prohibited"] is False
    assert (
        runtime_fence_target_sets
        == [{"source-controller-node", "bridge-node-0", "bridge-node-1"}] * 3
    )
    if dirty_rollback_fence:
        assert authority_targets == ["bridge-source"]
        assert journal["authority"]["writer_scale"]["state"] == "dispatching"
        assert "source_restart_revalidated_safe" not in journal["authority"]["writer_scale"]
    else:
        assert authority_targets == ["bridge-source", "source-singleton"]
        assert journal["authority"]["writer_scale"]["state"] == "rejected"
        assert journal["authority"]["writer_scale"]["source_restart_revalidated_safe"] is True


def test_source_scale_ambiguous_response_with_live_writers_permanently_fences_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = f"registry.example/slurmctld@sha256:{_DIGEST}"
    journal: dict[str, Any] = {
        "stage": BridgeStage.STATE_PROMOTED.value,
        "node_groups": _bridge_node_groups(),
        "fencing": {
            "source": {
                "runtime_evidence": [
                    {"node_name": "source-controller-node", "node_uid": "source-node-uid"}
                ]
            }
        },
        "state_precopy": {"source_state_save_location": "/var/spool/slurmctld"},
        "authority": {
            "epoch": "source-epoch-1",
            "owner": "source-singleton",
            "first_bridge_write_at": "",
            "source_restart_prohibited": False,
            "history": [],
        },
    }
    plan = type(
        "Plan",
        (),
        {
            "namespace": "cxcli-soperator-upgrade-bridge",
            "source_slurm_image": image,
            "campaign_fingerprint": _DIGEST,
        },
    )()
    stopped = {
        "metadata": {"uid": "bridge-sts-uid", "resourceVersion": "7"},
        "spec": {"replicas": 0},
    }
    accepted = copy.deepcopy(stopped)
    accepted["spec"]["replicas"] = 2
    observations = iter((stopped, accepted))
    monkeypatch.setattr(migration, "_delete_exact_bridge_cold_reader", lambda **_kwargs: None)
    monkeypatch.setattr(
        migration,
        "_revalidate_controller_bridge_mirrored_material",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_prove_controller_bridge_shared_mount",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_revalidate_controller_bridge_security_contract",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_revalidate_controller_runtime_census_nodes",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        migration,
        "_transition_controller_authority_lease",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        migration,
        "_prove_controller_runtime_fence",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        migration,
        "_prove_cluster_wide_slurmctld_absence",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_kubectl_get_namespace_resource",
        lambda **_kwargs: (True, next(observations)),
    )
    monkeypatch.setattr(
        migration,
        "_kubectl_scale_namespace_resource",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("scale transport failed")),
    )

    def record_authority(target: dict[str, Any], **kwargs: Any) -> None:
        target["authority"].update(
            {
                "epoch": kwargs["epoch"],
                "owner": kwargs["owner"],
                "first_bridge_write_at": "2026-07-12T10:10:00Z",
                "source_restart_prohibited": True,
            }
        )

    monkeypatch.setattr(migration, "record_bridge_authority", record_authority)

    def runner(args: Sequence[str], **_kwargs: Any) -> migration.SoperatorMigrationCommandResult:
        if "rollout" in args:
            raise RuntimeError("stop after source writer boundary")
        return _result(args)

    with pytest.raises(RuntimeError, match="stop after source writer boundary"):
        migration._activate_source_version_controller_bridge(  # noqa: SLF001
            checkpoint={},
            journal=journal,
            plan=plan,
            source=_source(),
            kube_context="context",
            command_runner=runner,
            checkpoint_writer=lambda: None,
        )

    assert journal["authority"]["writer_scale"]["state"] == "accepted"
    assert journal["authority"]["first_bridge_write_at"]
    assert journal["authority"]["source_restart_prohibited"] is True
    assert journal["authority"]["owner"] == "bridge-source"


class _StopAfterTargetWriterAccepted(RuntimeError):
    pass


def _run_target_writer_scale_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    observed_replicas: int,
    resume_dispatch: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_image = f"registry.example/slurmctld@sha256:{_DIGEST}"
    target_image = f"registry.example/slurmctld@sha256:{'b' * 64}"
    checkpoint: dict[str, Any] = {}
    journal: dict[str, Any] = {
        "stage": BridgeStage.SOURCE_HA_ACTIVE.value,
        "namespace": "cxcli-soperator-upgrade-bridge",
        "campaign_fingerprint": _DIGEST,
        "source_configuration": {"config_map_names": ["slurm-config"]},
        "state_precopy": {"source_state_save_location": "/var/spool/slurmctld"},
        "controller_roles": [
            {"node_name": "bridge-node-0", "node_uid": "bridge-node-uid-0"},
            {"node_name": "bridge-node-1", "node_uid": "bridge-node-uid-1"},
        ],
        "mirrored_material": [
            {"kind": "ConfigMap", "name": "slurm-config", "material_sha256": _DIGEST}
        ],
        "authority": {
            "epoch": "bridge-source-1",
            "owner": "bridge-source",
            "first_bridge_write_at": "2026-07-12T10:00:00Z",
            "source_restart_prohibited": True,
            "history": [],
            "writer_scale": {
                "state": "accepted",
                "statefulset_uid": "bridge-sts-uid",
                "accepted_at": "2026-07-12T10:00:00Z",
            },
        },
        "version_transition": {
            "source_image": source_image,
            "source_version": "24.11.6",
            "target_image": target_image,
            "target_version": "25.11.3",
            "backup_sha256": "",
            "target_write_at": "",
            "downgrade_prohibited": False,
        },
    }
    checkpoint["controller_bridge"] = journal
    source_statefulset = {
        "metadata": {"uid": "bridge-sts-uid", "resourceVersion": "10"},
        "spec": {
            "replicas": 0,
            "template": {
                "metadata": {"annotations": {}},
                "spec": {"containers": [{"name": "slurmctld", "image": source_image}]},
            },
        },
    }
    target_config = (
        "ClusterName=cluster\n"
        "SlurmctldHost=cxcli-slurm-controller-bridge-0\n"
        "SlurmctldHost=cxcli-slurm-controller-bridge-1\n"
    )
    target_config_sha256 = hashlib.sha256(target_config.encode()).hexdigest()
    patched_statefulset = {
        "metadata": {"uid": "bridge-sts-uid", "resourceVersion": "11"},
        "spec": {
            "replicas": 0,
            "template": {
                "metadata": {
                    "annotations": {"nebius.ai/cxcli-target-config-sha256": target_config_sha256}
                },
                "spec": {"containers": [{"name": "slurmctld", "image": target_image}]},
            },
        },
    }
    scale_observation = copy.deepcopy(patched_statefulset)
    scale_observation["spec"]["replicas"] = observed_replicas
    if resume_dispatch:
        resume_statefulset = copy.deepcopy(patched_statefulset)
        resume_statefulset["spec"]["replicas"] = 2
        journal["version_transition"].update(
            {
                "backup_sha256": "c" * 64,
                "both_stopped_at": "2026-07-12T10:10:00Z",
                "target_config_sha256": target_config_sha256,
                "target_material_staged_at": "2026-07-12T10:11:00Z",
                "target_writer_scale": {
                    "state": "dispatching",
                    "statefulset_uid": "bridge-sts-uid",
                    "dispatching_at": "2026-07-12T10:12:00Z",
                },
            }
        )
        observations = iter((resume_statefulset, resume_statefulset, resume_statefulset))
    else:
        observations = iter(
            (source_statefulset, source_statefulset, patched_statefulset, scale_observation)
        )

    monkeypatch.setattr(migration, "validate_bridge_journal", lambda _journal: None)
    monkeypatch.setattr(
        migration,
        "_controller_bridge_target_config",
        lambda **_kwargs: ("target-config", target_config),
    )
    monkeypatch.setattr(
        migration,
        "_kubectl_get_namespace_resource",
        lambda **_kwargs: (True, next(observations)),
    )
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: {"data": {"slurm.conf": "ClusterName=cluster\n"}},
    )
    monkeypatch.setattr(
        migration,
        "_kubectl_patch_namespace_resource",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_revalidate_controller_bridge_mirrored_material",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_prove_controller_bridge_shared_mount",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_revalidate_controller_bridge_security_contract",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_revalidate_controller_runtime_census_nodes",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        migration,
        "_prove_controller_runtime_fence",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        migration,
        "_prove_cluster_wide_slurmctld_absence",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_transition_controller_authority_lease",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        migration,
        "_revalidate_controller_authority_lease",
        lambda **_kwargs: None,
    )

    def fail_scale(**_kwargs: Any) -> None:
        if resume_dispatch:
            raise AssertionError("resume must reconcile live target writers without rescaling")
        raise RuntimeError("target writer scale transport failed")

    monkeypatch.setattr(migration, "_kubectl_scale_namespace_resource", fail_scale)

    def record_authority(target: dict[str, Any], **kwargs: Any) -> None:
        target["authority"].update({"epoch": kwargs["epoch"], "owner": kwargs["owner"]})

    monkeypatch.setattr(migration, "record_bridge_authority", record_authority)

    def runner(args: Sequence[str], **_kwargs: Any) -> migration.SoperatorMigrationCommandResult:
        if "rollout" in args:
            raise _StopAfterTargetWriterAccepted("stop after target writer boundary")
        if "cxcli-controller-bridge-stager" in args and "exec" in args:
            script = str(args[-1])
            if ".preimage-" in script:
                return _result(
                    args,
                    stdout=f"CXCLI_MANIFEST={'c' * 64} CXCLI_ENTRIES=1\n",
                )
            if "CXCLI_BACKUP_MANIFEST" in script:
                return _result(
                    args,
                    stdout=f"CXCLI_BACKUP_MANIFEST={'c' * 64} CXCLI_BACKUP_ENTRIES=1\n",
                )
        return _result(args)

    caught: RuntimeError | None = None
    try:
        migration._upgrade_controller_bridge_to_target_version(  # noqa: SLF001
            checkpoint=checkpoint,
            values={},
            target_ref="cluster",
            kube_context="context",
            command_runner=runner,
            checkpoint_writer=lambda: None,
        )
    except RuntimeError as exc:
        caught = exc
    assert caught is not None
    return journal, checkpoint


def test_target_scale_rejection_does_not_claim_write_or_prohibit_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, _checkpoint = _run_target_writer_scale_transport_failure(
        monkeypatch,
        observed_replicas=0,
    )

    transition = journal["version_transition"]
    assert transition["target_writer_scale"]["state"] == "rejected"
    assert transition.get("target_write_at", "") == ""
    assert transition["downgrade_prohibited"] is False
    assert transition["backup_operation"]["state"] == "accepted"
    assert transition["backup_sha256"] == "c" * 64
    assert journal["authority"]["owner"] == "bridge-source"


def test_target_scale_ambiguous_response_with_live_writers_prohibits_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, _checkpoint = _run_target_writer_scale_transport_failure(
        monkeypatch,
        observed_replicas=2,
    )

    transition = journal["version_transition"]
    assert transition["target_writer_scale"]["state"] == "accepted"
    assert transition["target_write_at"]
    assert transition["downgrade_prohibited"] is True
    assert transition["backup_operation"]["state"] == "accepted"
    assert journal["authority"]["owner"] == "bridge-target"


def test_target_scale_dispatch_resume_reconciles_live_writers_before_any_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, _checkpoint = _run_target_writer_scale_transport_failure(
        monkeypatch,
        observed_replicas=2,
        resume_dispatch=True,
    )

    transition = journal["version_transition"]
    assert transition["target_writer_scale"]["state"] == "accepted"
    assert transition["target_writer_scale"]["accepted_by_live_reconciliation"] is True
    assert transition["target_write_at"]
    assert transition["downgrade_prohibited"] is True
    assert journal["authority"]["owner"] == "bridge-target"


_GNU_TAR = (
    "GNU tar"
    in subprocess.run(
        ["tar", "--version"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout
)


@pytest.mark.skipif(not _GNU_TAR, reason="GNU incremental tar is required by the bridge")
def test_gnu_incremental_delta_replays_changes_and_deletions(tmp_path: Path) -> None:
    source = tmp_path / "source"
    clone = tmp_path / "clone"
    source.mkdir()
    clone.mkdir()
    (source / "kept").write_text("before", encoding="utf-8")
    (source / "deleted").write_text("delete-me", encoding="utf-8")
    (source / "link").symlink_to("kept")
    snapshot = tmp_path / "state.snar"
    full = tmp_path / "full.tar"
    subprocess.run(
        [
            "tar",
            "--listed-incremental",
            str(snapshot),
            "--numeric-owner",
            "--xattrs",
            "--acls",
            "-cpf",
            str(full),
            "-C",
            str(source),
            ".",
        ],
        check=True,
    )
    subprocess.run(
        ["tar", "--listed-incremental=/dev/null", "-xpf", str(full), "-C", str(clone)],
        check=True,
    )

    (source / "kept").write_text("after", encoding="utf-8")
    (source / "deleted").unlink()
    (source / "added").write_text("new", encoding="utf-8")
    (source / "link").unlink()
    (source / "link").symlink_to("added")
    delta = tmp_path / "delta.tar"
    subprocess.run(
        [
            "tar",
            "--listed-incremental",
            str(snapshot),
            "--numeric-owner",
            "--xattrs",
            "--acls",
            "-cpf",
            str(delta),
            "-C",
            str(source),
            ".",
        ],
        check=True,
    )
    subprocess.run(
        ["tar", "--listed-incremental=/dev/null", "-xpf", str(delta), "-C", str(clone)],
        check=True,
    )

    assert (clone / "kept").read_text(encoding="utf-8") == "after"
    assert not (clone / "deleted").exists()
    assert (clone / "added").read_text(encoding="utf-8") == "new"
    assert (clone / "link").readlink() == Path("added")
    source_hash = hashlib.sha256(
        "\n".join(sorted(path.name for path in source.iterdir())).encode()
    ).hexdigest()
    clone_hash = hashlib.sha256(
        "\n".join(sorted(path.name for path in clone.iterdir())).encode()
    ).hexdigest()
    assert source_hash == clone_hash
