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
_ORIGINAL_SLURM_CONF = (
    "ClusterName=old-cluster\nSlurmctldHost=controller-0\nSlurmdTimeout=300\nSlurmctldTimeout=120\n"
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
    annotations = {"nebius.ai/cxcli-source-uid": source_uid} if source_uid else {}
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "namespace": namespace,
            "name": _CONFIG_NAME,
            "uid": uid,
            "resourceVersion": resource_version,
            "annotations": annotations,
        },
        "data": {
            "slurm.conf": _ORIGINAL_SLURM_CONF,
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

    def __call__(
        self,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        **_kwargs: Any,
    ) -> migration.SoperatorMigrationCommandResult:
        selected = tuple(str(item) for item in args)
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
        if "exec" in selected and selected[-2:] == ("scontrol", "reconfigure"):
            return _result(selected)
        pytest.fail(f"unexpected command: {selected}")


def _journal_and_source(runner: _ConfigMapRunner) -> tuple[dict[str, Any], dict[str, Any]]:
    bridge = runner.resources[(_BRIDGE_NAMESPACE, _CONFIG_NAME)]
    source_image = f"registry.example/slurmctld@sha256:{'a' * 64}"
    attachment_sha256 = "b" * 64
    jail_attachment_sha256 = "e" * 64
    journal = {
        "stage": migration.BridgeStage.SUBSTRATE_READY.value,
        "namespace": _BRIDGE_NAMESPACE,
        "source_binding": {"slurm_image_digest": source_image},
        "version_transition": {"target_image": f"registry.example/slurmctld@sha256:{'c' * 64}"},
        "node_groups": [
            {
                "slot": index,
                "id": f"bridge-node-group-{index}",
                "controller_spool_attachment_sha256": attachment_sha256,
                "jail_attachment_sha256": jail_attachment_sha256,
                "scheduling_failure_domain": {
                    "node_name": f"bridge-node-{index}",
                    "node_uid": f"bridge-node-uid-{index}",
                    "zone": f"eu-north1-{chr(ord('a') + index)}",
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
                        "failure_domain": f"eu-north1-{chr(ord('a') + index)}",
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
    journal, source = _journal_and_source(runner)
    durable_checkpoints: list[dict[str, Any]] = []
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            migration,
            "advance_bridge_stage",
            lambda state, stage: state.update({"stage": stage.value}),
        )
        with pytest.raises(_InjectedCrash):
            migration._configure_source_controller_for_bridge(  # noqa: SLF001
                journal=journal,
                source=source,
                kube_context="context",
                command_runner=runner,
                checkpoint_writer=lambda: durable_checkpoints.append(copy.deepcopy(journal)),
            )

        resumed = copy.deepcopy(durable_checkpoints[-1])
        assert resumed["stage"] == migration.BridgeStage.SUBSTRATE_READY.value
        configuration = resumed["source_configuration"]
        assert configuration["original_slurm_conf"] == _ORIGINAL_SLURM_CONF
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
            source=source,
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
