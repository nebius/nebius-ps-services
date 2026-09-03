from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import nebius_cxcli.flux_ops as flux_ops
from nebius_cxcli.paths import ProjectPaths
from nebius_cxcli.soperator_failures import (
    SoperatorMainWorkloadIdentity,
    SoperatorMainWorkloadTerminalError,
    SoperatorSafetyPauseError,
)
from nebius_cxcli.soperator_flux_graph import SOPERATOR_GRAPH_SCHEMA


def _condition(condition_type: str = "Ready") -> list[dict[str, str]]:
    return [{"type": condition_type, "status": "True"}]


def _slurm_cluster_conditions() -> list[dict[str, str]]:
    return [
        {"type": condition_type, "status": "True"}
        for condition_type in (
            "CommonAvailable",
            "ControllersAvailable",
            "LoginAvailable",
            "AccountingAvailable",
            "SConfigControllerAvailable",
        )
    ]


def _contract() -> dict[str, Any]:
    return {
        "clusterName": "example",
        "releases": [
            {
                "releaseName": "soperator-fluxcd-soperator",
                "sourceKind": "OCIRepository",
                "sourceName": "soperator-upstream-operator",
                "revision": "sha256:" + "1" * 64,
            }
        ],
    }


def _responses(*, observed_generation: int = 1, unexpected: bool = False):
    release = {
        "metadata": {
            "name": "soperator-fluxcd-soperator",
            "namespace": "flux-system",
            "uid": "release-uid",
            "generation": 1,
            "labels": {"soperator.nebius.ai/release-graph": "nebius-cxcli"},
        },
        "spec": {
            "chartRef": {
                "kind": "OCIRepository",
                "name": "soperator-upstream-operator",
                "namespace": "flux-system",
            }
        },
        "status": {
            "observedGeneration": observed_generation,
            "conditions": _condition(),
        },
    }
    release_items = [release]
    if unexpected:
        release_items.append({"metadata": {"name": "soperator-fluxcd-foreign"}})
    return {
        "helmreleases.helm.toolkit.fluxcd.io": {"items": release_items},
        "ocirepository": {
            "metadata": {
                "name": "soperator-upstream-operator",
                "namespace": "flux-system",
                "uid": "source-uid",
                "generation": 1,
            },
            "status": {
                "conditions": _condition(),
                "artifact": {"revision": "sha256:" + "1" * 64},
            },
        },
        "daemonsets.apps": {
            "items": [
                {
                    "metadata": {
                        "name": "jail-mount",
                        "namespace": "soperator",
                        "uid": "daemonset-uid",
                        "generation": 2,
                    },
                    "status": {
                        "observedGeneration": 2,
                        "desiredNumberScheduled": 3,
                        "numberReady": 3,
                    },
                }
            ]
        },
        "persistentvolumes": {
            "items": [
                {
                    "metadata": {"name": "state-pv", "uid": "pv-uid"},
                    "spec": {
                        "storageClassName": "slurm-local-pv",
                        "persistentVolumeReclaimPolicy": "Retain",
                        "local": {"path": "/mnt/state"},
                    },
                    "status": {"phase": "Bound"},
                }
            ]
        },
        "persistentvolumeclaims": {
            "items": [
                {
                    "metadata": {
                        "name": "state",
                        "namespace": "soperator",
                        "uid": "pvc-uid",
                    },
                    "spec": {
                        "storageClassName": "slurm-local-pv",
                        "volumeName": "state-pv",
                    },
                    "status": {"phase": "Bound"},
                }
            ]
        },
        "pods": {
            "items": [
                {
                    "metadata": {
                        "name": "controller-0",
                        "namespace": "soperator",
                        "uid": "controller-pod-uid",
                    },
                    "status": {"phase": "Running", "conditions": _condition()},
                },
                {
                    "metadata": {
                        "name": "completed-check",
                        "namespace": "soperator",
                        "uid": "check-pod-uid",
                    },
                    "status": {"phase": "Succeeded"},
                },
            ]
        },
        "slurmclusters.slurm.nebius.ai": {
            "metadata": {
                "name": "example",
                "namespace": "soperator",
                "uid": "cluster-uid",
                "generation": 4,
            },
            "status": {
                "phase": "Available",
                "conditions": _slurm_cluster_conditions(),
            },
        },
        "nodesets.slurm.nebius.ai": {
            "items": [
                {
                    "metadata": {
                        "name": "worker-0",
                        "namespace": "soperator",
                        "uid": "nodeset-uid",
                        "generation": 3,
                    },
                    "spec": {"replicas": 2},
                    "status": {"phase": "Ready", "replicas": 2},
                }
            ]
        },
    }


def _write_main_workload_flux_graph(flux_dir: Path) -> None:
    source_name = "soperator-upstream-umbrella"
    release_name = "cxcli-soperator-fluxcd-slurm-cluster"
    graph = {
        "schema": SOPERATOR_GRAPH_SCHEMA,
        "releases": [
            {
                "releaseName": release_name,
                "namespace": "flux-system",
                "sourceKind": "OCIRepository",
                "sourceName": source_name,
                "revision": "sha256:" + "1" * 64,
                "isMain": True,
            }
        ],
    }
    documents = {
        "soperator-release-graph.yaml": {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "nebius-cxcli-soperator-release-graph",
                "namespace": "flux-system",
            },
            "data": {"graph.json": json.dumps(graph)},
        },
        "source.yaml": {
            "apiVersion": "source.toolkit.fluxcd.io/v1",
            "kind": "OCIRepository",
            "metadata": {"name": source_name, "namespace": "flux-system"},
            "spec": {"url": "oci://example.invalid/soperator"},
        },
        "release.yaml": {
            "apiVersion": "helm.toolkit.fluxcd.io/v2",
            "kind": "HelmRelease",
            "metadata": {"name": release_name, "namespace": "flux-system"},
            "spec": {
                "chartRef": {
                    "kind": "OCIRepository",
                    "name": source_name,
                    "namespace": "flux-system",
                }
            },
        },
    }
    for name, payload in documents.items():
        (flux_dir / name).write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    (flux_dir / "kustomization.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "resources": [f"./{name}" for name in documents],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _main_workload_payload(*, observed_generation: int = 4) -> dict[str, Any]:
    return {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {
            "name": "cxcli-soperator-fluxcd-slurm-cluster",
            "namespace": "flux-system",
            "uid": "main-release-uid",
            "generation": 4,
        },
        "spec": {
            "chartRef": {
                "kind": "OCIRepository",
                "name": "soperator-upstream-umbrella",
                "namespace": "flux-system",
            }
        },
        "status": {
            "observedGeneration": observed_generation,
            "conditions": [
                {"type": "Stalled", "status": "True", "reason": "RetriesExceeded"},
                {"type": "Ready", "status": "False", "reason": "InstallFailed"},
            ],
        },
    }


def _main_source_payload() -> dict[str, Any]:
    return {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "OCIRepository",
        "metadata": {
            "name": "soperator-upstream-umbrella",
            "namespace": "flux-system",
        },
        "status": {
            "conditions": _condition(),
            "artifact": {"digest": "sha256:" + "1" * 64},
        },
    }


def _frozen_main_identity(*, uid: str = "main-release-uid") -> SoperatorMainWorkloadIdentity:
    return SoperatorMainWorkloadIdentity(
        api_version="helm.toolkit.fluxcd.io/v2",
        kind="HelmRelease",
        namespace="flux-system",
        name="cxcli-soperator-fluxcd-slurm-cluster",
        source_kind="OCIRepository",
        source_name="soperator-upstream-umbrella",
        source_revision="sha256:" + "1" * 64,
        uid=uid,
        generation=4,
        observed_generation=4,
    )


def test_exact_graph_main_stall_is_the_only_terminal_upgrade_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flux_dir = tmp_path / "flux"
    flux_dir.mkdir()
    _write_main_workload_flux_graph(flux_dir)
    monkeypatch.setattr(flux_ops, "_require_binary", lambda _name: None)
    monkeypatch.setattr(
        flux_ops,
        "_kubectl_get_target",
        lambda target, **_kwargs: (
            _main_source_payload() if target.is_source else _main_workload_payload(),
            "",
        ),
    )
    monkeypatch.setattr(flux_ops, "_kubectl_json", lambda *_args, **_kwargs: _main_source_payload())

    with pytest.raises(SoperatorMainWorkloadTerminalError) as caught:
        flux_ops.wait_for_rendered_flux_resources(
            SimpleNamespace(flux_dir=flux_dir),
            timeout_seconds=30,
            poll_interval_seconds=0.01,
            main_workload_identity=_frozen_main_identity(),
        )

    assert caught.value.identity is not None
    assert caught.value.identity.resource == "flux-system/cxcli-soperator-fluxcd-slurm-cluster"
    assert caught.value.identity.uid == "main-release-uid"
    assert caught.value.identity.generation == 4


def test_generic_flux_wait_does_not_authenticate_main_terminal_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flux_dir = tmp_path / "flux"
    flux_dir.mkdir()
    _write_main_workload_flux_graph(flux_dir)
    monkeypatch.setattr(
        flux_ops,
        "_kubectl_get_target",
        lambda target, **_kwargs: (
            _main_source_payload() if target.is_source else _main_workload_payload(),
            "",
        ),
    )

    targets = flux_ops._flux_wait_targets(flux_dir)
    statuses, all_ready, only_sources_pending, _block = flux_ops._flux_status_block(
        targets,
        env={},
        started_at=0,
    )

    main_status = next(status for status in statuses if status.target.is_soperator_main)
    assert all_ready is False
    assert only_sources_pending is False
    assert main_status.is_terminal_failure is False
    assert main_status.main_workload_identity is None


def test_flux_wait_synthesizes_umbrella_owned_release_graph_targets(
    tmp_path: Path,
) -> None:
    flux_dir = tmp_path / "flux"
    flux_dir.mkdir()
    _write_main_workload_flux_graph(flux_dir)
    release_path = flux_dir / "release.yaml"
    umbrella = yaml.safe_load(release_path.read_text(encoding="utf-8"))
    umbrella["metadata"]["name"] = "soperator-controller"
    umbrella["metadata"]["namespace"] = "soperator-system"
    release_path.write_text(yaml.safe_dump(umbrella, sort_keys=False), encoding="utf-8")

    targets = flux_ops._flux_wait_targets(flux_dir)

    main = next(target for target in targets if target.is_soperator_main)
    root = next(target for target in targets if target.name == "soperator-controller")
    assert (main.namespace, main.name, main.kind) == (
        "flux-system",
        "cxcli-soperator-fluxcd-slurm-cluster",
        "HelmRelease",
    )
    assert main.is_soperator_graph_member is True
    assert root.is_soperator_graph_member is False


def test_rendered_flux_api_version_gaps_detects_unserved_stable_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flux_dir = tmp_path / "flux"
    flux_dir.mkdir()
    _write_main_workload_flux_graph(flux_dir)
    paths = SimpleNamespace(flux_dir=flux_dir)

    def _crd_payload(*, oci_versions: tuple[str, ...]) -> dict[str, Any]:
        return {
            "items": [
                {
                    "spec": {
                        "group": "source.toolkit.fluxcd.io",
                        "names": {"kind": "OCIRepository"},
                        "versions": [{"name": version, "served": True} for version in oci_versions],
                    }
                },
                {
                    "spec": {
                        "group": "helm.toolkit.fluxcd.io",
                        "names": {"kind": "HelmRelease"},
                        "versions": [{"name": "v2", "served": True}],
                    }
                },
            ]
        }

    monkeypatch.setattr(
        flux_ops,
        "_kubectl_json",
        lambda *_args, **_kwargs: _crd_payload(oci_versions=("v1beta2",)),
    )
    assert flux_ops.rendered_flux_api_version_gaps(paths) == (
        "source.toolkit.fluxcd.io/v1/OCIRepository",
    )

    monkeypatch.setattr(
        flux_ops,
        "_kubectl_json",
        lambda *_args, **_kwargs: _crd_payload(oci_versions=("v1", "v1beta2")),
    )
    assert flux_ops.rendered_flux_api_version_gaps(paths) == ()


def test_flux_api_upgrade_uses_bridge_migration_before_configured_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SimpleNamespace(flux_dir=tmp_path)
    observations = iter(
        [
            ("source.toolkit.fluxcd.io/v1/OCIRepository",),
            (),
            (),
        ]
    )
    calls: list[object] = []
    monkeypatch.setattr(
        flux_ops,
        "rendered_flux_api_version_gaps",
        lambda _paths, *, extra_env=None: next(observations),
    )
    monkeypatch.setattr(
        flux_ops,
        "_install_flux_controller_manifest",
        lambda url, **kwargs: (
            calls.append(("bridge", url, kwargs.get("extra_env")))
            or flux_ops.FluxApplySummary(
                created=0,
                configured=1,
                unchanged=0,
                other=0,
            )
        ),
    )
    monkeypatch.setattr(flux_ops, "resolve_flux_binary", lambda: "/managed/flux")
    monkeypatch.setattr(
        flux_ops,
        "_run_captured",
        lambda command, **kwargs: (
            calls.append(("run", tuple(command), kwargs))
            or subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(
        flux_ops,
        "install_flux_controllers",
        lambda **kwargs: (
            calls.append(("configured", kwargs.get("extra_env")))
            or "https://example.invalid/v2.8.0/install.yaml"
        ),
    )
    authority_calls: list[str] = []

    installed = flux_ops.upgrade_flux_controllers_for_rendered_apis(
        paths,
        extra_env={"KUBECONFIG": "/tmp/kubeconfig"},
        assert_authority=lambda: authority_calls.append("checked"),
    )

    assert installed == (
        "https://github.com/fluxcd/flux2/releases/download/v2.6.4/install.yaml",
        "https://example.invalid/v2.8.0/install.yaml",
    )
    assert authority_calls == ["checked", "checked", "checked"]
    assert calls[0][0] == "bridge"
    assert calls[1][0:2] == ("run", ("/managed/flux", "migrate"))
    assert calls[2] == ("configured", {"KUBECONFIG": "/tmp/kubeconfig"})


def test_stale_main_stall_enters_typed_safety_pause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flux_dir = tmp_path / "flux"
    flux_dir.mkdir()
    _write_main_workload_flux_graph(flux_dir)
    monkeypatch.setattr(flux_ops, "_require_binary", lambda _name: None)
    monkeypatch.setattr(
        flux_ops,
        "_kubectl_get_target",
        lambda target, **_kwargs: (
            _main_source_payload()
            if target.is_source
            else _main_workload_payload(observed_generation=3),
            "",
        ),
    )

    with pytest.raises(SoperatorSafetyPauseError, match="current generation"):
        flux_ops.wait_for_rendered_flux_resources(
            SimpleNamespace(flux_dir=flux_dir),
            timeout_seconds=30,
            poll_interval_seconds=0.01,
            main_workload_identity=_frozen_main_identity(),
        )


def test_recreated_main_stall_cannot_stop_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flux_dir = tmp_path / "flux"
    flux_dir.mkdir()
    _write_main_workload_flux_graph(flux_dir)
    monkeypatch.setattr(flux_ops, "_require_binary", lambda _name: None)
    monkeypatch.setattr(
        flux_ops,
        "_kubectl_get_target",
        lambda target, **_kwargs: (
            _main_source_payload() if target.is_source else _main_workload_payload(),
            "",
        ),
    )
    monkeypatch.setattr(flux_ops, "_kubectl_json", lambda *_args, **_kwargs: _main_source_payload())

    with pytest.raises(SoperatorSafetyPauseError, match="frozen authority"):
        flux_ops.wait_for_rendered_flux_resources(
            SimpleNamespace(flux_dir=flux_dir),
            timeout_seconds=30,
            poll_interval_seconds=0.01,
            main_workload_identity=_frozen_main_identity(uid="previous-main-release-uid"),
        )


def _install_fake_kubectl_json(
    monkeypatch: pytest.MonkeyPatch,
    responses: Mapping[str, dict[str, Any]],
) -> None:
    def _fake(command: list[str], *, env, timeout=30):
        del env, timeout
        for resource, payload in responses.items():
            if resource in command:
                return payload
        raise AssertionError(f"unexpected kubectl command: {command}")

    monkeypatch.setattr(flux_ops, "_kubectl_json", _fake)


def test_complete_product_readiness_requires_exact_nested_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_kubectl_json(monkeypatch, _responses())

    ready, detail = flux_ops._soperator_product_readiness(_contract(), env={})

    assert ready is True
    assert detail == "complete Soperator release graph and product are Ready"


def test_stale_child_generation_blocks_product_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_kubectl_json(monkeypatch, _responses(observed_generation=0))

    ready, detail = flux_ops._soperator_product_readiness(_contract(), env={})

    assert ready is False
    assert "observed generation" in detail


@pytest.mark.parametrize(
    ("phase", "accounting_status"),
    [
        ("Not available", "True"),
        ("Available", "Unknown"),
    ],
)
def test_product_readiness_requires_native_slurm_cluster_status_contract(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    accounting_status: str,
) -> None:
    responses = _responses()
    cluster_status = responses["slurmclusters.slurm.nebius.ai"]["status"]
    cluster_status["phase"] = phase
    next(
        condition
        for condition in cluster_status["conditions"]
        if condition["type"] == "AccountingAvailable"
    )["status"] = accounting_status
    _install_fake_kubectl_json(monkeypatch, responses)

    ready, detail = flux_ops._soperator_product_readiness(_contract(), env={})

    assert ready is False
    assert detail == "SlurmCluster example is not Available"


@pytest.mark.parametrize(
    ("phase", "ready_replicas"),
    [
        ("Provisioning", 2),
        ("Ready", 1),
    ],
)
def test_product_readiness_requires_native_nodeset_status_contract(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    ready_replicas: int,
) -> None:
    contract = _contract()
    contract["readiness"] = {"nodeSets": ["worker-0"]}
    responses = _responses()
    nodeset_status = responses["nodesets.slurm.nebius.ai"]["items"][0]["status"]
    nodeset_status.update({"phase": phase, "replicas": ready_replicas})
    _install_fake_kubectl_json(monkeypatch, responses)

    ready, detail = flux_ops._soperator_product_readiness(contract, env={})

    assert ready is False
    assert detail == "required NodeSets are not Available"


def test_product_readiness_accepts_ready_native_nodeset_status_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract()
    contract["readiness"] = {"nodeSets": ["worker-0"]}
    _install_fake_kubectl_json(monkeypatch, _responses())

    ready, detail = flux_ops._soperator_product_readiness(contract, env={})

    assert ready is True
    assert detail == "complete Soperator release graph and product are Ready"


def test_product_readiness_accepts_the_retained_suspended_namespace_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract()
    contract["releases"][0]["upstreamReleaseName"] = "soperator-fluxcd-ns"
    responses = _responses()
    responses["helmreleases.helm.toolkit.fluxcd.io"]["items"][0]["spec"]["suspend"] = True
    _install_fake_kubectl_json(monkeypatch, responses)

    ready, detail = flux_ops._soperator_product_readiness(contract, env={})

    assert ready is True
    assert detail == "complete Soperator release graph and product are Ready"


@pytest.mark.parametrize(
    ("upstream_release", "suspended"),
    [
        ("soperator-fluxcd-ns", False),
        ("soperator-fluxcd-soperator", True),
    ],
)
def test_product_readiness_rejects_a_release_outside_its_declared_suspension_state(
    monkeypatch: pytest.MonkeyPatch,
    upstream_release: str,
    suspended: bool,
) -> None:
    contract = _contract()
    contract["releases"][0]["upstreamReleaseName"] = upstream_release
    responses = _responses()
    if suspended:
        responses["helmreleases.helm.toolkit.fluxcd.io"]["items"][0]["spec"]["suspend"] = True
    _install_fake_kubectl_json(monkeypatch, responses)

    ready, detail = flux_ops._soperator_product_readiness(contract, env={})

    assert ready is False
    assert "declared suspension state" in detail


def test_unexpected_soperator_release_blocks_product_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_kubectl_json(monkeypatch, _responses(unexpected=True))

    ready, detail = flux_ops._soperator_product_readiness(_contract(), env={})

    assert ready is False
    assert "unexpected Soperator HelmReleases" in detail


def test_missing_adapter_daemonsets_block_product_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = _responses()
    responses["daemonsets.apps"] = {"items": []}
    _install_fake_kubectl_json(monkeypatch, responses)

    ready, detail = flux_ops._soperator_product_readiness(_contract(), env={})

    assert ready is False
    assert "adapter DaemonSet inventory is unavailable" in detail


def test_exact_storage_identity_mismatch_blocks_product_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract()
    contract["readiness"] = {
        "storage": [
            {
                "kind": "PersistentVolume",
                "name": "state-pv",
                "namespace": "",
                "storageClassName": "slurm-local-pv",
                "reclaimPolicy": "Retain",
                "backend": {
                    "kind": "local",
                    "identity": hashlib.sha256(
                        json.dumps(
                            {"path": "/mnt/different"},
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest(),
                },
            },
            {
                "kind": "PersistentVolumeClaim",
                "name": "state",
                "namespace": "soperator",
                "storageClassName": "slurm-local-pv",
                "volumeName": "state-pv",
            },
        ]
    }
    _install_fake_kubectl_json(monkeypatch, _responses())

    ready, detail = flux_ops._soperator_product_readiness(contract, env={})

    assert ready is False
    assert "wrong backing identity" in detail


def test_readiness_receipt_records_exact_live_object_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract()
    contract["release"] = "4.1.7"
    _install_fake_kubectl_json(monkeypatch, _responses())

    receipt = flux_ops._soperator_product_readiness_receipt(contract, env={})

    assert receipt.release == "4.1.7"
    assert receipt.cluster_uid == "cluster-uid"
    assert receipt.helm_releases == (
        ("flux-system", "soperator-fluxcd-soperator", "release-uid", 1),
    )
    assert receipt.protected_storage == (
        ("PersistentVolume", "", "state-pv", "pv-uid"),
        ("PersistentVolumeClaim", "soperator", "state", "pvc-uid"),
    )


def test_wait_revalidates_product_after_capturing_readiness_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SimpleNamespace(flux_dir=tmp_path)
    observations = iter(
        [
            (True, "initially ready"),
            (False, "changed during receipt capture"),
            (True, "ready again"),
            (True, "stable after receipt capture"),
        ]
    )
    receipts = iter(["stale-receipt", "stable-receipt"])
    monkeypatch.setattr(flux_ops, "_rendered_soperator_graph_contract", lambda _path: _contract())
    monkeypatch.setattr(
        flux_ops,
        "_soperator_product_readiness",
        lambda _contract_value, *, env: next(observations),
    )
    monkeypatch.setattr(
        flux_ops,
        "_soperator_product_readiness_receipt",
        lambda _contract_value, *, env: next(receipts),
    )
    monkeypatch.setattr(flux_ops.time, "sleep", lambda _seconds: None)

    receipt = flux_ops.wait_for_soperator_release_graph(
        paths,
        timeout_seconds=30,
        poll_interval_seconds=0.1,
    )

    assert receipt == "stable-receipt"


def test_adapter_storage_preflight_applies_and_waits_for_current_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flux_dir = tmp_path / "generated" / "flux"
    flux_dir.mkdir(parents=True)
    (flux_dir / "soperator-nebius-adapter.yaml").write_text(
        """apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: nebius-cxcli-soperator-jail-mount
  namespace: soperator
  labels:
    soperator.nebius.ai/managed-by: nebius-cxcli-adapter
    soperator.nebius.ai/lifecycle: recreatable
""",
        encoding="utf-8",
    )
    paths = ProjectPaths(
        config_path=tmp_path / "config.yaml",
        repo_root=tmp_path,
        deployments_dir=tmp_path,
        project_dir=tmp_path,
        generated_dir=tmp_path / "generated",
        infra_dir=tmp_path / "generated" / "infra",
        flux_dir=flux_dir,
        reports_dir=tmp_path / "generated" / "reports",
        path_tenant_folder="tenant",
        path_project_folder="project",
    )
    calls: list[tuple[list[str], str]] = []

    def _fake_run(command: list[str], **kwargs):
        calls.append((command, str(kwargs.get("input") or "")))
        if "apply" in command:
            return SimpleNamespace(returncode=0, stdout="applied", stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '{"metadata":{"generation":3},"status":'
                '{"observedGeneration":3,"desiredNumberScheduled":2,"numberAvailable":2}}'
            ),
            stderr="",
        )

    monkeypatch.setattr(flux_ops.subprocess, "run", _fake_run)

    flux_ops.prepare_soperator_adapter_storage(paths, cache_dir=tmp_path / "cache")

    assert "kind: Namespace" in calls[0][1]
    assert "soperator.nebius.ai/lifecycle: shared" in calls[0][1]
    assert "soperator.nebius.ai/managed-by: nebius-cxcli-adapter" in calls[0][1]
    assert "kind: DaemonSet" in calls[0][1]
    assert calls[1][0][-3:] == [
        "nebius-cxcli-soperator-jail-mount",
        "-o",
        "json",
    ]


def test_adapter_storage_postcondition_can_verify_exact_preimage_daemonsets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flux_dir = tmp_path / "generated" / "flux"
    flux_dir.mkdir(parents=True)
    (flux_dir / "soperator-nebius-adapter.yaml").write_text(
        """apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: nebius-cxcli-soperator-jail-mount
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: nebius-cxcli-soperator-controller-spool-mount
""",
        encoding="utf-8",
    )
    paths = ProjectPaths(
        config_path=tmp_path / "config.yaml",
        repo_root=tmp_path,
        deployments_dir=tmp_path,
        project_dir=tmp_path,
        generated_dir=tmp_path / "generated",
        infra_dir=tmp_path / "generated" / "infra",
        flux_dir=flux_dir,
        reports_dir=tmp_path / "generated" / "reports",
        path_tenant_folder="tenant",
        path_project_folder="project",
    )
    calls: list[list[str]] = []

    def _fake_run(command: list[str], **_kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '{"metadata":{"uid":"jail-uid","generation":3},"status":'
                '{"observedGeneration":3,"desiredNumberScheduled":2,"numberAvailable":2}}'
            ),
            stderr="",
        )

    monkeypatch.setattr(flux_ops.subprocess, "run", _fake_run)

    receipt = flux_ops.verify_soperator_adapter_storage(
        paths,
        expected_daemonsets=("nebius-cxcli-soperator-jail-mount",),
    )

    assert receipt == {
        "status": "ready",
        "daemonsets": [
            {
                "name": "nebius-cxcli-soperator-jail-mount",
                "uid": "jail-uid",
                "generation": 3,
            }
        ],
    }
    assert len(calls) == 1
    assert calls[0][-3:] == [
        "nebius-cxcli-soperator-jail-mount",
        "-o",
        "json",
    ]


def test_adapter_storage_postcondition_rejects_unknown_preimage_daemonset(
    tmp_path: Path,
) -> None:
    flux_dir = tmp_path / "generated" / "flux"
    flux_dir.mkdir(parents=True)
    (flux_dir / "soperator-nebius-adapter.yaml").write_text(
        """apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: nebius-cxcli-soperator-jail-mount
""",
        encoding="utf-8",
    )
    paths = ProjectPaths(
        config_path=tmp_path / "config.yaml",
        repo_root=tmp_path,
        deployments_dir=tmp_path,
        project_dir=tmp_path,
        generated_dir=tmp_path / "generated",
        infra_dir=tmp_path / "generated" / "infra",
        flux_dir=flux_dir,
        reports_dir=tmp_path / "generated" / "reports",
        path_tenant_folder="tenant",
        path_project_folder="project",
    )

    with pytest.raises(RuntimeError, match="preimage DaemonSet contract"):
        flux_ops.verify_soperator_adapter_storage(
            paths,
            expected_daemonsets=("unknown-writer",),
        )


def _native_payloads(*, reclaim_policy: str = "Retain") -> dict[str, Any]:
    available = {
        "metadata": {"namespace": "soperator", "uid": "uid", "generation": 1},
        "status": {"observedGeneration": 1, "conditions": _condition("Available")},
    }
    return {
        "helm": {
            "items": [
                {
                    "metadata": {
                        "name": "soperator-fluxcd",
                        "namespace": "flux-system",
                        "uid": "root-uid",
                        "generation": 1,
                    },
                    "spec": {
                        "releaseName": "soperator-fluxcd",
                        "chart": {
                            "spec": {
                                "chart": "helm-soperator-fluxcd",
                                "version": "4.1.7",
                            }
                        },
                    },
                    "status": {"observedGeneration": 1, "conditions": _condition()},
                }
            ]
        },
        "cluster": {
            "items": [
                {
                    **available,
                    "metadata": {
                        "name": "soperator",
                        "namespace": "soperator",
                        "uid": "cluster-uid",
                        "generation": 1,
                        "labels": {"app.kubernetes.io/version": "4.1.7"},
                    },
                    "spec": {
                        "slurmNodes": {
                            "accounting": {"enabled": True},
                            "controller": {},
                            "login": {},
                        }
                    },
                    "status": {
                        "phase": "Available",
                        "conditions": _slurm_cluster_conditions(),
                    },
                }
            ]
        },
        "nodesets": {
            "items": [
                {
                    **available,
                    "metadata": {
                        "name": "worker",
                        "namespace": "soperator",
                        "uid": "nodeset-uid",
                        "generation": 1,
                    },
                    "spec": {"replicas": 1},
                    "status": {"phase": "Ready", "replicas": 1},
                }
            ]
        },
        "pods": {
            "items": [
                {
                    "metadata": {
                        "name": "controller-0",
                        "namespace": "soperator",
                        "uid": "pod-uid",
                    },
                    "status": {"phase": "Running", "conditions": _condition()},
                }
            ]
        },
        "activechecks": {
            "items": [
                {
                    "metadata": {
                        "name": "slurm-ready",
                        "namespace": "soperator",
                        "uid": "check-uid",
                        "generation": 1,
                    },
                    "spec": {
                        "checkType": "k8sJob",
                        "runAfterCreation": True,
                    },
                    "status": {
                        "k8sJobsStatus": {
                            "lastJobName": "slurm-ready-123",
                            "lastJobStatus": "Complete",
                        }
                    },
                }
            ]
        },
        "reclaim_policy": reclaim_policy,
    }


def _install_native_kubectl(
    monkeypatch: pytest.MonkeyPatch,
    *,
    reclaim_policy: str = "Retain",
) -> dict[str, Any]:
    payloads = _native_payloads(reclaim_policy=reclaim_policy)

    def _fake(command: list[str], *, env, timeout=30):
        del env, timeout
        joined = " ".join(command)
        if "helmreleases.helm.toolkit.fluxcd.io" in command:
            return payloads["helm"]
        if "slurmclusters.slurm.nebius.ai" in command:
            return payloads["cluster"]
        if "nodesets.slurm.nebius.ai" in command:
            return payloads["nodesets"]
        if "activechecks.slurm.nebius.ai" in command:
            return payloads["activechecks"]
        if "pods" in command:
            return payloads["pods"]
        if " get pvc " in f" {joined} ":
            pvc_name = command[command.index("pvc") + 1]
            return {
                "metadata": {"name": pvc_name, "namespace": "soperator", "uid": pvc_name},
                "spec": {"volumeName": f"pv-{pvc_name}"},
                "status": {"phase": "Bound"},
            }
        if " get pv " in f" {joined} ":
            return {
                "metadata": {"name": command[command.index("pv") + 1], "uid": "pv-uid"},
                "spec": {"persistentVolumeReclaimPolicy": payloads["reclaim_policy"]},
                "status": {"phase": "Bound"},
            }
        raise AssertionError(f"unexpected kubectl command: {command}")

    monkeypatch.setattr(flux_ops, "_kubectl_json", _fake)
    return payloads


def test_native_upstream_observation_accepts_release_without_cxcli_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_native_kubectl(monkeypatch)

    ready, detail, receipt = flux_ops._native_soperator_observation(
        expected_release="4.1.7",
        env={},
    )

    assert ready is True
    assert "upstream-native" in detail
    assert receipt is not None
    assert receipt.release == "4.1.7"
    assert receipt.cluster_uid == "cluster-uid"
    assert len(receipt.protected_storage) == 2


def test_native_observation_rejects_failed_required_active_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads = _install_native_kubectl(monkeypatch)
    payloads["activechecks"]["items"][0]["status"]["k8sJobsStatus"]["lastJobStatus"] = "Failed"

    ready, detail, receipt = flux_ops._native_soperator_observation(
        expected_release="4.1.7",
        env={},
    )

    assert ready is False
    assert detail == "native Soperator ActiveChecks are not Available"
    assert receipt is None


def test_native_observation_uses_slurm_status_for_required_slurm_active_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads = _install_native_kubectl(monkeypatch)
    check = payloads["activechecks"]["items"][0]
    check["spec"]["checkType"] = "slurmJob"
    check["status"] = {
        "k8sJobsStatus": {"lastJobStatus": "Failed"},
        "slurmJobsStatus": {"lastRunStatus": "Complete"},
    }

    ready, _, receipt = flux_ops._native_soperator_observation(
        expected_release="4.1.7",
        env={},
    )

    assert ready is True
    assert receipt is not None


def test_native_observation_ignores_non_creation_active_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads = _install_native_kubectl(monkeypatch)
    periodic_check = {
        "metadata": {"name": "periodic", "namespace": "soperator"},
        "spec": {"checkType": "k8sJob", "runAfterCreation": False},
        "status": {"k8sJobsStatus": {"lastJobStatus": "Failed"}},
    }
    payloads["activechecks"]["items"].append(periodic_check)

    ready, _, receipt = flux_ops._native_soperator_observation(
        expected_release="4.1.7",
        env={},
    )

    assert ready is True
    assert receipt is not None


def test_native_observation_requires_creation_active_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads = _install_native_kubectl(monkeypatch)
    payloads["activechecks"]["items"][0]["spec"]["runAfterCreation"] = False

    ready, detail, receipt = flux_ops._native_soperator_observation(
        expected_release="4.1.7",
        env={},
    )

    assert ready is False
    assert detail == "native Soperator ActiveChecks are not Available"
    assert receipt is None


def test_noop_readiness_uses_rendered_graph_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_receipt = object()
    paths = SimpleNamespace(flux_dir=tmp_path)
    monkeypatch.setattr(
        flux_ops,
        "_rendered_soperator_graph_contract",
        lambda _flux_dir: {
            "release": "4.1.7",
            "readiness": {"activeChecksRequired": False},
        },
    )
    monkeypatch.setattr(
        flux_ops,
        "wait_for_soperator_release_graph",
        lambda *_args, **_kwargs: graph_receipt,
    )
    monkeypatch.setattr(
        flux_ops,
        "wait_for_native_soperator_release",
        lambda **_kwargs: pytest.fail("native readiness must not own a rendered graph"),
    )

    receipt = flux_ops.wait_for_soperator_noop_readiness(
        paths,  # type: ignore[arg-type]
        expected_release="4.1.7",
        extra_env={"KUBECONFIG": "test"},
    )

    assert receipt is graph_receipt


def test_noop_readiness_rejects_rendered_graph_release_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = SimpleNamespace(flux_dir=tmp_path)
    monkeypatch.setattr(
        flux_ops,
        "_rendered_soperator_graph_contract",
        lambda _flux_dir: {"release": "4.1.6"},
    )

    with pytest.raises(ValueError, match="differs from the no-op target"):
        flux_ops.wait_for_soperator_noop_readiness(
            paths,  # type: ignore[arg-type]
            expected_release="4.1.7",
        )


def test_noop_readiness_uses_native_authority_without_rendered_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_receipt = object()
    paths = SimpleNamespace(flux_dir=tmp_path)
    monkeypatch.setattr(
        flux_ops,
        "_rendered_soperator_graph_contract",
        lambda _flux_dir: None,
    )
    monkeypatch.setattr(
        flux_ops,
        "wait_for_soperator_release_graph",
        lambda *_args, **_kwargs: pytest.fail(
            "graph readiness must not own a direct-upstream install"
        ),
    )
    monkeypatch.setattr(
        flux_ops,
        "wait_for_native_soperator_release",
        lambda **_kwargs: native_receipt,
    )

    receipt = flux_ops.wait_for_soperator_noop_readiness(
        paths,  # type: ignore[arg-type]
        expected_release="4.1.7",
    )

    assert receipt is native_receipt


def test_native_upstream_observation_rejects_non_retained_protected_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_native_kubectl(monkeypatch, reclaim_policy="Delete")

    ready, detail, receipt = flux_ops._native_soperator_observation(
        expected_release="4.1.7",
        env={},
    )

    assert ready is False
    assert "Bound with Retain" in detail
    assert receipt is None
