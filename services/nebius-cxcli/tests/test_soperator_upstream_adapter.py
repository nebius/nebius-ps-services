from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from nebius_cxcli import cli
from nebius_cxcli.soperator_adapter import (
    SOPERATOR_ADAPTER_LABEL,
    SOPERATOR_ADAPTER_LABEL_VALUE,
    SOPERATOR_MONITORING_DASHBOARDS_POST_FLUX_DIGESTS,
    SOPERATOR_VM_STACK_CLEANUP_HOOK_DISABLED_PACKAGES,
    prepare_soperator_upgrade_adapter_handoff,
    render_soperator_monitoring_dashboard_documents,
    soperator_adapter_state_from_documents,
    soperator_monitoring_dashboards_require_post_flux,
    soperator_persistent_mount_bindings,
    soperator_persistent_mount_bindings_from_adapter_state,
)
from nebius_cxcli.soperator_adapter import (
    compile_upstream_soperator_values as _compile_upstream_soperator_values,
)
from nebius_cxcli.soperator_adapter import (
    render_soperator_adapter_documents as _render_soperator_adapter_documents,
)
from nebius_cxcli.soperator_populate_jail import switch_active_passive_jail_rootfs_values
from nebius_cxcli.soperator_release import SoperatorReleaseGraphNode
from soperator_fixtures import sample_snapshot

_RELEASE = sample_snapshot()
_DASHBOARD_FILES = (
    "cluster_health.json",
    "gpu_cluster_stats.json",
    "jobs_overview.json",
    "nfs_server_client.json",
    "slurm_controller.json",
    "workers_detailed_stats.json",
    "workers_overview.json",
)


def compile_upstream_soperator_values(values: dict[str, Any]):
    return _compile_upstream_soperator_values(values, release=_RELEASE)


def render_soperator_adapter_documents(values: dict[str, Any]):
    return _render_soperator_adapter_documents(values, release=_RELEASE)


def _release_with_monitoring_chart(digest: str):
    monitoring_chart = replace(
        _RELEASE.umbrella,
        name="helm-soperator-monitoring-dashboards",
        digest=digest,
        source_path="helm/soperator-monitoring-dashboards",
    )
    return replace(
        _RELEASE,
        charts={**dict(_RELEASE.charts), "monitoringDashboards": monitoring_chart},
    )


def _release_with_vm_stack_chart(*, package_sha256: str | None = None):
    chart, version, repository, expected_sha256, oci_digest = next(
        iter(SOPERATOR_VM_STACK_CLEANUP_HOOK_DISABLED_PACKAGES)
    )
    vm_stack = replace(
        _RELEASE.third_party_charts["certManager"],
        chart=chart,
        version=version,
        repository=repository,
        package_sha256=package_sha256 or expected_sha256,
        oci_digest=oci_digest,
    )
    return replace(
        _RELEASE,
        third_party_charts={
            **dict(_RELEASE.third_party_charts),
            "victoriaMetricsStack": vm_stack,
        },
        release_graph=(
            *tuple(
                node
                for node in _RELEASE.release_graph
                if node.release_name != "soperator-fluxcd-vm-stack"
            ),
            SoperatorReleaseGraphNode(
                release_name="soperator-fluxcd-vm-stack",
                namespace="flux-system",
                owner="third-party",
                stage=1,
                chart_key="victoriaMetricsStack",
                dependencies=(),
                is_main=False,
            ),
        ),
    )


def _write_dashboard_source(source_root: Path) -> Path:
    dashboard_dir = source_root / "helm" / "soperator-monitoring-dashboards" / "dashboards"
    dashboard_dir.mkdir(parents=True)
    for name in _DASHBOARD_FILES:
        (dashboard_dir / name).write_text(
            json.dumps({"title": name, "uid": Path(name).stem}) + "\n",
            encoding="utf-8",
        )
    return dashboard_dir


def _values() -> dict[str, Any]:
    role_affinity = [
        {
            "key": "nebius.com/node-group",
            "operator": "In",
            "values": ["system", "controller", "accounting", "worker"],
        }
    ]
    return {
        "clusterName": "example",
        "partitionConfiguration": {
            "configType": "structured",
            "partitions": [
                {
                    "name": "main",
                    "nodeSetRefs": ["worker"],
                    "policy": {
                        "default": True,
                        "state": "UP",
                        "maxTime": "INFINITE",
                        "priorityTier": 10,
                    },
                }
            ],
        },
        "slurmNodes": {
            "controller": {"k8sNodeFilterName": "controller"},
            "accounting": {
                "enabled": True,
                "k8sNodeFilterName": "accounting",
                "mariadbOperator": {"enabled": True},
            },
        },
        "nodesets": [
            {
                "name": "worker",
                "replicas": 1,
                "gpu": {"enabled": True},
                "slurmd": {
                    "resources": {
                        "cpu": "1",
                        "memory": "1Gi",
                        "ephemeralStorage": "1Gi",
                    },
                    "volumes": {"spool": {"emptyDir": {}}, "jail": {}},
                    "security": {"appArmorProfile": "unconfined"},
                },
                "munge": {
                    "resources": {
                        "cpu": "100m",
                        "memory": "128Mi",
                        "ephemeralStorage": "1Gi",
                    },
                    "security": {"appArmorProfile": "unconfined"},
                },
            }
        ],
        "gpuDriverJail": {"enabled": True},
        "jailRootfs": {
            "strategy": "activePassive",
            "activeSlot": "slot-a",
            "passiveSlot": "slot-b",
            "store": {
                "mountPath": "/mnt/jail-store",
                "rootfsPath": "/mnt/jail-store/rootfs",
            },
        },
        "jailPersistentMounts": [
            {
                "name": "jail-home",
                "mountPath": "/home",
                "localPath": "/mnt/jail-store/shared/home",
            }
        ],
        "volume": {
            "jail": {
                "type": "filestore",
                "size": "2Ti",
                "filestoreDeviceName": "jail",
            },
            "controllerSpool": {
                "name": "controller-spool",
                "type": "filestore",
                "size": "128Gi",
                "filestoreDeviceName": "controller-spool",
            },
            "accounting": {
                "enabled": True,
                "name": "accounting",
                "type": "filestore",
                "size": "128Gi",
                "filestoreDeviceName": "accounting",
            },
        },
        "storage": {
            "jail": {"matchExpressions": role_affinity},
            "controllerSpool": {"matchExpressions": role_affinity},
            "accounting": {"matchExpressions": role_affinity},
        },
        "sfs": {
            "filesystems": {
                "jail": {"filesystem_id": "filesystem-jail"},
                "controller-spool": {"filesystem_id": "filesystem-controller"},
                "accounting": {"filesystem_id": "filesystem-accounting"},
            }
        },
    }


def test_adapter_compiles_upstream_values_without_owning_product_resources() -> None:
    umbrella, contract = compile_upstream_soperator_values(_values())

    partition = umbrella["slurmCluster"]["overrideValues"]["partitionConfiguration"]["partitions"][
        0
    ]
    assert partition["config"] == ("Default=YES State=UP MaxTime=INFINITE PriorityTier=10")
    assert "policy" not in partition
    assert umbrella["slurmCluster"]["slurmClusterStorage"] == {"enabled": False}
    assert umbrella["nfsServer"] == {"enabled": False}
    assert umbrella["nodesets"]["version"] == "4.1.7"
    assert (
        umbrella["slurmCluster"]["overrideValues"]["images"]["populateJail"]
        == _RELEASE.populate_jail_image
    )
    assert contract["target_image_source"] == "upstream-default"
    assert "partitionProfile" not in umbrella["slurmCluster"]["overrideValues"]
    assert "topologyProfile" not in umbrella["slurmCluster"]["overrideValues"]
    assert contract["active_pvc"] == "jail-rootfs-slot-a-pvc"
    assert [
        (item.mount_path, item.pv_name, item.pvc_name)
        for item in soperator_persistent_mount_bindings(_values())
    ] == [("/home", "jail-home-pv", "jail-home-pvc")]
    worker_mounts = umbrella["nodesets"]["overrideValues"]["nodesets"][0]["slurmd"]["volumes"][
        "customVolumeMounts"
    ]
    assert {(item["name"], item["mountPath"]) for item in worker_mounts} == {
        ("nvidia-driver-root", "/run/nvidia/driver"),
        ("gpu-health-sysfs", "/mnt/jail/sys-host"),
    }

    documents, state = render_soperator_adapter_documents(_values())
    product_kinds = {"CustomResourceDefinition", "SlurmCluster", "NodeSet"}
    assert product_kinds.isdisjoint({document["kind"] for document in documents})
    assert all(
        document.get("metadata", {}).get("labels", {}).get(SOPERATOR_ADAPTER_LABEL)
        == SOPERATOR_ADAPTER_LABEL_VALUE
        for document in documents
    )
    persistent_volumes = [
        document for document in documents if document["kind"] == "PersistentVolume"
    ]
    assert {document["metadata"]["name"] for document in persistent_volumes} == {
        "accounting-pv",
        "controller-spool-pv",
        "jail-home-pv",
        "jail-rootfs-slot-a-pv",
        "jail-rootfs-slot-b-pv",
    }
    assert all(
        document["spec"]["persistentVolumeReclaimPolicy"] == "Retain"
        for document in persistent_volumes
    )
    daemonsets = [document for document in documents if document["kind"] == "DaemonSet"]
    assert len(daemonsets) == 3
    assert all(
        "@sha256:" in document["spec"]["template"]["spec"]["containers"][0]["image"]
        for document in daemonsets
    )
    assert state["filesystemId"] == "filesystem-jail"
    assert state["controllerSpool"]["filesystem_id"] == "filesystem-controller"
    assert state["accounting"]["filesystem_id"] == "filesystem-accounting"
    assert state["targetImage"] == _RELEASE.populate_jail_image
    assert state["targetImageSource"] == "upstream-default"
    parsed_state = soperator_adapter_state_from_documents(documents)
    assert parsed_state == state
    assert [
        (item.mount_path, item.pv_name, item.pvc_name)
        for item in soperator_persistent_mount_bindings_from_adapter_state(parsed_state)
    ] == [("/home", "jail-home-pv", "jail-home-pvc")]

    lifecycle_by_kind = {
        document["kind"]: document.get("metadata", {})
        .get("labels", {})
        .get("soperator.nebius.ai/lifecycle")
        for document in documents
    }
    assert lifecycle_by_kind["PersistentVolume"] == "protected"
    assert lifecycle_by_kind["DaemonSet"] == "recreatable"
    assert lifecycle_by_kind["ServiceAccount"] == "recreatable"
    local_paths = {
        document["metadata"]["name"]: document["spec"]["local"]["path"]
        for document in persistent_volumes
    }
    assert local_paths["controller-spool-pv"] == "/mnt/controller-spool/data"
    assert local_paths["accounting-pv"] == "/mnt/accounting/data"

    jail_daemonset = next(
        document
        for document in daemonsets
        if document["metadata"]["name"] == "nebius-cxcli-soperator-jail-mount"
    )
    mount_container = jail_daemonset["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item for item in mount_container["env"]}
    assert env["FILESYSTEM_ID"]["value"] == "filesystem-jail"
    assert env["NODE_NAME"]["valueFrom"]["fieldRef"]["fieldPath"] == "spec.nodeName"
    assert (
        len(
            jail_daemonset["spec"]["template"]["metadata"]["annotations"][
                "soperator.nebius.ai/mount-script-sha256"
            ]
        )
        == 64
    )
    assert set(env["CREATE_DIRS"]["value"].split(";")) == {
        "rootfs/slot-a",
        "rootfs/slot-b",
        "shared/home",
    }
    assert env["VERIFY_DIRS"]["value"] == ""
    assert mount_container["startupProbe"]["exec"]["command"][-1] == "verify"
    assert mount_container["readinessProbe"]["exec"]["command"][-1] == "verify"
    assert mount_container["livenessProbe"]["exec"]["command"][-1] == "verify"
    mount_script = next(
        document
        for document in documents
        if document.get("kind") == "ConfigMap"
        and document.get("metadata", {}).get("name") == "nebius-cxcli-soperator-mount"
    )["data"]["mount.sh"]
    assert 'receipt="$receipt_dir/$NODE_NAME"' in mount_script
    assert 'receipt="$proof_path/$receipt_dir_name/$NODE_NAME"' in mount_script
    assert 'chmod 0755 "$proof_path/.nebius-cxcli" "$receipt_dir"' in mount_script
    assert "umask 022" in mount_script
    assert "umask 077" not in mount_script

    controller = umbrella["slurmCluster"]["overrideValues"]["slurmNodes"]["controller"]
    assert controller["volumes"]["spool"] == {"volumeSourceName": "controller-spool"}
    assert {item["name"] for item in controller["customInitContainers"]} >= {
        "mount-gate-controller-spool",
        "mount-gate-controller-jail",
    }
    controller_spool_gate = next(
        item
        for item in controller["customInitContainers"]
        if item["name"] == "mount-gate-controller-spool"
    )
    assert controller_spool_gate["volumeMounts"] == [
        {
            "name": "controller-spool",
            "mountPath": "/proof",
            "readOnly": True,
        }
    ]
    assert "jailSubMounts" not in controller["volumes"]
    assert not any(
        item["name"].startswith("mount-gate-controller-jail-persistent-")
        for item in controller["customInitContainers"]
    )
    exporter = umbrella["slurmCluster"]["overrideValues"]["slurmNodes"]["exporter"]
    assert "jailSubMounts" not in exporter["volumes"]
    assert not any(
        item["name"].startswith("mount-gate-exporter-jail-persistent-")
        for item in exporter["customInitContainers"]
    )
    login = umbrella["slurmCluster"]["overrideValues"]["slurmNodes"]["login"]
    assert [item["mountPath"] for item in login["volumes"]["jailSubMounts"]] == ["/home"]
    worker = umbrella["nodesets"]["overrideValues"]["nodesets"][0]
    assert {item["name"] for item in worker["customInitContainers"]} >= {
        "mount-gate-worker-jail",
        "mount-gate-worker-jail-home",
    }
    gate = next(
        item for item in worker["customInitContainers"] if item["name"] == "mount-gate-worker-jail"
    )
    assert "/proof/.nebius-cxcli/mount-receipts/$NODE_NAME" in gate["command"][-1]


def test_rest_enabled_controller_waits_for_jwt_config_before_start() -> None:
    values = _values()
    values["slurmNodes"]["rest"] = {"enabled": True}

    umbrella, _contract = compile_upstream_soperator_values(values)

    controller = umbrella["slurmCluster"]["overrideValues"]["slurmNodes"]["controller"]
    controller_jail_gate = next(
        item
        for item in controller["customInitContainers"]
        if item["name"] == "mount-gate-controller-jail"
    )
    command = controller_jail_gate["command"][-1]
    assert "Waiting for Slurm REST JWT configuration" in command
    assert "AuthAltTypes=.*auth/jwt" in command
    assert "AuthAltParameters=.*jwt_key=" in command


def test_rest_disabled_controller_has_no_jwt_config_gate() -> None:
    values = _values()

    umbrella, _contract = compile_upstream_soperator_values(values)

    controller = umbrella["slurmCluster"]["overrideValues"]["slurmNodes"]["controller"]
    controller_jail_gate = next(
        item
        for item in controller["customInitContainers"]
        if item["name"] == "mount-gate-controller-jail"
    )
    assert "Waiting for Slurm REST JWT configuration" not in controller_jail_gate["command"][-1]


def test_adapter_requires_optional_persistent_directory_to_preexist() -> None:
    values = _values()
    values["jailPersistentMounts"].append(
        {
            "mountPath": "/srv/customer",
            "localPath": "/mnt/jail-store/shared/srv/customer",
        }
    )

    documents, _state = render_soperator_adapter_documents(values)

    jail_daemonset = next(
        document
        for document in documents
        if document.get("kind") == "DaemonSet"
        and document.get("metadata", {}).get("name") == "nebius-cxcli-soperator-jail-mount"
    )
    container = jail_daemonset["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item["value"] for item in container["env"] if "value" in item}
    assert env["VERIFY_DIRS"] == "shared/srv/customer"
    mount_script = next(
        document
        for document in documents
        if document.get("kind") == "ConfigMap"
        and document.get("metadata", {}).get("name") == "nebius-cxcli-soperator-mount"
    )["data"]["mount.sh"]
    assert 'if [ -L "$current" ]' in mount_script
    assert 'ensure_real_directory "$host_path/$relative" 0' in mount_script


def test_adapter_rejects_target_jail_image_override() -> None:
    values = _values()
    target = "registry.example.invalid/customer-jail@sha256:" + "7" * 64
    values["jailRootfs"]["targetImage"] = target

    with pytest.raises(ValueError, match="frozen official release"):
        compile_upstream_soperator_values(values)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda values: values["jailRootfs"].update({"targetImage": "repo/jail:latest"}),
        lambda values: values["jailRootfs"].update({"targetImage": ""}),
        lambda values: values.update({"images": {"populateJail": _RELEASE.populate_jail_image}}),
    ],
)
def test_adapter_rejects_mutable_or_second_target_jail_image_path(mutator) -> None:
    values = _values()
    mutator(values)

    with pytest.raises(ValueError, match="official release|targetImage"):
        compile_upstream_soperator_values(values)


def test_adapter_adopts_exact_controller_and_accounting_storage() -> None:
    values = _values()
    values["secrets"] = {"sshdKeysName": "soperator-sshd-keys"}
    values["volume"]["controllerSpool"].update(
        {
            "existingPvcName": "controller-spool-controller-0",
            "existingPvName": "pv-controller-spool",
            "existingStorageClassName": "compute-csi-network-ssd-ext4",
            "existingAccessModes": ["ReadWriteOnce"],
            "size": "50Gi",
        }
    )
    values["volume"]["accounting"].update(
        {
            "existingPvcName": "storage-soperator-acct-db-0",
            "existingPvName": "pv-accounting",
            "existingStorageClassName": "compute-csi-network-ssd-ext4",
            "existingAccessModes": ["ReadWriteOnce"],
            "size": "100Gi",
        }
    )

    umbrella, contract = compile_upstream_soperator_values(values)
    documents, state = render_soperator_adapter_documents(values)

    slurm = umbrella["slurmCluster"]["overrideValues"]
    sources = {item["name"]: item for item in slurm["volumeSources"]}
    assert sources["controller-spool"]["persistentVolumeClaim"]["claimName"] == (
        "controller-spool-controller-0"
    )
    assert slurm["secrets"]["sshdKeysName"] == "soperator-sshd-keys"
    mariadb = slurm["slurmNodes"]["accounting"]["mariadbOperator"]
    assert mariadb["protectedSecret"] is True
    assert mariadb["storage"] == {
        "volumeClaimTemplate": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "100Gi"}},
            "storageClassName": "compute-csi-network-ssd-ext4",
        }
    }
    names = {item["metadata"]["name"] for item in documents}
    assert "pv-controller-spool" not in names
    assert "controller-spool-controller-0" not in names
    assert "pv-accounting" not in names
    assert "nebius-cxcli-soperator-controller-spool-mount" in names
    assert "nebius-cxcli-soperator-accounting-mount" not in names
    assert contract["controller_spool"]["adopt_existing"] is True
    assert contract["accounting"]["adopt_existing"] is True
    assert state["controllerSpool"]["pvc_name"] == "controller-spool-controller-0"
    assert state["accounting"]["pvc_name"] == "storage-soperator-acct-db-0"
    spool_mount = next(
        item
        for item in documents
        if item.get("kind") == "DaemonSet"
        and item.get("metadata", {}).get("name") == "nebius-cxcli-soperator-controller-spool-mount"
    )
    spool_env = {
        item["name"]: item.get("value")
        for item in spool_mount["spec"]["template"]["spec"]["containers"][0]["env"]
        if "value" in item
    }
    assert spool_env["MOUNT_ID"] == "controller-spool"
    assert (
        spool_env["FILESYSTEM_ID"]
        == values["sfs"]["filesystems"]["controller-spool"]["filesystem_id"]
    )

    current_documents = [
        item
        for item in documents
        if not (
            item.get("kind") == "DaemonSet"
            and item.get("metadata", {}).get("name")
            == "nebius-cxcli-soperator-controller-spool-mount"
        )
    ]
    current = yaml.safe_dump_all(current_documents, sort_keys=False)
    replacement = yaml.safe_dump_all(documents, sort_keys=False)
    assert cli._soperator_upgrade_controller_spool_receipt_writer_is_exact(
        current,
        replacement,
    )
    changed = copy.deepcopy(documents)
    changed_spool_mount = next(
        item
        for item in changed
        if item.get("kind") == "DaemonSet"
        and item.get("metadata", {}).get("name") == "nebius-cxcli-soperator-controller-spool-mount"
    )
    changed_env = changed_spool_mount["spec"]["template"]["spec"]["containers"][0]["env"]
    next(item for item in changed_env if item["name"] == "FILESYSTEM_ID")["value"] = (
        "filesystem-other"
    )
    assert not cli._soperator_upgrade_controller_spool_receipt_writer_is_exact(
        current,
        yaml.safe_dump_all(changed, sort_keys=False),
    )

    values_without_recorded_filesystem = copy.deepcopy(values)
    values_without_recorded_filesystem["sfs"]["filesystems"]["controller-spool"].pop(
        "filesystem_id"
    )
    fallback_documents, fallback_state = render_soperator_adapter_documents(
        values_without_recorded_filesystem
    )
    fallback_writer = next(
        item
        for item in fallback_documents
        if item.get("kind") == "DaemonSet"
        and item.get("metadata", {}).get("name") == "nebius-cxcli-soperator-controller-spool-mount"
    )
    fallback_env = {
        item["name"]: item.get("value")
        for item in fallback_writer["spec"]["template"]["spec"]["containers"][0]["env"]
        if "value" in item
    }
    assert fallback_state["controllerSpool"]["filesystem_id"] == ""
    assert fallback_env["FILESYSTEM_ID"] == "filestore:controller-spool"
    fallback_current = yaml.safe_dump_all(
        [item for item in fallback_documents if item is not fallback_writer],
        sort_keys=False,
    )
    assert cli._soperator_upgrade_controller_spool_receipt_writer_is_exact(
        fallback_current,
        yaml.safe_dump_all(fallback_documents, sort_keys=False),
    )


def test_adapter_rejects_partial_existing_storage_identity() -> None:
    values = _values()
    values["volume"]["controllerSpool"]["existingPvcName"] = "controller-spool-controller-0"

    with pytest.raises(ValueError, match="must be set together"):
        compile_upstream_soperator_values(values)


def test_adapter_does_not_enable_observability_from_disabled_dcgm_defaults() -> None:
    values = _values()
    values["soperator-dcgm-exporter"] = {"enabled": False}

    umbrella, _contract = compile_upstream_soperator_values(values)

    assert umbrella["observability"] == {"enabled": False}
    assert umbrella["soperator"]["monitoringDashboards"] == {"enabled": False}


def test_adapter_uses_post_flux_dashboards_only_for_the_known_broken_digest() -> None:
    values = _values()
    values["observability"] = {"enabled": True}
    broken_digest = next(iter(SOPERATOR_MONITORING_DASHBOARDS_POST_FLUX_DIGESTS))
    broken_release = _release_with_monitoring_chart(broken_digest)
    ordinary_release = _release_with_monitoring_chart("sha256:" + "d" * 64)

    broken_umbrella, _ = _compile_upstream_soperator_values(values, release=broken_release)
    ordinary_umbrella, _ = _compile_upstream_soperator_values(values, release=ordinary_release)

    assert soperator_monitoring_dashboards_require_post_flux(values, release=broken_release)
    assert not soperator_monitoring_dashboards_require_post_flux(values, release=ordinary_release)
    assert broken_umbrella["soperator"]["monitoringDashboards"] == {"enabled": False}
    assert ordinary_umbrella["soperator"]["monitoringDashboards"] == {
        "enabled": True,
        "version": "4.1.7",
    }


def test_adapter_records_raw_child_cleanup_exception_without_mutating_outer_values() -> None:
    values = _values()
    values["observability"] = {
        "enabled": True,
        "vmStack": {
            "values": {
                "retained": {"setting": True},
                "victoria-metrics-operator": {
                    "crds": {"cleanup": {"enabled": True, "retain": "value"}}
                },
            }
        },
    }
    release = _release_with_vm_stack_chart()

    umbrella, contract = _compile_upstream_soperator_values(values, release=release)
    _documents, state = _render_soperator_adapter_documents(values, release=release)

    vm_values = umbrella["observability"]["vmStack"]["values"]
    assert vm_values["retained"] == {"setting": True}
    assert vm_values["victoria-metrics-operator"]["crds"]["cleanup"] == {
        "enabled": True,
        "retain": "value",
    }
    assert contract["chart_exceptions"] == [
        {
            "id": "victoria-metrics-cleanup-hook-disabled",
            "chart": "victoria-metrics-k8s-stack",
            "version": "0.39.4",
            "repository": "https://victoriametrics.github.io/helm-charts",
            "packageSha256": (
                "sha256:01e38a9632441d5c6c11f7c047fb99dc41084b4cc32e96c15ede612f85e02eb9"
            ),
            "reason": "frozen-package-uninstall-hook-image-unavailable",
        }
    ]
    assert state["chartExceptions"] == contract["chart_exceptions"]


def test_adapter_does_not_patch_a_different_vm_stack_package() -> None:
    values = _values()
    values["observability"] = {"enabled": True}
    release = _release_with_vm_stack_chart(package_sha256="sha256:" + "d" * 64)

    umbrella, contract = _compile_upstream_soperator_values(values, release=release)

    assert "vmStack" not in umbrella["observability"]
    assert contract["chart_exceptions"] == []


def test_adapter_does_not_record_raw_child_exception_when_vm_stack_is_disabled() -> None:
    values = _values()
    values["observability"] = {"enabled": True, "vmStack": {"enabled": False}}

    _umbrella, contract = _compile_upstream_soperator_values(
        values, release=_release_with_vm_stack_chart()
    )

    assert contract["chart_exceptions"] == []


def test_adapter_renders_verified_official_dashboards_as_owned_configmaps(
    tmp_path: Path,
) -> None:
    values = _values()
    values["observability"] = {"enabled": True}
    broken_digest = next(iter(SOPERATOR_MONITORING_DASHBOARDS_POST_FLUX_DIGESTS))
    release = _release_with_monitoring_chart(broken_digest)
    dashboard_dir = _write_dashboard_source(tmp_path)

    documents = render_soperator_monitoring_dashboard_documents(
        values,
        release=release,
        source_root=tmp_path,
    )

    assert [item["metadata"]["name"] for item in documents] == [
        f"soperator-{Path(name).stem.replace('_', '-')}" for name in _DASHBOARD_FILES
    ]
    assert len(documents) == len(_DASHBOARD_FILES)
    for document, source_name in zip(documents, _DASHBOARD_FILES, strict=True):
        metadata = document["metadata"]
        dashboard_name = Path(source_name).stem.replace("_", "-")
        assert metadata["namespace"] == "monitoring-system"
        assert metadata["labels"][SOPERATOR_ADAPTER_LABEL] == SOPERATOR_ADAPTER_LABEL_VALUE
        assert metadata["labels"]["soperator.nebius.ai/lifecycle"] == "recreatable"
        assert metadata["labels"]["grafana_dashboard"] == "1"
        assert metadata["annotations"]["soperator.nebius.ai/upstream-chart-digest"] == (
            broken_digest
        )
        assert metadata["annotations"]["soperator.nebius.ai/dashboard-sha256"].startswith("sha256:")
        assert document["data"] == {
            f"{dashboard_name}.json": (dashboard_dir / source_name).read_text(encoding="utf-8")
        }


def test_adapter_rejects_changed_digest_bound_dashboard_file_set(tmp_path: Path) -> None:
    values = _values()
    values["observability"] = {"enabled": True}
    release = _release_with_monitoring_chart(
        next(iter(SOPERATOR_MONITORING_DASHBOARDS_POST_FLUX_DIGESTS))
    )
    dashboard_dir = _write_dashboard_source(tmp_path)
    (dashboard_dir / _DASHBOARD_FILES[-1]).unlink()

    with pytest.raises(ValueError, match="file set"):
        render_soperator_monitoring_dashboard_documents(
            values,
            release=release,
            source_root=tmp_path,
        )


def test_adapter_rejects_symlinked_or_invalid_dashboard_payloads(tmp_path: Path) -> None:
    values = _values()
    values["observability"] = {"enabled": True}
    release = _release_with_monitoring_chart(
        next(iter(SOPERATOR_MONITORING_DASHBOARDS_POST_FLUX_DIGESTS))
    )
    dashboard_dir = _write_dashboard_source(tmp_path)
    source_path = dashboard_dir / _DASHBOARD_FILES[0]
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n", encoding="utf-8")
    source_path.unlink()
    source_path.symlink_to(outside)

    with pytest.raises(ValueError, match="regular JSON file"):
        render_soperator_monitoring_dashboard_documents(
            values,
            release=release,
            source_root=tmp_path,
        )

    source_path.unlink()
    source_path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="valid UTF-8 JSON"):
        render_soperator_monitoring_dashboard_documents(
            values,
            release=release,
            source_root=tmp_path,
        )


def test_adapter_rejects_downstream_only_qos_configuration() -> None:
    values = _values()
    values["qosConfiguration"] = {"enabled": True}

    with pytest.raises(ValueError, match="downstream-only"):
        compile_upstream_soperator_values(values)


@pytest.mark.parametrize(
    "key,value",
    [
        ("schedulingConfig", {"preemptType": "preempt/qos"}),
        ("storageClass", {"name": "legacy"}),
        ("uninstallCleanup", {"enabled": True}),
    ],
)
def test_adapter_rejects_other_downstream_only_values(key: str, value: object) -> None:
    values = _values()
    values[key] = value

    with pytest.raises(ValueError, match="downstream-only"):
        compile_upstream_soperator_values(values)


def test_adapter_routes_cert_manager_customization_to_upstream_owner() -> None:
    values = _values()
    values["certManager"] = {"privateKey": {"rotationPolicy": "Always"}}

    umbrella, _ = compile_upstream_soperator_values(values)

    assert umbrella["certManager"] == {
        "enabled": True,
        "version": "v1.19.6",
        "overrideValues": {"privateKey": {"rotationPolicy": "Always"}},
    }


def test_adapter_rejects_partition_policy_collisions() -> None:
    values = _values()
    partition = values["partitionConfiguration"]["partitions"][0]
    partition["config"] = "PriorityTier=20"

    with pytest.raises(ValueError, match="both set PriorityTier"):
        compile_upstream_soperator_values(values)


def test_adapter_rejects_unsafe_partition_tokens() -> None:
    values = _values()
    values["partitionConfiguration"]["partitions"][0]["policy"]["state"] = "UP Hidden=YES"

    with pytest.raises(ValueError, match="single tokens"):
        compile_upstream_soperator_values(values)


def test_adapter_rejects_owned_volume_source_name_collisions() -> None:
    values = _values()
    values["volumeSources"] = [{"name": "jail", "emptyDir": {}}]

    with pytest.raises(ValueError, match="volumeSources collide"):
        compile_upstream_soperator_values(values)


def test_protected_upgrade_handoff_replaces_generated_storage_aliases() -> None:
    values = switch_active_passive_jail_rootfs_values(_values())
    values["volume"]["controllerSpool"].update(
        {
            "existingPvcName": "controller-spool-controller-0",
            "existingPvName": "pv-controller-spool",
            "existingStorageClassName": "compute-csi-network-ssd-ext4",
            "existingAccessModes": ["ReadWriteOnce"],
            "size": "50Gi",
        }
    )

    prepared = prepare_soperator_upgrade_adapter_handoff(values)
    umbrella, _contract = compile_upstream_soperator_values(prepared)

    assert prepared["volumeSources"] == []
    sources = {
        item["name"]: item for item in umbrella["slurmCluster"]["overrideValues"]["volumeSources"]
    }
    assert sources["controller-spool"]["persistentVolumeClaim"]["claimName"] == (
        "controller-spool-controller-0"
    )
    assert sources["jail"]["persistentVolumeClaim"]["claimName"] == ("jail-rootfs-slot-b-pvc")


def test_adapter_rejects_owned_persistent_mount_name_collisions() -> None:
    values = _values()
    values["jailPersistentMounts"][0]["name"] = "jail"

    with pytest.raises(ValueError, match="volume source names collide"):
        compile_upstream_soperator_values(values)


def test_adapter_rejects_non_integer_gpu_resource_counts() -> None:
    values = _values()
    nodeset = values["nodesets"][0]
    nodeset["gpu"]["enabled"] = False
    nodeset["slurmd"]["resources"]["nvidia.com/gpu"] = "1Gi"

    with pytest.raises(ValueError, match="non-negative integer"):
        compile_upstream_soperator_values(values)


def test_adapter_rejects_gpu_mount_collisions() -> None:
    values = _values()
    values["nodesets"][0]["slurmd"]["volumes"]["customVolumeMounts"] = [
        {"name": "nvidia-driver-root", "mountPath": "/custom"}
    ]

    with pytest.raises(ValueError, match="adapter-owned GPU mounts"):
        compile_upstream_soperator_values(values)


def test_adapter_rejects_shell_delimiters_in_mount_paths() -> None:
    values = _values()
    values["jailPersistentMounts"][0]["localPath"] = "/mnt/jail-store/shared;unsafe"

    with pytest.raises(ValueError, match="normalized absolute path"):
        compile_upstream_soperator_values(values)


@pytest.mark.parametrize("unsafe", ["*", "?", "[abc]"])
def test_adapter_rejects_shell_globs_in_mount_paths(unsafe: str) -> None:
    values = _values()
    values["jailPersistentMounts"][0]["localPath"] = f"/mnt/jail-store/shared/{unsafe}"

    with pytest.raises(ValueError, match="normalized absolute path"):
        compile_upstream_soperator_values(values)


def test_adapter_routes_nodeconfigurator_values_to_upstream_subchart() -> None:
    values = _values()
    values["controllerManager"] = {
        "replicas": 2,
        "manager": {"env": {"slurmOperatorWatchNamespaces": "soperator"}},
    }
    values["serviceMonitor"] = {"enabled": True}
    values["customContainer"] = {"enabled": True, "command": ["sleep", "infinity"]}
    values["hostNetwork"] = True
    values["rebooter"] = {"enabled": False}

    umbrella, _contract = compile_upstream_soperator_values(values)

    soperator = umbrella["soperator"]
    assert soperator["overrideValues"] == {
        "controllerManager": {
            "replicas": 2,
            "manager": {
                "env": {
                    "slurmOperatorWatchNamespaces": "soperator",
                    "isApparmorCrdInstalled": True,
                    "isMariadbCrdInstalled": True,
                    "isPrometheusCrdInstalled": False,
                }
            },
        },
        "serviceMonitor": {"enabled": True},
    }
    assert soperator["nodeConfigurator"] == {
        "enabled": True,
        "version": "4.1.7",
        "overrideValues": {
            "customContainer": {
                "enabled": True,
                "command": ["sleep", "infinity"],
            },
            "hostNetwork": True,
            "rebooter": {"enabled": False},
        },
    }


def test_adapter_materializes_disabled_capabilities_in_partial_operator_override() -> None:
    values = _values()
    values["controllerManager"] = {"replicas": 2}
    values["mariadb-operator"] = {"installOperator": False}
    values["observability"] = {
        "enabled": True,
        "prometheusOperator": {"enabled": False},
    }

    umbrella, _contract = compile_upstream_soperator_values(values)

    assert umbrella["soperator"]["overrideValues"]["controllerManager"] == {
        "replicas": 2,
        "manager": {
            "env": {
                "isApparmorCrdInstalled": True,
                "isMariadbCrdInstalled": False,
                "isPrometheusCrdInstalled": False,
            }
        },
    }


def test_adapter_rejects_invalid_virtiofs_device_tags() -> None:
    values = _values()
    values["volume"]["jail"]["filestoreDeviceName"] = "-jail"

    with pytest.raises(ValueError, match="virtiofs device tag"):
        render_soperator_adapter_documents(values)


def test_external_nfs_is_statically_bound_without_default_storage_class() -> None:
    values = _values()
    values["externalNfs"] = {
        "enabled": True,
        "server": "10.10.0.5",
        "path": "/srv/nfs/home",
        "mountPath": "/home",
    }

    documents, _state = render_soperator_adapter_documents(values)
    nfs_pv = next(
        item
        for item in documents
        if item["kind"] == "PersistentVolume" and item["metadata"]["name"] == "external-nfs-home-pv"
    )
    nfs_pvc = next(
        item
        for item in documents
        if item["kind"] == "PersistentVolumeClaim"
        and item["metadata"]["name"] == "external-nfs-home-pvc"
    )

    assert nfs_pv["spec"]["storageClassName"] == ""
    assert nfs_pvc["spec"]["storageClassName"] == ""
    assert nfs_pvc["spec"]["volumeName"] == nfs_pv["metadata"]["name"]
