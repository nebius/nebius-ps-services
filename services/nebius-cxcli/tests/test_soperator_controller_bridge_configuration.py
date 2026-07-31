from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from typing import Any

import pytest

from nebius_cxcli import soperator_controller_bridge as bridge_contract
from nebius_cxcli import soperator_migration as migration

_BRIDGE_NAMESPACE = "cxcli-soperator-upgrade-bridge"
_CONFIG_NAME = "slurm-controller-config"
_CAMPAIGN_FINGERPRINT = "a" * 64
_CLUSTER_ID = "mk8scluster-test"
_ORIGINAL_SLURM_CONF = (
    "ClusterName=old-cluster\n"
    "SlurmctldHost=controller-0(soperator-controller-svc)\n"
    "SlurmdTimeout=300\n"
    "SlurmctldTimeout=120\n"
)


def test_shared_authority_composer_preserves_customer_configuration() -> None:
    source = (
        _ORIGINAL_SLURM_CONF
        + "Include=/etc/slurm/customer/*.conf\n"
        + "Prolog=/opt/customer/prolog.sh\n"
        + "PluginDir=/opt/customer/plugins\n"
        + "# customer comment remains byte-for-byte\n"
    )

    result = bridge_contract.compose_controller_authority_config(
        source,
        authority_owner="bridge-target",
        controller_hosts=("bridge-0(bridge-0.svc)", "bridge-1(bridge-1.svc)"),
        state_save_location="/mnt/controller-spool/current",
        compatibility_fields={"SlurmctldTimeout": "3600", "SlurmdTimeout": "3600"},
    )

    assert result.count("ClusterName=old-cluster") == 1
    assert result.count("SlurmctldHost=") == 2
    assert "StateSaveLocation=/mnt/controller-spool/current" in result
    assert "SlurmctldTimeout=3600" in result
    assert "SlurmdTimeout=3600" in result
    for customer_line in (
        "Include=/etc/slurm/customer/*.conf",
        "Prolog=/opt/customer/prolog.sh",
        "PluginDir=/opt/customer/plugins",
        "# customer comment remains byte-for-byte",
    ):
        assert customer_line in result


def test_shared_authority_composer_rejects_singleton_bridge_and_unknown_overlay() -> None:
    with pytest.raises(ValueError, match="cannot use 1 host"):
        bridge_contract.compose_controller_authority_config(
            _ORIGINAL_SLURM_CONF,
            authority_owner="bridge-source",
            controller_hosts=("bridge-0",),
        )
    with pytest.raises(ValueError, match="unsupported.*AccountingStorageType"):
        bridge_contract.compose_controller_authority_config(
            _ORIGINAL_SLURM_CONF,
            authority_owner="bridge-source",
            controller_hosts=("bridge-0", "bridge-1"),
            compatibility_fields={"AccountingStorageType": "accounting_storage/slurmdbd"},
        )


class _InjectedCrash(BaseException):
    pass


def _result(
    args: Sequence[str],
    *,
    stdout: str = "",
) -> migration.SoperatorMigrationCommandResult:
    return migration.SoperatorMigrationCommandResult(
        args=tuple(args),
        returncode=0,
        stdout=stdout,
        stderr="",
    )


def _config_map(
    *,
    namespace: str,
    uid: str,
    resource_version: str,
    source_uid: str = "",
) -> dict[str, Any]:
    annotations = (
        {
            "nebius.ai/cxcli-campaign-fingerprint": _CAMPAIGN_FINGERPRINT,
            "nebius.ai/cxcli-cluster-id": _CLUSTER_ID,
            "nebius.ai/cxcli-source-uid": source_uid,
            "nebius.ai/cxcli-source-resource-version": "10",
        }
        if source_uid
        else {}
    )
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "namespace": namespace,
            "name": _CONFIG_NAME,
            "uid": uid,
            "resourceVersion": resource_version,
            "annotations": annotations,
            "labels": {
                "app.kubernetes.io/managed-by": "nebius-cxcli",
                migration.CONTROLLER_BRIDGE_LABEL: "true",
            }
            if source_uid
            else {},
        },
        "data": {
            "slurm.conf": _ORIGINAL_SLURM_CONF,
            "custom_slurm.conf": "# optional customer additions\n",
            "unrelated.conf": "AccountingStorageType=accounting_storage/slurmdbd\n",
        },
    }


class _ConfigMapRunner:
    def __init__(self, *, crash_namespace: str) -> None:
        source = _config_map(
            namespace="soperator",
            uid="source-config-uid",
            resource_version="10",
        )
        bridge = _config_map(
            namespace=_BRIDGE_NAMESPACE,
            uid="bridge-config-uid",
            resource_version="20",
            source_uid="source-config-uid",
        )
        self.resources = {
            ("soperator", _CONFIG_NAME): source,
            (_BRIDGE_NAMESPACE, _CONFIG_NAME): bridge,
        }
        self.crash_namespace = crash_namespace
        self.patch_calls: list[str] = []
        self.exec_calls: list[tuple[str, ...]] = []
        self.jailed_config = {
            "apiVersion": "slurm.nebius.ai/v1alpha1",
            "kind": "JailedConfig",
            "metadata": {
                "namespace": "soperator",
                "name": "soperator-slurm-configs",
                "uid": "jailed-config-uid",
                "resourceVersion": "30",
            },
            "spec": {
                "configMap": {"name": _CONFIG_NAME},
                "items": [{"key": "slurm.conf", "path": "/etc/slurm/slurm.conf"}],
            },
        }

    def __call__(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        **_kwargs: Any,
    ) -> migration.SoperatorMigrationCommandResult:
        selected = tuple(str(item) for item in args)
        if "exec" in selected:
            self.exec_calls.append(selected)
        if "get" in selected and "jailedconfigs.slurm.nebius.ai" in selected:
            return _result(selected, stdout=json.dumps({"items": [self.jailed_config]}))
        if "get" in selected and "configmap" in selected:
            namespace = selected[selected.index("-n") + 1]
            name = selected[selected.index("configmap") + 1]
            return _result(selected, stdout=json.dumps(self.resources[(namespace, name)]))
        if "patch" in selected and "configmap" in selected:
            namespace = selected[selected.index("-n") + 1]
            name = selected[selected.index("configmap") + 1]
            resource = self.resources[(namespace, name)]
            patch = json.loads(input_text or "[]")
            assert selected[-3:] == ("--type=json", "--patch-file", "/dev/stdin")
            assert patch[:3] == [
                {
                    "op": "test",
                    "path": "/metadata/uid",
                    "value": resource["metadata"]["uid"],
                },
                {
                    "op": "test",
                    "path": "/metadata/resourceVersion",
                    "value": resource["metadata"]["resourceVersion"],
                },
                {
                    "op": "test",
                    "path": "/data/slurm.conf",
                    "value": _ORIGINAL_SLURM_CONF,
                },
            ]
            assert patch[3]["op"] == "replace"
            assert patch[3]["path"] == "/data/slurm.conf"
            resource["data"]["slurm.conf"] = patch[3]["value"]
            resource["metadata"]["resourceVersion"] = str(
                int(resource["metadata"]["resourceVersion"]) + 1
            )
            self.patch_calls.append(namespace)
            if self.crash_namespace == namespace:
                self.crash_namespace = ""
                raise _InjectedCrash
            return _result(selected)
        if "replace" in selected and selected[-4:] == ("-f", "-", "-o", "json"):
            replacement = json.loads(input_text or "{}")
            metadata = replacement["metadata"]
            namespace = metadata["namespace"]
            name = metadata["name"]
            current = self.resources[(namespace, name)]
            assert metadata["uid"] == current["metadata"]["uid"]
            assert metadata["resourceVersion"] == current["metadata"]["resourceVersion"]
            replacement["metadata"]["resourceVersion"] = str(
                int(current["metadata"]["resourceVersion"]) + 1
            )
            self.resources[(namespace, name)] = replacement
            return _result(selected, stdout=json.dumps(replacement))
        if "exec" in selected and selected[-2:] == (
            "cat",
            migration._SOPERATOR_LEGACY_SLURM_CONF,  # noqa: SLF001
        ):
            return _result(
                selected,
                stdout=self.resources[("soperator", _CONFIG_NAME)]["data"]["slurm.conf"],
            )
        if "exec" in selected and selected[-2:] == ("scontrol", "reconfigure"):
            return _result(selected)
        if "exec" in selected and selected[-2:] == ("scontrol", "ping"):
            return _result(selected, stdout="Slurmctld(primary) at controller-0 is UP\n")
        pytest.fail(f"unexpected command: {selected}")


def _journal_and_source(runner: _ConfigMapRunner) -> tuple[dict[str, Any], dict[str, Any]]:
    bridge = runner.resources[(_BRIDGE_NAMESPACE, _CONFIG_NAME)]
    source_image = f"registry.example/slurmctld@sha256:{'a' * 64}"
    attachment_sha256 = "b" * 64
    jail_attachment_sha256 = "e" * 64
    journal = {
        "stage": migration.BridgeStage.SUBSTRATE_READY.value,
        "namespace": _BRIDGE_NAMESPACE,
        "campaign_fingerprint": _CAMPAIGN_FINGERPRINT,
        "cluster_id": _CLUSTER_ID,
        "authority": {
            "owner": "source-singleton",
            "first_bridge_write_at": "",
            "source_restart_prohibited": False,
        },
        "source_binding": {"slurm_image_digest": source_image},
        "version_transition": {"target_image": f"registry.example/slurmctld@sha256:{'c' * 64}"},
        "node_groups": [
            {
                "slot": index,
                "id": f"bridge-node-group-{index}",
                "controller_spool_attachment_sha256": attachment_sha256,
                "jail_attachment_sha256": jail_attachment_sha256,
                "scheduling_failure_domain": {
                    "topology_key": "nebius.com/node-group-id",
                    "topology_value": f"bridge-node-group-{index}",
                    "node_group_id": f"bridge-node-group-{index}",
                    "node_name": f"bridge-node-{index}",
                    "node_uid": f"bridge-node-uid-{index}",
                },
            }
            for index in range(2)
        ],
        "kubernetes_resources": [
            {
                "kind": "PersistentVolume",
                "name": f"{migration.CONTROLLER_BRIDGE_STATE_PVC}-pv",
                "uid": "bridge-state-pv-uid",
            },
            {
                "kind": "PersistentVolumeClaim",
                "namespace": _BRIDGE_NAMESPACE,
                "name": migration.CONTROLLER_BRIDGE_STATE_PVC,
                "uid": "bridge-state-pvc-uid",
            },
            {
                "kind": "PersistentVolume",
                "name": f"{migration.CONTROLLER_BRIDGE_JAIL_PVC}-pv",
                "uid": "bridge-jail-pv-uid",
            },
            {
                "kind": "PersistentVolumeClaim",
                "namespace": _BRIDGE_NAMESPACE,
                "name": migration.CONTROLLER_BRIDGE_JAIL_PVC,
                "uid": "bridge-jail-pvc-uid",
            },
        ],
        "source_configuration": {},
        "mirrored_material": [
            {
                "kind": "ConfigMap",
                "name": _CONFIG_NAME,
                "uid": "bridge-config-uid",
                "source_uid": "source-config-uid",
                "material_sha256": migration._controller_bridge_material_fingerprint(bridge),  # noqa: SLF001
            }
        ],
        "shared_mount_canaries": [
            {
                "schema": migration.CONTROLLER_BRIDGE_MOUNT_CANARY_SCHEMA,
                "purpose": migration.CONTROLLER_BRIDGE_PRE_SOURCE_MUTATION_CANARY,
                "image": source_image,
                "token_sha256": "d" * 64,
                "controller_spool_attachment_sha256": attachment_sha256,
                "jail_attachment_sha256": jail_attachment_sha256,
                "storage": {
                    "controller_spool": {
                        "pv_name": f"{migration.CONTROLLER_BRIDGE_STATE_PVC}-pv",
                        "pv_uid": "bridge-state-pv-uid",
                        "pvc_name": migration.CONTROLLER_BRIDGE_STATE_PVC,
                        "pvc_uid": "bridge-state-pvc-uid",
                    },
                    "jail": {
                        "pv_name": f"{migration.CONTROLLER_BRIDGE_JAIL_PVC}-pv",
                        "pv_uid": "bridge-jail-pv-uid",
                        "pvc_name": migration.CONTROLLER_BRIDGE_JAIL_PVC,
                        "pvc_uid": "bridge-jail-pvc-uid",
                    },
                },
                "mount_paths": {"controller_spool": "/shared", "jail": "/jail"},
                "pods": [
                    {
                        "slot": index,
                        "pod_name": f"cxcli-controller-bridge-canary-{index}",
                        "pod_uid": f"bridge-canary-pod-uid-{index}",
                        "node_name": f"bridge-node-{index}",
                        "node_uid": f"bridge-node-uid-{index}",
                        "node_group_id": f"bridge-node-group-{index}",
                        "failure_domain": f"bridge-node-group-{index}",
                    }
                    for index in range(2)
                ],
                "bidirectional": True,
                "observed_at": "2026-07-12T10:01:00Z",
            }
        ],
    }
    source = {"configuration": {"config_map_names": [_CONFIG_NAME]}}
    return journal, source


def _run_configuration_crash_resume(
    crash_namespace: str,
) -> tuple[dict[str, Any], _ConfigMapRunner]:
    runner = _ConfigMapRunner(crash_namespace=crash_namespace)
    journal, _source = _journal_and_source(runner)
    durable_checkpoints: list[dict[str, Any]] = []
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            migration,
            "advance_bridge_stage",
            lambda state, stage: state.update({"stage": stage.value}),
        )
        monkeypatch.setattr(
            migration,
            "_reassert_controller_bridge_partition_pause_after_reconfigure",
            lambda **_kwargs: [],
        )
        with pytest.raises(_InjectedCrash):
            migration._configure_source_controller_for_bridge(  # noqa: SLF001
                journal=journal,
                kube_context="context",
                command_runner=runner,
                checkpoint_writer=lambda: durable_checkpoints.append(copy.deepcopy(journal)),
            )

        resumed = copy.deepcopy(durable_checkpoints[-1])
        assert resumed["stage"] == migration.BridgeStage.SUBSTRATE_READY.value
        configuration = resumed["source_configuration"]
        assert configuration["original_slurm_conf"] == _ORIGINAL_SLURM_CONF
        assert configuration["source_reference"] == {
            "jailed_config_name": "soperator-slurm-configs",
            "jailed_config_uid": "jailed-config-uid",
            "jailed_config_resource_version": "30",
            "config_map_name": _CONFIG_NAME,
            "config_map_uid": "source-config-uid",
            "config_key": "slurm.conf",
            "path": "/etc/slurm/slurm.conf",
        }
        assert configuration["original_timeouts"] == {
            "SlurmdTimeout": "300",
            "SlurmctldTimeout": "120",
        }
        assert configuration["copies"]["source"]["uid"] == "source-config-uid"
        assert configuration["copies"]["source"]["intent_resource_version"] == "10"
        assert configuration["copies"]["bridge"]["uid"] == "bridge-config-uid"
        assert configuration["copies"]["bridge"]["intent_resource_version"] == "20"
        bridge_contract._validate_source_configuration_transition(  # noqa: SLF001
            configuration,
            stage=migration.BridgeStage.SUBSTRATE_READY.value,
        )

        migration._configure_source_controller_for_bridge(  # noqa: SLF001
            journal=resumed,
            kube_context="context",
            command_runner=runner,
            checkpoint_writer=lambda: durable_checkpoints.append(copy.deepcopy(resumed)),
        )
        bridge_contract._validate_source_configuration_transition(  # noqa: SLF001
            resumed["source_configuration"],
            stage=migration.BridgeStage.SOURCE_CONFIGURED.value,
        )
    return resumed, runner


def test_source_config_patch_crash_reuses_checkpointed_timeout_preimage() -> None:
    journal, runner = _run_configuration_crash_resume("soperator")

    assert runner.patch_calls == ["soperator", _BRIDGE_NAMESPACE]
    assert journal["stage"] == migration.BridgeStage.SOURCE_CONFIGURED.value
    configuration = journal["source_configuration"]
    assert configuration["original_timeouts"] == {
        "SlurmdTimeout": "300",
        "SlurmctldTimeout": "120",
    }
    assert configuration["copies"]["source"]["state"] == "accepted"
    assert configuration["copies"]["bridge"]["state"] == "accepted"
    assert (
        "SlurmdTimeout=3600" in runner.resources[("soperator", _CONFIG_NAME)]["data"]["slurm.conf"]
    )


def test_bridge_config_patch_crash_reuses_checkpointed_timeout_preimage() -> None:
    journal, runner = _run_configuration_crash_resume(_BRIDGE_NAMESPACE)

    assert runner.patch_calls == ["soperator", _BRIDGE_NAMESPACE]
    assert journal["stage"] == migration.BridgeStage.SOURCE_CONFIGURED.value
    configuration = journal["source_configuration"]
    assert configuration["original_slurm_conf"] == _ORIGINAL_SLURM_CONF
    assert configuration["copies"]["source"]["accepted_resource_version"] == "11"
    assert configuration["copies"]["bridge"]["accepted_resource_version"] == "21"
    assert (
        runner.resources[("soperator", _CONFIG_NAME)]["data"]
        == runner.resources[(_BRIDGE_NAMESPACE, _CONFIG_NAME)]["data"]
    )


def test_pre_authority_exact_preimage_rollback_regenerates_corrected_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, runner = _run_configuration_crash_resume("soperator")
    for resource in runner.resources.values():
        resource["data"]["slurm.conf"] = _ORIGINAL_SLURM_CONF
        resource["metadata"]["resourceVersion"] = str(
            int(resource["metadata"]["resourceVersion"]) + 1
        )
    checkpoints: list[dict[str, Any]] = []
    monkeypatch.setattr(
        migration,
        "advance_bridge_stage",
        lambda state, stage: state.update({"stage": stage.value}),
    )
    monkeypatch.setattr(
        migration,
        "_reassert_controller_bridge_partition_pause_after_reconfigure",
        lambda **_kwargs: [],
    )

    migration._configure_source_controller_for_bridge(  # noqa: SLF001
        journal=journal,
        kube_context="context",
        command_runner=runner,
        checkpoint_writer=lambda: checkpoints.append(copy.deepcopy(journal)),
    )

    assert journal["stage"] == migration.BridgeStage.SOURCE_CONFIGURED.value
    assert len(journal["configuration_recoveries"]) == 1
    assert journal["configuration_recoveries"][0]["reason"] == (
        "operator-restored-exact-preimage-before-authority-transfer"
    )
    intended = journal["source_configuration"]["intended_slurm_conf"]
    assert "SlurmctldHost=controller-0(soperator-controller-svc)" in intended
    assert "SlurmctldHost=cxcli-slurm-controller-bridge-0" in intended
    assert runner.resources[("soperator", _CONFIG_NAME)]["data"]["slurm.conf"] == intended
    assert runner.resources[(_BRIDGE_NAMESPACE, _CONFIG_NAME)]["data"]["slurm.conf"] == intended
    bridge_contract._validate_configuration_recoveries(journal)  # noqa: SLF001
    invalid = copy.deepcopy(journal)
    invalid["configuration_recoveries"][0]["copies"].pop("bridge")
    with pytest.raises(ValueError, match="exact source and bridge copies"):
        bridge_contract._validate_configuration_recoveries(invalid)  # noqa: SLF001
    assert checkpoints


def test_pre_authority_partial_preimage_rollback_reapplies_only_restored_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, runner = _run_configuration_crash_resume("soperator")
    source = runner.resources[("soperator", _CONFIG_NAME)]
    source["data"]["slurm.conf"] = _ORIGINAL_SLURM_CONF
    source["metadata"]["resourceVersion"] = str(int(source["metadata"]["resourceVersion"]) + 1)
    runner.jailed_config["metadata"]["resourceVersion"] = str(
        int(runner.jailed_config["metadata"]["resourceVersion"]) + 1
    )
    runner.patch_calls.clear()
    checkpoints: list[dict[str, Any]] = []
    monkeypatch.setattr(
        migration,
        "advance_bridge_stage",
        lambda state, stage: state.update({"stage": stage.value}),
    )
    monkeypatch.setattr(
        migration,
        "_reassert_controller_bridge_partition_pause_after_reconfigure",
        lambda **_kwargs: [],
    )

    migration._configure_source_controller_for_bridge(  # noqa: SLF001
        journal=journal,
        kube_context="context",
        command_runner=runner,
        checkpoint_writer=lambda: checkpoints.append(copy.deepcopy(journal)),
    )

    assert journal["stage"] == migration.BridgeStage.SOURCE_CONFIGURED.value
    assert runner.patch_calls == ["soperator"]
    copies = journal["source_configuration"]["copies"]
    assert copies["source"]["state"] == "accepted"
    assert copies["source"]["recovery_count"] == 1
    assert copies["source"]["recovery_reason"] == (
        "operator-restored-exact-preimage-before-authority-transfer"
    )
    assert copies["bridge"]["state"] == "accepted"
    assert journal["source_configuration"]["source_reference_rebindings"] == [
        {
            "reason": "same-identity-jailed-config-resource-version-churn",
            "jailed_config_uid": "jailed-config-uid",
            "previous_resource_version": "30",
            "resource_version": "31",
            "rebound_at": journal["source_configuration"]["source_reference_rebindings"][0][
                "rebound_at"
            ],
        }
    ]
    assert (
        runner.resources[("soperator", _CONFIG_NAME)]["data"]
        == runner.resources[(_BRIDGE_NAMESPACE, _CONFIG_NAME)]["data"]
    )
    bridge_contract._validate_source_configuration_transition(  # noqa: SLF001
        journal["source_configuration"],
        stage=migration.BridgeStage.SOURCE_CONFIGURED.value,
    )
    assert checkpoints


def test_active_slurm_configuration_rejects_multiple_jailed_config_mappings() -> None:
    runner = _ConfigMapRunner(crash_namespace="")
    duplicate = copy.deepcopy(runner.jailed_config)
    duplicate["metadata"].update(
        {
            "name": "duplicate-slurm-configs",
            "uid": "duplicate-jailed-config-uid",
            "resourceVersion": "31",
        }
    )
    original_runner = runner.__call__

    def command_runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        **kwargs: Any,
    ) -> migration.SoperatorMigrationCommandResult:
        selected = tuple(str(item) for item in args)
        if "get" in selected and "jailedconfigs.slurm.nebius.ai" in selected:
            return _result(
                selected,
                stdout=json.dumps({"items": [runner.jailed_config, duplicate]}),
            )
        return original_runner(args, input_text=input_text, **kwargs)

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="exactly one JailedConfig item mapped",
    ):
        migration._controller_bridge_active_slurm_configuration(  # noqa: SLF001
            kube_context="context",
            command_runner=command_runner,
        )


def test_source_reconfigure_waits_for_exact_jailed_config_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _ConfigMapRunner(crash_namespace="")
    journal, _source = _journal_and_source(runner)
    original_runner = runner.__call__

    def stale_projection_runner(
        args: Sequence[str],
        *,
        input_text: str | None = None,
        **kwargs: Any,
    ) -> migration.SoperatorMigrationCommandResult:
        selected = tuple(str(item) for item in args)
        if "exec" in selected and selected[-2:] == (
            "cat",
            migration._SOPERATOR_LEGACY_SLURM_CONF,  # noqa: SLF001
        ):
            runner.exec_calls.append(selected)
            return _result(selected, stdout=_ORIGINAL_SLURM_CONF)
        return original_runner(args, input_text=input_text, **kwargs)

    monkeypatch.setattr(
        migration,
        "_reassert_controller_bridge_partition_pause_after_reconfigure",
        lambda **_kwargs: [],
    )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="has not yet projected the exact intended jailed slurm.conf",
    ):
        migration._configure_source_controller_for_bridge(  # noqa: SLF001
            journal=journal,
            kube_context="context",
            command_runner=stale_projection_runner,
            checkpoint_writer=lambda: None,
        )

    assert journal["stage"] == migration.BridgeStage.SUBSTRATE_READY.value
    assert not any(call[-2:] == ("scontrol", "reconfigure") for call in runner.exec_calls)


def _partition_pause_journal() -> tuple[
    dict[str, Any],
    migration.SlurmPartitionState,
    migration.SlurmPartitionState,
]:
    previous = migration.parse_scontrol_show_partition_states(
        "PartitionName=main State=UP Nodes=worker-[0-1]\n"
    )[0]
    applied = migration.parse_scontrol_show_partition_states(
        "PartitionName=main State=DOWN Nodes=worker-[0-1]\n"
    )[0]
    record = migration.slurm_partition_pause_records(
        partitions=("main",),
        states=(previous,),
    )[0].with_applied_observation(applied)
    return {"partition_pause": [record.as_payload()]}, previous, applied


def test_partition_pause_after_reconfigure_reuses_exact_down_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, _previous, applied = _partition_pause_journal()
    checkpoints: list[dict[str, Any]] = []
    monkeypatch.setattr(
        migration,
        "_external_upgrade_partition_state",
        lambda **_kwargs: applied,
    )
    monkeypatch.setattr(
        migration,
        "_kubectl_exec_login",
        lambda **_kwargs: pytest.fail("an exact DOWN observation must not be mutated"),
    )

    lines = migration._reassert_controller_bridge_partition_pause_after_reconfigure(  # noqa: SLF001
        journal=journal,
        kube_context="context",
        command_runner=lambda *_args, **_kwargs: pytest.fail("unexpected command"),
        checkpoint_writer=lambda: checkpoints.append(copy.deepcopy(journal)),
        checkpoint=None,
        operation_label="test bridge partition pause reassertion",
    )

    assert journal["partition_pause_reasserted"] == []
    assert journal["partition_pause_revalidated_at"]
    assert checkpoints
    assert "reasserted 0 partition(s)" in lines[0]


def test_partition_pause_after_reconfigure_reasserts_exact_previous_up_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, previous, applied = _partition_pause_journal()
    observations = iter((previous, applied))
    updates: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        migration,
        "_external_upgrade_partition_state",
        lambda **_kwargs: next(observations),
    )

    def update_partition(**kwargs: Any) -> migration.SoperatorMigrationCommandResult:
        updates.append(tuple(kwargs["args"]))
        return _result(kwargs["args"])

    monkeypatch.setattr(
        migration,
        "_kubectl_exec_observed_slurm_route",
        update_partition,
    )

    lines = migration._reassert_controller_bridge_partition_pause_after_reconfigure(  # noqa: SLF001
        journal=journal,
        kube_context="context",
        command_runner=lambda *_args, **_kwargs: pytest.fail("unexpected command"),
        checkpoint_writer=lambda: None,
        checkpoint=None,
        operation_label="test bridge partition pause reassertion",
    )

    assert updates == [("scontrol", "update", "PartitionName=main", "State=DOWN")]
    assert journal["partition_pause_reasserted"] == ["main"]
    assert "reasserted 1 partition(s)" in lines[0]


def test_partition_pause_after_reconfigure_rejects_unowned_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal, _previous, _applied = _partition_pause_journal()
    drifted = migration.parse_scontrol_show_partition_states(
        "PartitionName=main State=INACTIVE Nodes=worker-[0-1]\n"
    )[0]
    monkeypatch.setattr(
        migration,
        "_external_upgrade_partition_state",
        lambda **_kwargs: drifted,
    )

    with pytest.raises(RuntimeError, match="outside its exact checkpointed UP/DOWN pair"):
        migration._reassert_controller_bridge_partition_pause_after_reconfigure(  # noqa: SLF001
            journal=journal,
            kube_context="context",
            command_runner=lambda *_args, **_kwargs: pytest.fail("unexpected command"),
            checkpoint_writer=lambda: None,
            checkpoint=None,
            operation_label="test bridge partition pause reassertion",
        )
