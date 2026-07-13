from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from nebius_cxcli import soperator_migration as migration
from nebius_cxcli.soperator_controller_bridge import CONTROLLER_BRIDGE_JAIL_PVC


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


def _old_controller_pod_spec() -> dict[str, Any]:
    return {
        "serviceAccountName": "slurm-controller",
        "initContainers": [
            {
                "name": "ensure-jail-mounted",
                "image": "registry.example/slurmctld:24.11.6",
                "volumeMounts": [{"name": "jail", "mountPath": "/mnt/jail"}],
            }
        ],
        "containers": [
            {
                "name": "slurmctld",
                "image": "registry.example/slurmctld:24.11.6",
                "volumeMounts": [
                    {"name": "controller-spool", "mountPath": "/var/spool/slurmctld"},
                    {"name": "jail", "mountPath": "/mnt/jail"},
                ],
            }
        ],
        "volumes": [
            {
                "name": "controller-spool",
                "persistentVolumeClaim": {
                    "claimName": "controller-spool-controller-0"
                },
            },
            {"name": "jail", "persistentVolumeClaim": {"claimName": "jail-pvc"}},
            {
                "name": "kube-api-access-old",
                "projected": {"sources": [{"serviceAccountToken": {"path": "token"}}]},
            },
        ],
    }


def test_old_controller_pod_retargets_jail_without_changing_runtime_mounts() -> None:
    source = _old_controller_pod_spec()

    rewritten, state_volume_name = (
        migration._controller_bridge_pod_spec_with_bridge_storage(  # noqa: SLF001
            pod_spec=source,
            source_state_pvc="controller-spool-controller-0",
            source_jail_pvc="jail-pvc",
        )
    )

    assert state_volume_name == "controller-spool"
    assert source == _old_controller_pod_spec()
    volumes = {item["name"]: item for item in rewritten["volumes"]}
    assert set(volumes) == {"controller-spool", "jail"}
    assert volumes["controller-spool"]["persistentVolumeClaim"]["claimName"] == (
        "controller-spool-controller-0"
    )
    assert volumes["jail"]["persistentVolumeClaim"]["claimName"] == (
        CONTROLLER_BRIDGE_JAIL_PVC
    )
    assert rewritten["initContainers"][0]["volumeMounts"] == [
        {"name": "jail", "mountPath": "/mnt/jail"}
    ]
    assert rewritten["containers"][0]["volumeMounts"] == [
        {"name": "controller-spool", "mountPath": "/var/spool/slurmctld"},
        {"name": "jail", "mountPath": "/mnt/jail"},
    ]


def test_old_controller_pod_rejects_unmapped_third_pvc() -> None:
    source = _old_controller_pod_spec()
    source["volumes"].append(
        {"name": "extra", "persistentVolumeClaim": {"claimName": "unsupported-pvc"}}
    )

    with pytest.raises(
        migration.SoperatorMigrationPhasePending,
        match="cannot mirror cross-namespace source PVC references: unsupported-pvc",
    ):
        migration._controller_bridge_pod_spec_with_bridge_storage(  # noqa: SLF001
            pod_spec=source,
            source_state_pvc="controller-spool-controller-0",
            source_jail_pvc="jail-pvc",
        )


def _mirrored_config_map() -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "namespace": "cxcli-soperator-upgrade-bridge",
            "name": "controller-material",
            "uid": "mirrored-config-uid",
            "annotations": {"nebius.ai/cxcli-source-uid": "source-config-uid"},
        },
        "immutable": True,
        "data": {"slurm.conf": "ClusterName=cluster"},
        "binaryData": {"opaque.bin": "value-one"},
    }


def _source_role() -> dict[str, Any]:
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "Role",
        "metadata": {
            "namespace": "soperator",
            "name": "slurm-controller-power-manager",
            "uid": "source-role-uid",
        },
        "rules": [
            {
                "apiGroups": ["slurm.nebius.ai"],
                "resources": ["nodesets"],
                "verbs": ["get", "list", "watch"],
            },
            {
                "apiGroups": ["slurm.nebius.ai"],
                "resources": ["nodesetpowerstates"],
                "verbs": ["get", "list", "watch", "create", "update"],
            },
            {
                "apiGroups": ["slurm.nebius.ai"],
                "resources": ["nodesetpowerstates/status"],
                "verbs": ["get"],
            },
        ],
    }


def _source_role_binding_contract() -> dict[str, Any]:
    role = _source_role()
    return {
        "schema": "nebius-cxcli-controller-bridge-source-role/v1",
        "required": True,
        "binding": {
            "namespace": "soperator",
            "name": "cxcli-controller-bridge-power-manager",
            "uid": "bridge-role-binding-uid",
            "role_ref": {
                "api_group": "rbac.authorization.k8s.io",
                "kind": "Role",
                "name": "slurm-controller-power-manager",
            },
            "service_account": {
                "namespace": "cxcli-soperator-upgrade-bridge",
                "name": "slurm-controller",
            },
        },
        "role": {
            "namespace": "soperator",
            "name": "slurm-controller-power-manager",
            "uid": "source-role-uid",
            "rules_sha256": migration._controller_bridge_role_rules_fingerprint(role),  # noqa: SLF001
        },
    }


def _live_role_binding(contract: Mapping[str, Any]) -> dict[str, Any]:
    role = contract["role"]
    binding = contract["binding"]
    service_account = binding["service_account"]
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": {
            "namespace": "soperator",
            "name": "cxcli-controller-bridge-power-manager",
            "uid": "bridge-role-binding-uid",
            "annotations": {
                "nebius.ai/cxcli-source-role-uid": role["uid"],
                "nebius.ai/cxcli-source-role-rules-sha256": role["rules_sha256"],
            },
        },
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "Role",
            "name": "slurm-controller-power-manager",
        },
        "subjects": [
            {
                "kind": "ServiceAccount",
                "namespace": service_account["namespace"],
                "name": service_account["name"],
            }
        ],
    }


def test_mirrored_material_fingerprint_is_canonical_and_binds_every_runtime_field() -> None:
    config = _mirrored_config_map()
    expected = migration._controller_bridge_material_fingerprint(config)  # noqa: SLF001
    reordered = copy.deepcopy(config)
    reordered["data"] = dict(reversed(tuple(reordered["data"].items())))
    reordered["binaryData"] = dict(reversed(tuple(reordered["binaryData"].items())))

    assert (
        migration._controller_bridge_material_fingerprint(reordered)  # noqa: SLF001
        == expected
    )

    mutations = (
        lambda item: item["data"].update({"slurm.conf": "ClusterName=replacement"}),
        lambda item: item["binaryData"].update({"opaque.bin": "value-two"}),
        lambda item: item.update({"type": "example/type"}),
        lambda item: item.update({"immutable": False}),
        lambda item: item["metadata"]["annotations"].update(
            {"nebius.ai/cxcli-source-uid": "replacement-source-config-uid"}
        ),
    )
    for mutate in mutations:
        changed = copy.deepcopy(config)
        mutate(changed)
        assert (
            migration._controller_bridge_material_fingerprint(changed)  # noqa: SLF001
            != expected
        )


def test_source_role_rules_fingerprint_is_semantically_canonical() -> None:
    role = _source_role()
    expected = migration._controller_bridge_role_rules_fingerprint(role)  # noqa: SLF001
    reordered = copy.deepcopy(role)
    reordered["rules"].reverse()
    for rule in reordered["rules"]:
        for values in rule.values():
            values.reverse()

    assert (
        migration._controller_bridge_role_rules_fingerprint(reordered)  # noqa: SLF001
        == expected
    )

    reordered["rules"][0]["verbs"].append("delete")
    assert (
        migration._controller_bridge_role_rules_fingerprint(reordered)  # noqa: SLF001
        != expected
    )


@pytest.mark.parametrize(
    "mutation",
    (
        lambda role: role["rules"].append(
            {"apiGroups": [""], "resources": ["pods"], "verbs": ["get"]}
        ),
        lambda role: role["rules"][1]["verbs"].append("delete"),
        lambda role: role["rules"][0].update({"resourceNames": ["worker"]}),
    ),
)
def test_source_role_rules_reject_access_outside_exact_power_manager_allowlist(
    mutation: Any,
) -> None:
    role = _source_role()
    mutation(role)

    with pytest.raises(RuntimeError, match="Role rule|outside the allowlisted"):
        migration._validate_controller_bridge_role_rules_allowlist(role)  # noqa: SLF001


def test_mirrored_objects_pin_exact_source_role_uid_and_rules() -> None:
    source_role = _source_role()
    role_bindings = {
        "items": [
            {
                "roleRef": {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "Role",
                    "name": "slurm-controller-power-manager",
                },
                "subjects": [
                    {
                        "kind": "ServiceAccount",
                        "namespace": "soperator",
                        "name": "slurm-controller",
                    }
                ],
            }
        ]
    }

    def runner(
        args: Sequence[str],
        **_kwargs: Any,
    ) -> migration.SoperatorMigrationCommandResult:
        if "serviceaccount" in args:
            payload = {
                "metadata": {"uid": "source-service-account-uid"},
                "imagePullSecrets": [],
            }
        elif "rolebindings.rbac.authorization.k8s.io" in args:
            payload = role_bindings
        elif "role.rbac.authorization.k8s.io" in args:
            payload = source_role
        else:
            raise AssertionError(args)
        return _result(args, stdout=json.dumps(payload))

    objects = migration._controller_bridge_mirrored_objects(  # noqa: SLF001
        source={"configuration": {}, "munge": {}, "jwt": {}},
        source_pod_spec={"serviceAccountName": "slurm-controller"},
        namespace="cxcli-soperator-upgrade-bridge",
        kube_context="context",
        command_runner=runner,
    )
    binding = next(item for item in objects if item["kind"] == "RoleBinding")
    service_account = next(item for item in objects if item["kind"] == "ServiceAccount")
    annotations = binding["metadata"]["annotations"]

    assert service_account["automountServiceAccountToken"] is False
    assert annotations["nebius.ai/cxcli-source-role-uid"] == "source-role-uid"
    assert annotations["nebius.ai/cxcli-source-role-rules-sha256"] == (
        migration._controller_bridge_role_rules_fingerprint(source_role)  # noqa: SLF001
    )
    assert binding["roleRef"]["name"] == "slurm-controller-power-manager"


def test_source_role_uid_and_rules_are_written_to_the_bridge_journal() -> None:
    contract = _source_role_binding_contract()
    live_binding = _live_role_binding(contract)
    expected_binding = copy.deepcopy(live_binding)
    expected_binding["metadata"].pop("uid")

    def runner(
        args: Sequence[str],
        **_kwargs: Any,
    ) -> migration.SoperatorMigrationCommandResult:
        assert "rolebinding.rbac.authorization.k8s.io" in args
        return _result(args, stdout=json.dumps(live_binding))

    observed = migration._controller_bridge_source_role_binding_contract(  # noqa: SLF001
        resources=[expected_binding],
        bridge_namespace="cxcli-soperator-upgrade-bridge",
        kube_context="context",
        command_runner=runner,
    )

    assert observed == contract


def _writer_boundary_fixture() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    config = _mirrored_config_map()
    source_role = _source_role()
    contract = _source_role_binding_contract()
    live_binding = _live_role_binding(contract)
    journal = {
        "namespace": "cxcli-soperator-upgrade-bridge",
        "mirrored_material": [
            {
                "kind": "ConfigMap",
                "name": "controller-material",
                "uid": "mirrored-config-uid",
                "source_uid": "source-config-uid",
                "material_sha256": migration._controller_bridge_material_fingerprint(  # noqa: SLF001
                    config
                ),
            }
        ],
        "source_role_binding": contract,
    }
    return journal, config, source_role, live_binding


def _writer_boundary_runner(
    *,
    config: Mapping[str, Any],
    source_role: Mapping[str, Any],
    live_binding: Mapping[str, Any],
) -> migration.SoperatorMigrationCommandRunner:
    def runner(
        args: Sequence[str],
        **_kwargs: Any,
    ) -> migration.SoperatorMigrationCommandResult:
        if "configmap" in args:
            payload = config
        elif "rolebinding.rbac.authorization.k8s.io" in args:
            payload = live_binding
        elif "role.rbac.authorization.k8s.io" in args:
            payload = source_role
        else:
            raise AssertionError(args)
        return _result(args, stdout=json.dumps(payload))

    return runner


@pytest.mark.parametrize("drift", ("role_uid", "role_rules", "role_ref", "subject"))
def test_writer_boundary_revalidation_rejects_source_role_drift(drift: str) -> None:
    journal, config, source_role, live_binding = _writer_boundary_fixture()
    if drift == "role_uid":
        source_role["metadata"]["uid"] = "replacement-role-uid"
    elif drift == "role_rules":
        source_role["rules"][0]["verbs"].append("delete")
    elif drift == "role_ref":
        live_binding["roleRef"]["name"] = "replacement-role"
    else:
        live_binding["subjects"][0]["name"] = "replacement-service-account"

    with pytest.raises(RuntimeError, match="recovery-required"):
        migration._revalidate_controller_bridge_mirrored_material(  # noqa: SLF001
            journal=journal,
            kube_context="context",
            command_runner=_writer_boundary_runner(
                config=config,
                source_role=source_role,
                live_binding=live_binding,
            ),
        )


def test_writer_boundary_revalidation_accepts_exact_source_role_contract() -> None:
    journal, config, source_role, live_binding = _writer_boundary_fixture()

    migration._revalidate_controller_bridge_mirrored_material(  # noqa: SLF001
        journal=journal,
        kube_context="context",
        command_runner=_writer_boundary_runner(
            config=config,
            source_role=source_role,
            live_binding=live_binding,
        ),
    )
