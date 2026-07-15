from __future__ import annotations

import copy
import hashlib
import inspect
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
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


def test_slurm_config_patch_uses_exact_journaled_key_and_preserves_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched: dict[str, Any] = {}
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: {
            "data": {
                "slurm.conf": "ClusterName=source\n",
                "custom_slurm.conf": "# customer fragment\n",
            }
        },
    )
    monkeypatch.setattr(
        migration,
        "_kubectl_patch_namespace_resource",
        lambda **kwargs: patched.update(kwargs),
    )

    migration._patch_slurm_config_map_text(  # noqa: SLF001
        namespace="soperator",
        name="slurm-config",
        config_key="slurm.conf",
        slurm_conf="ClusterName=target\n",
        kube_context="context",
        command_runner=lambda *_args, **_kwargs: _result(()),
    )

    assert patched["patch"]["data"] == {
        "slurm.conf": "ClusterName=target\n",
        "custom_slurm.conf": "# customer fragment\n",
    }


def test_slurm_config_patch_rejects_missing_journaled_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: {"data": {"custom_slurm.conf": "ClusterName=foreign\n"}},
    )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="lost its exact journaled key slurm.conf",
    ):
        migration._patch_slurm_config_map_text(  # noqa: SLF001
            namespace="soperator",
            name="slurm-config",
            config_key="slurm.conf",
            slurm_conf="ClusterName=target\n",
            kube_context="context",
            command_runner=lambda *_args, **_kwargs: _result(()),
        )


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
    staging = inspect.getsource(migration._bridge_stage_precopy_archive)  # noqa: SLF001
    cold_copy = inspect.getsource(  # noqa: SLF001
        migration._cold_copy_and_promote_controller_state
    )

    assert "transfer_token" in precopy
    assert 'rm -f -- "$archive" "$snapshot" "$complete"' in precopy
    assert "test ! -L" in staging
    assert "rm -rf --" in staging
    assert "_bridge_tree_manifest_transient_cleanup_script" in staging
    assert "delta_token" in cold_copy
    assert 'condition="create"' in cold_copy
    assert '"capture_mode": "full-cold-posix-v1"' in cold_copy
    assert "tar --format=posix" in cold_copy
    assert "cp {shlex.quote(remote_baseline)}" not in cold_copy
    assert "cp -a --reflink=auto" not in cold_copy
    assert 'rm -f -- "$delta" "$snapshot"' in cold_copy


def test_bridge_tree_manifest_uses_destination_representable_mtime_precision() -> None:
    script = migration._bridge_tree_manifest_script(  # noqa: SLF001
        root="/source",
        output="/work/tree.tsv",
    )

    assert "mtime_seconds=$(stat -c %Y" in script
    assert "stat -c %y" not in script


def test_precopy_stage_discards_only_owned_incomplete_incoming_tree(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "precopy.tar"
    snapshot = tmp_path / "precopy.snar"
    archive.write_bytes(b"archive")
    snapshot.write_bytes(b"snapshot")
    shell_scripts: list[str] = []

    def runner(
        args: Sequence[str],
        **_kwargs: Any,
    ) -> migration.SoperatorMigrationCommandResult:
        selected = tuple(str(item) for item in args)
        if len(selected) >= 2 and selected[-2] == "-ec":
            shell_scripts.append(selected[-1])
            if len(shell_scripts) == 3:
                return _result(selected, returncode=1)
            if len(shell_scripts) == 4:
                return _result(
                    selected,
                    stdout=f"CXCLI_MANIFEST={'c' * 64} CXCLI_ENTRIES=3\n",
                )
        return _result(selected)

    remote_path, manifest, entries = migration._bridge_stage_precopy_archive(  # noqa: SLF001
        plan=SimpleNamespace(namespace="cxcli-soperator-upgrade-bridge"),
        authority_epoch="source-epoch-1",
        archive=archive,
        archive_sha256="a" * 64,
        snapshot=snapshot,
        snapshot_sha256="b" * 64,
        transfer_token="d" * 32,
        kube_context="context",
        command_runner=runner,
    )

    fallback = shell_scripts[3]
    existing_verification = shell_scripts[2]
    incoming = "/shared/.incoming-source-epoch-1-precopy-" + "d" * 32
    assert " -dpf " not in existing_verification
    assert " -dpf " not in fallback
    assert " -xpf " in fallback
    assert f"test ! -L {incoming}" in fallback
    assert f"test -d {incoming}" in fallback
    assert f"rm -rf -- {incoming}" in fallback
    assert f"mkdir {incoming}" in fallback
    assert remote_path == "/shared/epochs/source-epoch-1.precopy"
    assert manifest == "c" * 64
    assert entries == 3


def test_full_cold_promotion_never_deletes_shared_current_or_live_epoch() -> None:
    source = inspect.getsource(migration._bridge_apply_cold_delta)  # noqa: SLF001

    assert "rm " not in source
    assert " -dpf " not in source
    assert 'destination = f"/shared/epochs/{authority_epoch}"' in source
    assert 'precopy = f"/shared/epochs/{authority_epoch}.precopy"' not in source
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


def test_target_version_bridge_accepts_checkpointed_target_cluster_name_change() -> None:
    journal = {
        "cluster_name": "source-cluster",
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
                '{"data":{"slurm.conf":"ClusterName=target-cluster\\n'
                'SlurmctldHost=controller-0\\nStateSaveLocation=/target/state\\n"}}'
            ),
        )

    name, rendered = migration._controller_bridge_target_config(  # noqa: SLF001
        journal=journal,
        values={"clusterName": "target-cluster"},
        target_ref="target-cluster",
        kube_context="ctx",
        command_runner=runner,
    )

    assert name == "target-cluster-slurm-configs"
    assert "ClusterName=target-cluster" in rendered
    assert "StateSaveLocation=/var/spool/slurmctld" in rendered


def test_target_version_bridge_preserves_missing_source_partitions_paused() -> None:
    journal = {
        "cluster_name": "source-cluster",
        "state_precopy": {"source_state_save_location": "/var/spool/slurmctld"},
        "source_configuration": {
            "original_slurm_conf": (
                "ClusterName=source-cluster\n"
                "PartitionName=main Nodes=ALL Default=YES State=UP PriorityTier=10\n"
                "PartitionName=hidden Nodes=ALL Hidden=YES State=UP\n"
            )
        },
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
                '{"data":{"slurm.conf":"ClusterName=target-cluster\\n'
                "SlurmctldHost=controller-0\\nStateSaveLocation=/target/state\\n"
                'PartitionName=gpu Nodes=worker Default=YES State=UP\\n"}}'
            ),
        )

    _name, rendered = migration._controller_bridge_target_config(  # noqa: SLF001
        journal=journal,
        values={"clusterName": "target-cluster"},
        target_ref="target-cluster",
        kube_context="ctx",
        command_runner=runner,
    )

    assert "PartitionName=gpu Nodes=worker Default=YES State=UP" in rendered
    assert "PartitionName=main Nodes=ALL Default=NO State=DOWN PriorityTier=10" in rendered
    assert "PartitionName=hidden Nodes=ALL Hidden=YES State=DOWN Default=NO" in rendered


def test_target_version_bridge_rejects_duplicate_partition_definitions() -> None:
    with pytest.raises(RuntimeError, match="duplicate or empty PartitionName"):
        migration._controller_bridge_preserve_source_partitions(  # noqa: SLF001
            target_slurm_conf=(
                "ClusterName=target\n"
                "PartitionName=gpu Nodes=worker\n"
                "PartitionName=GPU Nodes=worker\n"
            ),
            source_slurm_conf="PartitionName=main Nodes=ALL\n",
        )


def _pending_job_observation(job_id: str = "16") -> migration._SlurmJobControlObservation:  # noqa: SLF001
    identity = {
        "job_id": job_id,
        "user_id": "root(0)",
        "job_name": "held-upgrade-test",
        "submit_time": "2026-07-15T07:00:00",
        "array_job_id": "",
        "array_task_id": "",
        "het_job_id": "",
        "het_job_offset": "",
    }
    record = (
        f"JobId={job_id} UserId=root(0) JobName=held-upgrade-test "
        "SubmitTime=2026-07-15T07:00:00 ArrayJobId= ArrayTaskId= HetJobId= "
        "HetJobOffset= JobState=PENDING NodeList=(null) Partition=main Priority=0 "
        "Reason=JobHeldAdmin Requeue=1 Restarts=0 TimeLimit=UNLIMITED"
    )
    return migration._SlurmJobControlObservation(  # noqa: SLF001
        job_id=job_id,
        identity=identity,
        identity_fingerprint=hashlib.sha256(
            migration._stable_json(identity).encode("utf-8")  # noqa: SLF001
        ).hexdigest(),
        job_state="PENDING",
        reason="JobHeldAdmin",
        priority="0",
        held=True,
        record=record,
        record_fingerprint=hashlib.sha256(record.encode("utf-8")).hexdigest(),
    )


def test_cold_controller_job_census_preserves_late_pending_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint: dict[str, Any] = {}
    transition: dict[str, Any] = {}
    job = migration.AffectedSlurmJob(
        job_id="16",
        user="root",
        state="PENDING",
        partition="main",
        allocated_nodes="",
        requested_nodes="",
        scheduled_nodes="",
        reason="JobHeldAdmin",
        elapsed="0:00",
        limit="UNLIMITED",
        remaining="UNLIMITED",
        name="held-upgrade-test",
        impact_scope="pending",
    )
    observation = _pending_job_observation()
    monkeypatch.setattr(migration, "_external_upgrade_all_slurm_jobs", lambda **_kwargs: (job,))
    monkeypatch.setattr(
        migration,
        "_external_upgrade_slurm_job_control_observation",
        lambda **_kwargs: observation,
    )

    migration._capture_controller_bridge_cold_job_census(  # noqa: SLF001
        checkpoint=checkpoint,
        transition=transition,
        kube_context="ctx",
        command_runner=lambda *_args, **_kwargs: _result(()),
        checkpoint_writer=None,
    )
    lines = migration._verify_controller_bridge_cold_job_census(  # noqa: SLF001
        checkpoint=checkpoint,
        transition=transition,
        kube_context="ctx",
        command_runner=lambda *_args, **_kwargs: _result(()),
        checkpoint_writer=None,
    )

    assert transition["cold_job_census"]["job_ids"] == ["16"]
    assert transition["cold_job_census"]["status"] == "verified"
    assert lines == ["Cold controller restart preserved 1 exact active/pending Slurm job(s)."]


def test_cold_controller_job_census_rejects_lost_pending_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation = _pending_job_observation()
    checkpoint: dict[str, Any] = {}
    transition = {
        "cold_job_census": {
            "schema": migration._SLURM_COLD_JOB_CENSUS_SCHEMA,  # noqa: SLF001
            "status": "captured",
            "job_ids": ["16"],
            "jobs": {"16": {"observation": observation.as_payload(), "partition": "main"}},
            "captured_at": "2026-07-15T07:01:00Z",
        }
    }

    def missing(**_kwargs: Any) -> migration._SlurmJobControlObservation:  # noqa: SLF001
        raise RuntimeError("invalid job id")

    monkeypatch.setattr(migration, "_external_upgrade_slurm_job_control_observation", missing)

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="pending Slurm job 16 disappeared",
    ):
        migration._verify_controller_bridge_cold_job_census(  # noqa: SLF001
            checkpoint=checkpoint,
            transition=transition,
            kube_context="ctx",
            command_runner=lambda *_args, **_kwargs: _result(()),
            checkpoint_writer=None,
        )


def test_target_version_bridge_rejects_cluster_name_outside_target_values() -> None:
    journal = {
        "cluster_name": "source-cluster",
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
                '{"data":{"slurm.conf":"ClusterName=foreign-cluster\\n'
                'SlurmctldHost=controller-0\\nStateSaveLocation=/target/state\\n"}}'
            ),
        )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="does not match the exact target Helm values",
    ):
        migration._controller_bridge_target_config(  # noqa: SLF001
            journal=journal,
            values={"clusterName": "target-cluster"},
            target_ref="target-cluster",
            kube_context="ctx",
            command_runner=runner,
        )


def test_target_jailed_config_stager_binds_config_map_pvc_and_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_sha = "b" * 64
    source_sha = "a" * 64
    journal: dict[str, Any] = {
        "namespace": "bridge",
        "cluster_id": "cluster-id",
        "campaign_fingerprint": _DIGEST,
        "controller_roles": [{"node_name": "bridge-node-0"}],
        "version_transition": {},
    }
    manifests: list[dict[str, Any]] = []

    def runner(
        args: Sequence[str],
        *,
        check: bool = True,
        input_text: str | None = None,
        timeout_seconds: int | None = None,
    ) -> migration.SoperatorMigrationCommandResult:
        del check, timeout_seconds
        if "apply" in args:
            assert input_text is not None
            manifests.append(json.loads(input_text))
        if "logs" in args:
            return _result(
                args,
                stdout=(
                    "schema=nebius-cxcli-controller-bridge-jailed-config/v1\n"
                    f"target_sha256={target_sha}\n"
                ),
            )
        return _result(args)

    monkeypatch.setattr(migration, "_kubectl_wait", lambda **_kwargs: None)

    migration._stage_controller_bridge_jailed_config(  # noqa: SLF001
        journal=journal,
        config_map_name="bridge-config",
        config_key="slurm.conf",
        target_config_sha256=target_sha,
        expected_preimage_sha256=source_sha,
        target_image=f"registry.example/slurmctld@sha256:{target_sha}",
        kube_context="context",
        command_runner=runner,
        checkpoint_writer=lambda: None,
    )

    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest["spec"]["nodeName"] == "bridge-node-0"
    assert manifest["spec"]["volumes"] == [
        {
            "name": "jail",
            "persistentVolumeClaim": {"claimName": "cxcli-controller-bridge-jail"},
        },
        {
            "name": "config",
            "configMap": {
                "name": "bridge-config",
                "items": [{"key": "slurm.conf", "path": "slurm.conf"}],
            },
        },
    ]
    command = manifest["spec"]["containers"][0]["command"][2]
    assert f"expected_target={target_sha}" in command
    assert f"expected_preimage={source_sha}" in command
    assert journal["version_transition"]["target_jailed_config"]["state"] == "accepted"


def test_target_cluster_name_marker_is_atomically_migrated_on_promoted_state() -> None:
    journal: dict[str, Any] = {
        "namespace": "bridge",
        "version_transition": {
            "source_cluster_name": "source-cluster",
            "target_cluster_name": "target-cluster",
        },
    }
    scripts: list[str] = []

    def runner(
        args: Sequence[str],
        **_kwargs: Any,
    ) -> migration.SoperatorMigrationCommandResult:
        scripts.append(str(args[-1]))
        return _result(
            args,
            stdout=(
                "schema=nebius-cxcli-controller-cluster-name-marker/v1\n"
                "target_cluster_name=target-cluster\n"
            ),
        )

    migration._stage_controller_bridge_cluster_name_marker(  # noqa: SLF001
        journal=journal,
        kube_context="context",
        command_runner=runner,
        checkpoint_writer=lambda: None,
    )

    assert len(scripts) == 1
    assert '"$expected_source"\\|*' in scripts[0]
    assert "*[!0-9]*" in scripts[0]
    assert 'target_value="${expected_target}${suffix}"' in scripts[0]
    assert 'mv -f "$temporary" "$marker"' in scripts[0]
    marker = journal["version_transition"]["target_cluster_name_marker"]
    assert marker["state"] == "accepted"
    assert marker["source_cluster_name"] == "source-cluster"
    assert marker["target_cluster_name"] == "target-cluster"


def test_accepted_target_with_source_jailed_config_is_fenced_staged_and_restarted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_sha = "a" * 64
    target_sha = "b" * 64
    target_image = f"registry.example/slurmctld@sha256:{target_sha}"
    journal: dict[str, Any] = {
        "namespace": "bridge",
        "cluster_id": "cluster-id",
        "campaign_fingerprint": _DIGEST,
        "source_configuration": {"bridge_config_sha256": source_sha},
        "state_precopy": {"source_state_save_location": "/var/spool/slurmctld"},
        "controller_roles": [
            {"node_name": "bridge-node-0", "node_uid": "node-uid-0"},
            {"node_name": "bridge-node-1", "node_uid": "node-uid-1"},
        ],
        "version_transition": {},
    }
    statefulset = {
        "metadata": {"uid": "bridge-sts-uid", "resourceVersion": "10"},
        "spec": {"replicas": 2},
    }
    stopped_statefulset = copy.deepcopy(statefulset)
    stopped_statefulset["metadata"]["resourceVersion"] = "11"
    stopped_statefulset["spec"]["replicas"] = 0
    events: list[str] = []
    pods = {
        "items": [
            {"metadata": {"name": "bridge-0", "uid": "pod-0"}},
            {"metadata": {"name": "bridge-1", "uid": "pod-1"}},
        ]
    }

    monkeypatch.setattr(
        migration,
        "_controller_bridge_live_jailed_config_digests",
        lambda **_kwargs: {"pod-0": source_sha, "pod-1": source_sha},
    )
    monkeypatch.setattr(migration, "_json_from_command", lambda *_args, **_kwargs: pods)
    monkeypatch.setattr(
        migration,
        "_prove_cluster_wide_slurmctld_exclusivity",
        lambda **_kwargs: [
            {
                "node_name": "bridge-node-0",
                "node_uid": "node-uid-0",
                "provider_id": "provider-0",
                "system_uuid": "system-0",
            },
            {
                "node_name": "bridge-node-1",
                "node_uid": "node-uid-1",
                "provider_id": "provider-1",
                "system_uuid": "system-1",
            },
        ],
    )
    monkeypatch.setattr(
        migration,
        "_kubectl_scale_namespace_resource",
        lambda **kwargs: events.append(f"scale-{kwargs['replicas']}"),
    )
    monkeypatch.setattr(
        migration,
        "_prove_controller_runtime_fence",
        lambda **_kwargs: events.append("fence") or [],
    )
    absence_modes: list[bool] = []
    monkeypatch.setattr(
        migration,
        "_prove_cluster_wide_slurmctld_absence",
        lambda **kwargs: absence_modes.append(bool(kwargs.get("process_census", True))) or [],
    )
    monkeypatch.setattr(
        migration,
        "_revalidate_controller_runtime_census_nodes",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        migration,
        "_stage_controller_bridge_jailed_config",
        lambda **_kwargs: events.append("stage"),
    )
    monkeypatch.setattr(
        migration,
        "_stage_controller_bridge_cluster_name_marker",
        lambda **_kwargs: events.append("marker"),
    )
    monkeypatch.setattr(
        migration,
        "_kubectl_get_namespace_resource",
        lambda **_kwargs: (True, stopped_statefulset),
    )
    monkeypatch.setattr(
        migration,
        "_revalidate_controller_authority_lease",
        lambda **_kwargs: events.append("lease"),
    )
    monkeypatch.setattr(
        migration,
        "_revalidate_controller_bridge_security_contract",
        lambda **_kwargs: events.append("security"),
    )

    replicas = migration._ensure_controller_bridge_target_jailed_config(  # noqa: SLF001
        journal=journal,
        statefulset=statefulset,
        target_replicas=2,
        container_name="slurmctld",
        config_map_name="bridge-config",
        config_key="slurm.conf",
        target_config_sha256=target_sha,
        target_image=target_image,
        target_write_already_accepted=True,
        kube_context="context",
        command_runner=lambda args, **_kwargs: _result(args),
        checkpoint_writer=lambda: None,
    )

    assert replicas == 2
    assert events == [
        "scale-0",
        "fence",
        "stage",
        "marker",
        "lease",
        "security",
        "scale-2",
    ]
    assert absence_modes == [False]
    assert journal["version_transition"]["target_jailed_config_repair"]["state"] == "accepted"


def test_target_version_bridge_creates_cold_backup_directory_before_preimage() -> None:
    source = inspect.getsource(
        migration._upgrade_controller_bridge_to_target_version  # noqa: SLF001
    )
    preimage = source.index("preimage_script =")
    symlink_guard = source.index("test ! -L /shared/backups", preimage)
    create_directory = source.index("mkdir -p /shared/backups", symlink_guard)
    manifest = source.index("_bridge_tree_manifest_script", create_directory)

    assert preimage < symlink_guard < create_directory < manifest


def test_consumed_backup_recovery_uses_a_fresh_immutable_epoch() -> None:
    previous = {
        "state": "accepted",
        "restore_epoch": "pre-target-campaign-recovered-old",
        "token": "a" * 32,
        "accepted_at": "2026-07-15T04:54:17Z",
    }
    transition: dict[str, Any] = {
        "backup_recovery": copy.deepcopy(previous),
        "backup_recovery_consumed": True,
    }

    recovery, reused = migration._controller_bridge_backup_recovery_binding(  # noqa: SLF001
        transition,
        backup_epoch="pre-target-campaign",
    )

    assert reused is False
    assert recovery["restore_epoch"].startswith("pre-target-campaign-recovered-")
    assert recovery["restore_epoch"] != previous["restore_epoch"]
    assert recovery["token"] != previous["token"]
    assert transition["backup_recovery_consumed"] is False
    assert transition["backup_recovery_history"] == [previous]


def test_unaccepted_backup_recovery_resume_reuses_exact_operation_binding() -> None:
    recovery = {
        "state": "dispatching",
        "restore_epoch": "pre-target-campaign-recovered-current",
        "token": "b" * 32,
    }
    transition: dict[str, Any] = {
        "backup_recovery": recovery,
        "backup_recovery_consumed": False,
    }

    observed, reused = migration._controller_bridge_backup_recovery_binding(  # noqa: SLF001
        transition,
        backup_epoch="pre-target-campaign",
    )

    assert reused is True
    assert observed is recovery
    assert "backup_recovery_history" not in transition


def test_pre_target_transition_failure_restores_exact_source_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_image = f"registry.example/slurmctld@sha256:{_DIGEST}"
    checkpoint: dict[str, Any] = {
        "controller_bridge": {
            "stage": BridgeStage.SOURCE_HA_ACTIVE.value,
            "namespace": "bridge",
            "authority": {
                "owner": "bridge-source",
                "writer_scale": {"statefulset_uid": "bridge-sts-uid"},
            },
            "version_transition": {
                "source_image": source_image,
                "target_write_at": "",
                "target_material_staged_at": "",
                "downgrade_prohibited": False,
                "post_stop_api_absence_verified_at": "2026-07-15T05:17:41Z",
                "source_runtime_fence": [
                    {
                        "fenced": True,
                        "slurmctld_count": 0,
                        "writable_state_mount_count": 0,
                    },
                    {
                        "fenced": True,
                        "slurmctld_count": 0,
                        "writable_state_mount_count": 0,
                    },
                ],
            },
        }
    }
    stopped = {
        "metadata": {
            "uid": "bridge-sts-uid",
            "resourceVersion": "12",
        },
        "spec": {
            "replicas": 0,
            "template": {
                "spec": {
                    "containers": [
                        {"name": "slurmctld", "image": source_image},
                    ]
                }
            },
        },
    }
    monkeypatch.setattr(
        migration,
        "_kubectl_get_namespace_resource",
        lambda **_kwargs: (True, stopped),
    )
    monkeypatch.setattr(
        migration,
        "_revalidate_controller_bridge_mirrored_material",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        migration,
        "_revalidate_controller_bridge_security_contract",
        lambda **_kwargs: None,
    )
    scales: list[dict[str, Any]] = []
    monkeypatch.setattr(
        migration,
        "_kubectl_scale_namespace_resource",
        lambda **kwargs: scales.append(kwargs),
    )

    def runner(args: Sequence[str], **_kwargs: Any) -> migration.SoperatorMigrationCommandResult:
        if "logs" in args:
            return _result(args, stdout="slurmctld: Running as primary controller\n")
        if "get" in args and "statefulset/cxcli-slurm-controller-bridge" in args:
            live = copy.deepcopy(stopped)
            live["spec"]["replicas"] = 2
            live["status"] = {"readyReplicas": 2}
            return _result(args, stdout=migration.json.dumps(live))
        return _result(args)

    lines = migration._restore_source_bridge_after_failed_target_transition(  # noqa: SLF001
        checkpoint=checkpoint,
        kube_context="context",
        command_runner=runner,
        checkpoint_writer=lambda: None,
    )

    assert lines
    assert scales[0]["replicas"] == 2
    transition = checkpoint["controller_bridge"]["version_transition"]
    assert transition["source_recovery_after_failed_target_transition"]["state"] == "accepted"
    assert transition["cold_stop_dispatching_at"] == ""


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


def test_namespaced_resource_get_treats_not_found_as_absent() -> None:
    observed_check: list[bool] = []

    def runner(
        args: Sequence[str],
        *,
        check: bool = True,
        timeout_seconds: int | None = None,
        **_kwargs: object,
    ) -> migration.SoperatorMigrationCommandResult:
        observed_check.append(check)
        return _result(args, returncode=1, stderr="Error from server (NotFound): missing")

    exists, resource = migration._kubectl_get_namespace_resource(  # noqa: SLF001
        command_runner=runner,
        kube_context="context",
        resource="pod/missing",
    )

    assert exists is False
    assert resource == {}
    assert observed_check == [False]


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
        "_ensure_controller_bridge_target_jailed_config",
        lambda **kwargs: int(kwargs["target_replicas"]),
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
        "_ensure_controller_bridge_target_jailed_config",
        lambda **kwargs: int(kwargs["target_replicas"]),
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


def test_source_bridge_takeover_reuses_exact_live_primary_marker() -> None:
    journal: dict[str, Any] = {"namespace": "bridge"}
    pods = {
        "bridge-0": {"metadata": {"uid": "pod-0"}},
        "bridge-1": {"metadata": {"uid": "pod-1"}},
    }
    calls: list[tuple[str, ...]] = []

    def runner(args: Sequence[str], **_kwargs: Any) -> migration.SoperatorMigrationCommandResult:
        calls.append(tuple(args))
        if "logs" in args:
            return _result(
                args,
                stdout=("Running as primary controller\n" if "bridge-0" in args else ""),
            )
        return _result(args)

    active = migration._prove_source_bridge_takeover(  # noqa: SLF001
        journal=journal,
        pod_by_name=pods,
        source_container="slurmctld",
        kube_context="context",
        command_runner=runner,
        checkpoint_writer=lambda: None,
    )

    assert active == "bridge-0"
    assert journal["source_bridge_takeover"]["state"] == "accepted"
    assert not any("takeover" in call for call in calls)
    assert any("squeue" in call for call in calls)


def test_source_bridge_takeover_dispatches_backup_index_one() -> None:
    journal: dict[str, Any] = {"namespace": "bridge"}
    pods = {
        "bridge-0": {"metadata": {"uid": "pod-0"}},
        "bridge-1": {"metadata": {"uid": "pod-1"}},
    }
    takeover_dispatched = False

    def runner(args: Sequence[str], **_kwargs: Any) -> migration.SoperatorMigrationCommandResult:
        nonlocal takeover_dispatched
        if "takeover" in args:
            takeover_dispatched = True
        if "logs" in args:
            return _result(
                args,
                stdout=(
                    "Running as primary controller\n"
                    if takeover_dispatched and "bridge-0" in args
                    else ""
                ),
            )
        return _result(args)

    active = migration._prove_source_bridge_takeover(  # noqa: SLF001
        journal=journal,
        pod_by_name=pods,
        source_container="slurmctld",
        kube_context="context",
        command_runner=runner,
        checkpoint_writer=lambda: None,
    )

    assert active == "bridge-0"
    assert takeover_dispatched is True
    assert journal["source_bridge_takeover"]["backup_index"] == 1
    assert journal["source_bridge_takeover"]["state"] == "accepted"


def test_source_bridge_takeover_revalidation_uses_runtime_marker_and_rpc() -> None:
    journal = {
        "namespace": "bridge",
        "source_bridge_takeover": {
            "state": "accepted",
            "pod_name": "bridge-0",
            "pod_uid": "pod-0",
            "backup_index": 1,
        },
        "controller_roles": [
            {"pod_name": "bridge-0", "pod_uid": "pod-0", "role": "active"},
            {"pod_name": "bridge-1", "pod_uid": "pod-1", "role": "standby"},
        ],
    }
    pods = {
        "bridge-0": {"metadata": {"uid": "pod-0"}},
        "bridge-1": {"metadata": {"uid": "pod-1"}},
    }
    calls: list[tuple[str, ...]] = []

    def runner(args: Sequence[str], **_kwargs: Any) -> migration.SoperatorMigrationCommandResult:
        calls.append(tuple(args))
        if "logs" in args:
            return _result(
                args,
                stdout="Running as primary controller\n" if "bridge-0" in args else "",
            )
        return _result(args)

    assert (
        migration._revalidate_source_bridge_takeover(  # noqa: SLF001
            journal=journal,
            pod_by_name=pods,
            source_container="slurmctld",
            kube_context="context",
            command_runner=runner,
        )
        == "bridge-0"
    )
    assert any("squeue" in call for call in calls)
    assert not any("ping" in call or "takeover" in call for call in calls)


def test_source_bridge_takeover_revalidation_rejects_static_role_without_runtime_marker() -> None:
    journal = {
        "namespace": "bridge",
        "source_bridge_takeover": {
            "state": "accepted",
            "pod_name": "bridge-0",
            "pod_uid": "pod-0",
            "backup_index": 1,
        },
        "controller_roles": [
            {"pod_name": "bridge-0", "pod_uid": "pod-0", "role": "active"},
            {"pod_name": "bridge-1", "pod_uid": "pod-1", "role": "standby"},
        ],
    }
    pods = {
        "bridge-0": {"metadata": {"uid": "pod-0"}},
        "bridge-1": {"metadata": {"uid": "pod-1"}},
    }

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="runtime marker no longer identifies",
    ):
        migration._revalidate_source_bridge_takeover(  # noqa: SLF001
            journal=journal,
            pod_by_name=pods,
            source_container="slurmctld",
            kube_context="context",
            command_runner=lambda args, **_kwargs: _result(args),
        )


def test_bridge_container_name_comes_from_locked_workload_image() -> None:
    image = f"registry.example/slurmctld@sha256:{_DIGEST}"
    statefulset = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {"name": "slurmctld", "image": image},
                    ]
                }
            }
        }
    }

    assert (
        migration._controller_bridge_container_name(  # noqa: SLF001
            statefulset,
            expected_image=image,
        )
        == "slurmctld"
    )


def test_bridge_container_name_rejects_missing_locked_workload_image() -> None:
    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="exactly one container bound to the locked source image digest",
    ):
        migration._controller_bridge_container_name(  # noqa: SLF001
            {"spec": {"template": {"spec": {"containers": []}}}},
            expected_image=f"registry.example/slurmctld@sha256:{_DIGEST}",
        )


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
        "cluster_name": "cluster",
        "source_configuration": {
            "config_map_names": ["slurm-config"],
            "config_key": "slurm.conf",
        },
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
            "pre_stop_runtime_census": [
                {
                    "node_name": "bridge-node-0",
                    "node_uid": "bridge-node-uid-0",
                    "provider_id": "provider://bridge-node-0",
                    "system_uuid": "system-uuid-0",
                },
                {
                    "node_name": "bridge-node-1",
                    "node_uid": "bridge-node-uid-1",
                    "provider_id": "provider://bridge-node-1",
                    "system_uuid": "system-uuid-1",
                },
            ],
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
        "_ensure_controller_bridge_target_jailed_config",
        lambda **kwargs: int(kwargs["target_replicas"]),
    )
    monkeypatch.setattr(
        migration,
        "_kubectl_get_namespace_resource",
        lambda **_kwargs: (True, next(observations)),
    )
    monkeypatch.setattr(
        migration,
        "_json_from_command",
        lambda *_args, **_kwargs: {
            "data": {
                "slurm.conf": "ClusterName=cluster\n",
                "custom_slurm.conf": "# custom fragments\n",
            }
        },
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
    absence_process_census: list[bool] = []

    def prove_absence(**kwargs: Any) -> list[dict[str, Any]]:
        absence_process_census.append(bool(kwargs.get("process_census", True)))
        return []

    monkeypatch.setattr(
        migration,
        "_prove_cluster_wide_slurmctld_absence",
        prove_absence,
    )
    authority_runtime_censuses: list[list[dict[str, Any]]] = []

    def transition_authority(**kwargs: Any) -> dict[str, Any]:
        authority_runtime_censuses.append(list(copy.deepcopy(kwargs["runtime_node_census"])))
        return {}

    monkeypatch.setattr(
        migration,
        "_transition_controller_authority_lease",
        transition_authority,
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
    checkpoint["_test_absence_process_census"] = absence_process_census
    checkpoint["_test_authority_runtime_censuses"] = authority_runtime_censuses
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


def test_target_writer_start_reuses_pre_stop_census_without_post_stop_process_census(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, checkpoint = _run_target_writer_scale_transport_failure(
        monkeypatch,
        observed_replicas=0,
    )

    assert checkpoint["_test_absence_process_census"] == [False, False, False]
    assert checkpoint["_test_authority_runtime_censuses"] == [
        journal["version_transition"]["pre_stop_runtime_census"]
    ]


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


def _job_state_recovery_journal() -> dict[str, Any]:
    return {
        "namespace": "cxcli-soperator-upgrade-bridge",
        "controller_roles": [
            {"pod_name": "cxcli-slurm-controller-bridge-0"},
            {"pod_name": "cxcli-slurm-controller-bridge-1"},
        ],
        "version_transition": {
            "target_config_sha256": "b" * 64,
            "target_cluster_name": "target-cluster",
            "job_state_recovery": {
                "schema": "nebius-cxcli/controller-bridge-job-state-recovery-v1",
                "state": "verified",
                "target_config_sha256": "b" * 64,
                "job_id": "16",
                "recovery_epoch": "pre-target-job-recovery-16",
                "job_state_sha256": "c" * 64,
                "controller_statefulset_uid": "bridge-sts-uid",
                "job_state": "PENDING",
                "job_reason": "JobHeldAdmin",
                "job_partition": "main",
            },
        },
    }


def test_job_state_recovery_revalidation_allows_live_slurm_checkpoint_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = _job_state_recovery_journal()
    monkeypatch.setattr(
        migration,
        "_kubectl_get_namespace_resource",
        lambda **_kwargs: (
            True,
            {
                "metadata": {"uid": "bridge-sts-uid"},
                "spec": {"replicas": 2},
                "status": {"readyReplicas": 2},
            },
        ),
    )
    monkeypatch.setattr(
        migration,
        "_controller_bridge_live_jailed_config_digests",
        lambda **_kwargs: {"bridge-0": "b" * 64, "bridge-1": "b" * 64},
    )

    def _runner(args: Sequence[str], **_kwargs: Any) -> migration.SoperatorMigrationCommandResult:
        if "cxcli-controller-bridge-stager" in args:
            return _result(
                args,
                stdout=(
                    "epochs/pre-target-job-recovery-16\n"
                    + "d" * 64
                    + "  /shared/current/job_state\n"
                    "target-cluster|251\n"
                ),
            )
        return _result(
            args,
            stdout=("JobId=16 JobState=PENDING Reason=JobHeldAdmin Partition=main Restarts=0\n"),
        )

    lines = migration._revalidate_controller_bridge_job_state_recovery(  # noqa: SLF001
        journal=journal,
        kube_context="context",
        command_runner=_runner,
    )

    assert lines == ["Recovered Slurm job 16 revalidated from the sealed pre-switch state."]


def test_job_state_recovery_revalidation_rejects_epoch_substitution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = _job_state_recovery_journal()
    monkeypatch.setattr(
        migration,
        "_kubectl_get_namespace_resource",
        lambda **_kwargs: (
            True,
            {
                "metadata": {"uid": "bridge-sts-uid"},
                "spec": {"replicas": 2},
                "status": {"readyReplicas": 2},
            },
        ),
    )
    monkeypatch.setattr(
        migration,
        "_controller_bridge_live_jailed_config_digests",
        lambda **_kwargs: {"bridge-0": "b" * 64, "bridge-1": "b" * 64},
    )

    def _runner(args: Sequence[str], **_kwargs: Any) -> migration.SoperatorMigrationCommandResult:
        return _result(
            args,
            stdout=(
                "epochs/substituted\n" + "d" * 64 + "  /shared/current/job_state\ntarget-cluster\n"
            ),
        )

    with pytest.raises(RuntimeError, match="epoch identity or marker drifted"):
        migration._revalidate_controller_bridge_job_state_recovery(  # noqa: SLF001
            journal=journal,
            kube_context="context",
            command_runner=_runner,
        )


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
