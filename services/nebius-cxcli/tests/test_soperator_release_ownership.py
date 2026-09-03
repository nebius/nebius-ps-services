from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from nebius_cxcli.soperator_release_ownership import (
    SOPERATOR_RELEASE_OWNERSHIP_SCHEMA,
    SourceReleaseOwner,
    SourceReleaseOwnership,
    capture_source_release_ownership,
    quiesce_source_release_ownership,
    restore_source_release_ownership,
    retire_source_release_ownership,
    source_parent_writer_from_payload,
    verify_source_release_quiesced,
    verify_source_release_retired,
    verify_target_single_writer,
)


@dataclass
class _Result:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


def _metadata(name: str, namespace: str, uid: str, labels: dict[str, str] | None = None):
    return {
        "name": name,
        "namespace": namespace,
        "uid": uid,
        "resourceVersion": "10",
        "labels": labels or {},
    }


def _not_found_status() -> str:
    return json.dumps(
        {
            "apiVersion": "v1",
            "kind": "Status",
            "status": "Failure",
            "reason": "NotFound",
            "code": 404,
        }
    )


def _graph_contract() -> dict[str, object]:
    return {
        "schema": "nebius-cxcli.soperator-release-graph.v2",
        "releases": [
            {
                "releaseName": "cxcli-soperator-fluxcd-slurm-cluster",
                "namespace": "flux-system",
                "sourceKind": "OCIRepository",
                "sourceName": "soperator-upstream-umbrella",
                "revision": "sha256:" + "1" * 64,
                "isMain": True,
            }
        ],
    }


def _protected_data_plane_inventory() -> dict[str, object]:
    umbrella_name = "soperator-fluxcd"
    helm_release_name = "flux-system-soperator-fluxcd"

    def child(name: str, chart: str, version: str) -> dict[str, object]:
        return {
            "metadata": {
                **_metadata(
                    f"{helm_release_name}-{name}",
                    "flux-system",
                    f"source-{name}",
                    {
                        "app.kubernetes.io/instance": helm_release_name,
                        "app.kubernetes.io/managed-by": "Helm",
                        "app.kubernetes.io/name": "helm-soperator-fluxcd",
                        "app.kubernetes.io/version": "1.22.3",
                        "helm.sh/chart": "helm-soperator-fluxcd-1.22.3",
                        "helm.toolkit.fluxcd.io/name": umbrella_name,
                        "helm.toolkit.fluxcd.io/namespace": "flux-system",
                    },
                ),
                "annotations": {
                    "meta.helm.sh/release-name": helm_release_name,
                    "meta.helm.sh/release-namespace": "flux-system",
                },
            },
            "spec": {
                "chart": {
                    "spec": {
                        "chart": chart,
                        "version": version,
                        "sourceRef": {
                            "kind": "HelmRepository",
                            "name": f"{helm_release_name}-soperator",
                        },
                    }
                }
            },
        }

    return {
        "items": [
            child("soperator", "helm-soperator", "1.22.3"),
            child("slurm-cluster", "helm-slurm-cluster", "1.22.3"),
            child(
                "slurm-cluster-storage",
                "helm-slurm-cluster-storage",
                "1.22.3",
            ),
            child("cert-manager", "cert-manager", "v1.17.*"),
            {
                "metadata": _metadata(
                    umbrella_name,
                    "flux-system",
                    "source-umbrella",
                ),
                "spec": {
                    "targetNamespace": "flux-system",
                    "chart": {
                        "spec": {
                            "chart": "helm-soperator-fluxcd",
                            "version": "1.22.3",
                            "sourceRef": {
                                "kind": "HelmRepository",
                                "name": umbrella_name,
                            },
                        }
                    },
                },
            },
            {
                "metadata": _metadata("unrelated", "other", "unrelated"),
                "spec": {},
            },
        ]
    }


def test_capture_source_ownership_uses_only_exact_frozen_graph_identities() -> None:
    helmrelease = {
        "metadata": _metadata(
            "cxcli-soperator-fluxcd-slurm-cluster",
            "flux-system",
            "source-hr",
            {
                "app.kubernetes.io/version": "4.1.6",
                "soperator.nebius.ai/release-graph": "nebius-cxcli",
            },
        ),
        "spec": {
            "chartRef": {
                "kind": "OCIRepository",
                "name": "soperator-upstream-umbrella",
                "namespace": "flux-system",
            }
        },
    }
    deployment = {
        "metadata": _metadata(
            "soperator-controller-manager",
            "soperator-system",
            "source-controller",
            {"app.kubernetes.io/version": "4.1.6"},
        ),
        "spec": {"replicas": 1},
    }
    commands: list[tuple[str, ...]] = []

    def runner(args, **_kwargs):
        commands.append(tuple(args))
        return _Result(stdout=json.dumps(helmrelease if "helmrelease" in args else deployment))

    receipt = capture_source_release_ownership(
        runner,
        kube_context="ctx",
        graph_contract=_graph_contract(),
        source_release="4.1.6",
    )

    assert {(owner.kind, owner.uid) for owner in receipt.owners} == {
        ("HelmRelease", "source-hr"),
        ("Deployment", "source-controller"),
    }
    assert not any("-A" in command for command in commands)


def test_single_writer_requires_exact_release_and_one_ready_controller() -> None:
    payload: dict[str, Any] = {
        "metadata": _metadata(
            "soperator-controller-manager",
            "soperator-system",
            "target-controller",
            {"app.kubernetes.io/version": "4.1.7"},
        ),
        "spec": {"replicas": 1},
        "status": {"readyReplicas": 1},
    }

    result = verify_target_single_writer(
        lambda _args, **_kwargs: _Result(stdout=json.dumps(payload)),
        kube_context="ctx",
        target_release="4.1.7",
    )

    assert result["status"] == "exclusive"
    assert result["uid"] == "target-controller"

    payload["metadata"]["labels"]["app.kubernetes.io/version"] = "4.1.6"
    with pytest.raises(RuntimeError, match="exact target release"):
        verify_target_single_writer(
            lambda _args, **_kwargs: _Result(stdout=json.dumps(payload)),
            kube_context="ctx",
            target_release="4.1.7",
        )


def test_capture_source_ownership_filters_target_only_release_rows() -> None:
    graph = _graph_contract()
    releases = graph["releases"]
    assert isinstance(releases, list)
    releases.append(
        {
            "releaseName": "cxcli-soperator-fluxcd-cert-manager",
            "namespace": "flux-system",
            "sourceKind": "HelmChart",
            "sourceName": "soperator-third-party-cert-manager",
            "revision": "sha256:" + "2" * 64,
            "isMain": False,
        }
    )
    helmrelease = {
        "metadata": _metadata(
            "cxcli-soperator-fluxcd-slurm-cluster",
            "flux-system",
            "source-hr",
            {
                "app.kubernetes.io/version": "4.1.6",
                "soperator.nebius.ai/release-graph": "nebius-cxcli",
            },
        ),
        "spec": {
            "chartRef": {
                "kind": "OCIRepository",
                "name": "soperator-upstream-umbrella",
                "namespace": "flux-system",
            }
        },
    }
    deployment = {
        "metadata": _metadata(
            "soperator-controller-manager",
            "soperator-system",
            "source-controller",
            {"app.kubernetes.io/version": "4.1.6"},
        ),
        "spec": {"replicas": 1},
    }
    commands: list[tuple[str, ...]] = []

    def runner(args, **_kwargs):
        commands.append(tuple(args))
        return _Result(stdout=json.dumps(helmrelease if "helmrelease" in args else deployment))

    capture_source_release_ownership(
        runner,
        kube_context="ctx",
        graph_contract=graph,
        source_release="4.1.6",
        source_release_identities=(("flux-system", "cxcli-soperator-fluxcd-slurm-cluster"),),
    )

    assert not any("cxcli-soperator-fluxcd-cert-manager" in command for command in commands)


def test_capture_protected_data_plane_ownership_uses_exact_umbrella_children() -> None:
    inventory = _protected_data_plane_inventory()
    deployment = {
        "metadata": _metadata(
            "soperator-controller-manager",
            "soperator-system",
            "source-controller",
            {"app.kubernetes.io/version": "1.22.3"},
        ),
        "spec": {"replicas": 1},
    }
    commands: list[tuple[str, ...]] = []

    def runner(args, **_kwargs):
        commands.append(tuple(args))
        return _Result(stdout=json.dumps(deployment if "deployment" in args else inventory))

    receipt = capture_source_release_ownership(
        runner,
        kube_context="ctx",
        graph_contract=_graph_contract(),
        source_release="1.22.3",
        source_contract="protected-data-plane-v1",
    )

    assert {(owner.kind, owner.name) for owner in receipt.owners} == {
        ("Deployment", "soperator-controller-manager"),
        ("HelmRelease", "soperator-fluxcd"),
        ("HelmRelease", "flux-system-soperator-fluxcd-cert-manager"),
        ("HelmRelease", "flux-system-soperator-fluxcd-slurm-cluster"),
        ("HelmRelease", "flux-system-soperator-fluxcd-slurm-cluster-storage"),
        ("HelmRelease", "flux-system-soperator-fluxcd-soperator"),
    }
    assert sum("helmreleases.helm.toolkit.fluxcd.io" in command for command in commands) == 1
    assert all(owner.name != "unrelated" for owner in receipt.owners)


def test_capture_protected_data_plane_ownership_rejects_incomplete_core_graph() -> None:
    inventory = _protected_data_plane_inventory()
    items = inventory["items"]
    assert isinstance(items, list)
    inventory["items"] = [
        item
        for item in items
        if not str(item.get("metadata", {}).get("name", "")).endswith("-slurm-cluster-storage")
    ]

    with pytest.raises(RuntimeError, match="unique operator, storage, or SlurmCluster"):
        capture_source_release_ownership(
            lambda _args, **_kwargs: _Result(stdout=json.dumps(inventory)),
            kube_context="ctx",
            graph_contract=_graph_contract(),
            source_release="1.22.3",
            source_contract="protected-data-plane-v1",
        )


def test_quiesce_is_idempotent_after_the_source_is_already_stopped() -> None:
    ownership = SourceReleaseOwnership(
        schema=SOPERATOR_RELEASE_OWNERSHIP_SCHEMA,
        owners=(
            SourceReleaseOwner(
                kind="HelmRelease",
                namespace="flux-system",
                name="soperator-fluxcd",
                uid="source-hr",
                resource_version="1",
                original_suspend=False,
            ),
            SourceReleaseOwner(
                kind="Deployment",
                namespace="soperator-system",
                name="soperator-controller-manager",
                uid="source-controller",
                resource_version="1",
                original_replicas=1,
            ),
        ),
    )
    commands: list[tuple[str, ...]] = []

    def runner(args, **_kwargs):
        commands.append(tuple(args))
        if "helmrelease" in args:
            return _Result(
                stdout=json.dumps(
                    {
                        "metadata": _metadata("soperator-fluxcd", "flux-system", "source-hr"),
                        "spec": {"suspend": True},
                    }
                )
            )
        return _Result(
            stdout=json.dumps(
                {
                    "metadata": _metadata(
                        "soperator-controller-manager",
                        "soperator-system",
                        "source-controller",
                    ),
                    "spec": {"replicas": 0},
                    "status": {"readyReplicas": 0},
                }
            )
        )

    result = quiesce_source_release_ownership(
        runner,
        kube_context="ctx",
        ownership=ownership,
        assert_authority=lambda: None,
    )

    assert result["status"] == "quiesced"
    assert not any("patch" in command for command in commands)


def test_quiesce_stops_flux_parent_before_legacy_release_children() -> None:
    ownership = SourceReleaseOwnership(
        schema=SOPERATOR_RELEASE_OWNERSHIP_SCHEMA,
        owners=(
            SourceReleaseOwner(
                kind="HelmRelease",
                namespace="flux-system",
                name="soperator-fluxcd",
                uid="source-hr",
                resource_version="1",
                original_suspend=False,
            ),
            SourceReleaseOwner(
                kind="Deployment",
                namespace="soperator-system",
                name="soperator-controller-manager",
                uid="source-controller",
                resource_version="1",
                original_replicas=1,
            ),
        ),
    )
    helmrelease = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "metadata": _metadata(
            "soperator-fluxcd",
            "flux-system",
            "source-hr",
            {
                "kustomize.toolkit.fluxcd.io/name": "flux-system",
                "kustomize.toolkit.fluxcd.io/namespace": "flux-system",
            },
        ),
        "spec": {},
    }
    parent = {
        "metadata": _metadata("flux-system", "flux-system", "source-parent"),
        "spec": {
            "interval": "10m",
            "path": "./clusters/test",
            "prune": True,
            "sourceRef": {"kind": "GitRepository", "name": "flux-system"},
        },
        "status": {
            "inventory": {
                "entries": [
                    {
                        "id": ("flux-system_soperator-fluxcd_helm.toolkit.fluxcd.io_HelmRelease"),
                        "v": "v2",
                    }
                ]
            },
            "conditions": [],
        },
    }
    deployment = {
        "metadata": _metadata(
            "soperator-controller-manager",
            "soperator-system",
            "source-controller",
        ),
        "spec": {"replicas": 1},
        "status": {"readyReplicas": 1},
    }
    patches: list[str] = []
    patch_commands: list[tuple[str, ...]] = []

    def runner(args, **_kwargs):
        joined = " ".join(args)
        if "get helmrelease soperator-fluxcd" in joined:
            return _Result(stdout=json.dumps(helmrelease))
        if "get kustomization.kustomize.toolkit.fluxcd.io flux-system" in joined:
            return _Result(stdout=json.dumps(parent))
        if "patch kustomization.kustomize.toolkit.fluxcd.io flux-system" in joined:
            patches.append("parent")
            patch_commands.append(tuple(args))
            parent["spec"]["suspend"] = True
            return _Result(stdout="{}")
        if "patch helmrelease soperator-fluxcd" in joined:
            patches.append("helmrelease")
            patch_commands.append(tuple(args))
            helmrelease["spec"]["suspend"] = True
            return _Result(stdout="{}")
        if "get deployment soperator-controller-manager" in joined:
            return _Result(stdout=json.dumps(deployment))
        if "patch deployment soperator-controller-manager" in joined:
            patches.append("deployment")
            patch_commands.append(tuple(args))
            deployment["spec"]["replicas"] = 0
            deployment["status"]["readyReplicas"] = 0
            return _Result(stdout="{}")
        if "rollout status deployment/soperator-controller-manager" in joined:
            return _Result(stdout="deployment successfully rolled out")
        raise AssertionError(joined)

    result = quiesce_source_release_ownership(
        runner,
        kube_context="ctx",
        ownership=ownership,
        assert_authority=lambda: None,
    )

    assert patches == ["parent", "helmrelease", "deployment"]
    expected = {
        "flux-system": ("source-parent", {"suspend": True}),
        "soperator-fluxcd": ("source-hr", {"suspend": True}),
        "soperator-controller-manager": ("source-controller", {"replicas": 0}),
    }
    for command in patch_commands:
        assert "--type=merge" in command
        assert "--type=json" not in command
        name = command[command.index("patch") + 2]
        payload = json.loads(command[command.index("-p") + 1])
        expected_uid, expected_spec = expected[name]
        assert payload == {
            "metadata": {"uid": expected_uid, "resourceVersion": "10"},
            "spec": expected_spec,
        }
    assert result["parentWriterCount"] == 1
    parent_payload = result["parentWriter"]
    assert isinstance(parent_payload, dict)
    assert source_parent_writer_from_payload(parent_payload).uid == "source-parent"


def test_completed_quiescence_rejects_resumed_source_helmrelease() -> None:
    ownership = SourceReleaseOwnership(
        schema=SOPERATOR_RELEASE_OWNERSHIP_SCHEMA,
        owners=(
            SourceReleaseOwner(
                kind="HelmRelease",
                namespace="flux-system",
                name="soperator-fluxcd",
                uid="source-hr",
                resource_version="1",
                original_suspend=False,
            ),
            SourceReleaseOwner(
                kind="Deployment",
                namespace="soperator-system",
                name="soperator-controller-manager",
                uid="source-controller",
                resource_version="1",
                original_replicas=1,
            ),
        ),
    )

    def runner(args, **_kwargs):
        if "helmrelease" in args:
            return _Result(
                stdout=json.dumps(
                    {
                        "metadata": _metadata("soperator-fluxcd", "flux-system", "source-hr"),
                        "spec": {"suspend": False},
                    }
                )
            )
        return _Result(
            stdout=json.dumps(
                {
                    "metadata": _metadata(
                        "soperator-controller-manager",
                        "soperator-system",
                        "source-controller",
                    ),
                    "spec": {"replicas": 0},
                    "status": {"readyReplicas": 0},
                }
            )
        )

    with pytest.raises(RuntimeError, match="not suspended"):
        verify_source_release_quiesced(runner, kube_context="ctx", ownership=ownership)


def test_retired_release_accepts_absent_helmrelease_and_quiesced_controller() -> None:
    ownership = SourceReleaseOwnership(
        schema=SOPERATOR_RELEASE_OWNERSHIP_SCHEMA,
        owners=(
            SourceReleaseOwner(
                kind="HelmRelease",
                namespace="flux-system",
                name="soperator-fluxcd",
                uid="source-hr",
                resource_version="1",
                original_suspend=False,
            ),
            SourceReleaseOwner(
                kind="Deployment",
                namespace="soperator-system",
                name="soperator-controller-manager",
                uid="source-controller",
                resource_version="1",
                original_replicas=1,
            ),
        ),
    )

    def runner(args, **_kwargs):
        if "helmrelease" in args:
            return _Result(returncode=1, stderr=_not_found_status())
        return _Result(
            stdout=json.dumps(
                {
                    "metadata": _metadata(
                        "soperator-controller-manager",
                        "soperator-system",
                        "source-controller",
                    ),
                    "spec": {"replicas": 0},
                    "status": {"readyReplicas": 0},
                }
            )
        )

    result = verify_source_release_retired(runner, kube_context="ctx", ownership=ownership)

    assert result["status"] == "retired"


def test_completed_quiescence_accepts_kubectl_ignored_retired_source_helmrelease() -> None:
    ownership = SourceReleaseOwnership(
        schema=SOPERATOR_RELEASE_OWNERSHIP_SCHEMA,
        owners=(
            SourceReleaseOwner(
                kind="HelmRelease",
                namespace="flux-system",
                name="soperator-fluxcd",
                uid="source-hr",
                resource_version="1",
                original_suspend=False,
            ),
            SourceReleaseOwner(
                kind="Deployment",
                namespace="soperator-system",
                name="soperator-controller-manager",
                uid="source-controller",
                resource_version="1",
                original_replicas=1,
            ),
        ),
    )

    def runner(args, **_kwargs):
        if "helmrelease" in args:
            assert "--ignore-not-found=true" in args
            return _Result()
        return _Result(
            stdout=json.dumps(
                {
                    "metadata": _metadata(
                        "soperator-controller-manager",
                        "soperator-system",
                        "source-controller",
                    ),
                    "spec": {"replicas": 0},
                    "status": {"readyReplicas": 0},
                }
            )
        )

    result = verify_source_release_quiesced(runner, kube_context="ctx", ownership=ownership)

    assert result["status"] == "quiesced"


def test_completed_quiescence_accepts_controller_adopted_by_exact_target_release() -> None:
    ownership = SourceReleaseOwnership(
        schema=SOPERATOR_RELEASE_OWNERSHIP_SCHEMA,
        owners=(
            SourceReleaseOwner(
                kind="Deployment",
                namespace="soperator-system",
                name="soperator-controller-manager",
                uid="source-controller",
                resource_version="1",
                original_replicas=1,
            ),
        ),
    )
    deployment = {
        "metadata": _metadata(
            "soperator-controller-manager",
            "soperator-system",
            "source-controller",
            {
                "app.kubernetes.io/managed-by": "Helm",
                "app.kubernetes.io/version": "4.1.7",
                "helm.sh/chart": "helm-soperator-4.1.7_dfddf7cde96b",
                "helm.toolkit.fluxcd.io/name": "cxcli-soperator",
                "helm.toolkit.fluxcd.io/namespace": "flux-system",
            },
        ),
        "spec": {"replicas": 1},
        "status": {"readyReplicas": 0},
    }
    deployment["metadata"]["annotations"] = {
        "meta.helm.sh/release-name": "soperator-controller",
        "meta.helm.sh/release-namespace": "soperator-system",
    }
    target = {
        "metadata": _metadata(
            "cxcli-soperator",
            "flux-system",
            "target-hr",
            {
                "app.kubernetes.io/version": "4.1.7",
                "soperator.nebius.ai/release-graph": "nebius-cxcli",
            },
        ),
        "spec": {
            "chartRef": {
                "kind": "OCIRepository",
                "name": "soperator-upstream-operator",
                "namespace": "flux-system",
            },
            "targetNamespace": "soperator-system",
            "releaseName": "soperator-controller",
        },
    }
    target["metadata"]["annotations"] = {
        "meta.helm.sh/release-name": "soperator-controller",
        "meta.helm.sh/release-namespace": "soperator-system",
    }

    def runner(args, **_kwargs):
        if "deployment" in args:
            return _Result(stdout=json.dumps(deployment))
        if "helmrelease" in args:
            return _Result(stdout=json.dumps(target))
        raise AssertionError(args)

    result = verify_source_release_quiesced(
        runner,
        kube_context="ctx",
        ownership=ownership,
        adopted_target_release="4.1.7",
    )

    assert result["status"] == "quiesced"


def test_completed_quiescence_rejects_unproven_controller_adoption() -> None:
    ownership = SourceReleaseOwnership(
        schema=SOPERATOR_RELEASE_OWNERSHIP_SCHEMA,
        owners=(
            SourceReleaseOwner(
                kind="Deployment",
                namespace="soperator-system",
                name="soperator-controller-manager",
                uid="source-controller",
                resource_version="1",
                original_replicas=1,
            ),
        ),
    )
    deployment = {
        "metadata": _metadata(
            "soperator-controller-manager",
            "soperator-system",
            "source-controller",
            {
                "app.kubernetes.io/managed-by": "Helm",
                "app.kubernetes.io/version": "4.1.7",
                "helm.sh/chart": "helm-soperator-4.1.7_dfddf7cde96b",
                "helm.toolkit.fluxcd.io/name": "cxcli-soperator",
                "helm.toolkit.fluxcd.io/namespace": "flux-system",
            },
        ),
        "spec": {"replicas": 1},
        "status": {"readyReplicas": 1},
    }
    deployment["metadata"]["annotations"] = {
        "meta.helm.sh/release-name": "soperator-controller",
        "meta.helm.sh/release-namespace": "soperator-system",
    }
    unbound_target = {
        "metadata": _metadata(
            "cxcli-soperator",
            "flux-system",
            "target-hr",
            {"app.kubernetes.io/version": "4.1.7"},
        ),
        "spec": {
            "chartRef": {
                "kind": "OCIRepository",
                "name": "soperator-upstream-operator",
                "namespace": "flux-system",
            },
            "targetNamespace": "soperator-system",
            "releaseName": "soperator-controller",
        },
    }
    unbound_target["metadata"]["annotations"] = deployment["metadata"]["annotations"]

    def runner(args, **_kwargs):
        if "deployment" in args:
            return _Result(stdout=json.dumps(deployment))
        if "helmrelease" in args:
            return _Result(stdout=json.dumps(unbound_target))
        raise AssertionError(args)

    with pytest.raises(RuntimeError, match="controller writer is not quiesced"):
        verify_source_release_quiesced(
            runner,
            kube_context="ctx",
            ownership=ownership,
            adopted_target_release="4.1.7",
        )


def test_retirement_does_not_treat_incidental_not_found_text_as_absence() -> None:
    ownership = SourceReleaseOwnership(
        schema=SOPERATOR_RELEASE_OWNERSHIP_SCHEMA,
        owners=(
            SourceReleaseOwner(
                kind="HelmRelease",
                namespace="flux-system",
                name="soperator-fluxcd",
                uid="source-hr",
                resource_version="1",
                original_suspend=False,
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="ownership command failed"):
        verify_source_release_retired(
            lambda _args, **_kwargs: _Result(
                returncode=1,
                stderr="exec: authentication helper not found",
            ),
            kube_context="ctx",
            ownership=ownership,
        )


def test_retired_release_accepts_target_adoption_of_source_helmrelease() -> None:
    ownership = SourceReleaseOwnership(
        schema=SOPERATOR_RELEASE_OWNERSHIP_SCHEMA,
        owners=(
            SourceReleaseOwner(
                kind="HelmRelease",
                namespace="flux-system",
                name="soperator-fluxcd",
                uid="source-hr",
                resource_version="1",
                original_suspend=False,
            ),
        ),
    )

    def runner(args, **_kwargs):
        assert "helmrelease" in args
        return _Result(
            stdout=json.dumps(
                {
                    "metadata": _metadata(
                        "soperator-fluxcd",
                        "flux-system",
                        "source-hr",
                        {"soperator.nebius.ai/release-graph": "nebius-cxcli"},
                    ),
                    "spec": {"suspend": False},
                }
            )
        )

    result = verify_source_release_retired(runner, kube_context="ctx", ownership=ownership)

    assert result["status"] == "retired"


def test_rollback_rejects_visible_target_owner() -> None:
    ownership = SourceReleaseOwnership(
        schema=SOPERATOR_RELEASE_OWNERSHIP_SCHEMA,
        owners=(
            SourceReleaseOwner(
                kind="HelmRelease",
                namespace="flux-system",
                name="soperator-fluxcd-soperator",
                uid="source-hr",
                resource_version="1",
                original_suspend=False,
            ),
            SourceReleaseOwner(
                kind="Deployment",
                namespace="soperator-system",
                name="soperator-controller-manager",
                uid="controller-uid",
                resource_version="1",
                original_replicas=1,
            ),
        ),
    )

    def runner(args, **kwargs):
        joined = " ".join(args)
        if "get helmreleases.helm.toolkit.fluxcd.io -A" in joined:
            return _Result(
                stdout=json.dumps(
                    {
                        "items": [
                            {
                                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                                "metadata": _metadata(
                                    "cxcli-soperator-fluxcd-soperator",
                                    "flux-system",
                                    "target-hr",
                                    {"soperator.nebius.ai/release-graph": "nebius-cxcli"},
                                ),
                                "spec": {"suspend": True},
                            }
                        ]
                    }
                )
            )
        raise AssertionError(joined)

    with pytest.raises(RuntimeError, match="resume forward"):
        restore_source_release_ownership(
            runner,
            kube_context="ctx",
            ownership=ownership,
            source_release="1.22.0",
            assert_authority=lambda: None,
        )


def test_rollback_restores_source_flux_when_target_owner_is_absent() -> None:
    ownership = SourceReleaseOwnership(
        schema=SOPERATOR_RELEASE_OWNERSHIP_SCHEMA,
        owners=(
            SourceReleaseOwner(
                kind="HelmRelease",
                namespace="flux-system",
                name="soperator-fluxcd-soperator",
                uid="source-hr",
                resource_version="1",
                original_suspend=False,
            ),
            SourceReleaseOwner(
                kind="Deployment",
                namespace="soperator-system",
                name="soperator-controller-manager",
                uid="controller-uid",
                resource_version="1",
                original_replicas=1,
            ),
        ),
    )
    commands: list[tuple[str, ...]] = []

    def runner(args, **_kwargs):
        commands.append(tuple(args))
        joined = " ".join(args)
        if "get helmreleases.helm.toolkit.fluxcd.io -A" in joined:
            return _Result(stdout=json.dumps({"items": []}))
        if "get helmrelease soperator-fluxcd-soperator" in joined:
            return _Result(
                stdout=json.dumps(
                    {
                        "metadata": _metadata(
                            "soperator-fluxcd-soperator", "flux-system", "source-hr"
                        ),
                        "spec": {"suspend": True},
                    }
                )
            )
        if "patch helmrelease" in joined:
            return _Result(stdout="{}")
        if "get deployment soperator-controller-manager" in joined:
            return _Result(
                stdout=json.dumps(
                    {
                        "metadata": _metadata(
                            "soperator-controller-manager",
                            "soperator-system",
                            "controller-uid",
                            {"app.kubernetes.io/version": "1.22.0"},
                        ),
                        "spec": {"replicas": 1},
                        "status": {"readyReplicas": 1},
                    }
                )
            )
        raise AssertionError(joined)

    result = restore_source_release_ownership(
        runner,
        kube_context="ctx",
        ownership=ownership,
        source_release="1.22.0",
        assert_authority=lambda: None,
    )

    assert result["status"] == "restored"
    patch_command = next(command for command in commands if "patch" in command)
    assert "--type=merge" in patch_command
    assert "--type=json" not in patch_command
    assert json.loads(patch_command[patch_command.index("-p") + 1]) == {
        "metadata": {"uid": "source-hr", "resourceVersion": "10"},
        "spec": {"suspend": False},
    }
    assert not any("delete" in command for command in commands)


def test_retirement_removes_finalizer_with_uid_resource_version_cas() -> None:
    ownership = SourceReleaseOwnership(
        schema=SOPERATOR_RELEASE_OWNERSHIP_SCHEMA,
        owners=(
            SourceReleaseOwner(
                kind="HelmRelease",
                namespace="flux-system",
                name="soperator-fluxcd",
                uid="source-hr",
                resource_version="1",
                original_suspend=False,
            ),
        ),
    )
    resource = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "metadata": {
            **_metadata("soperator-fluxcd", "flux-system", "source-hr"),
            "finalizers": ["finalizers.fluxcd.io"],
        },
        "spec": {"suspend": True},
    }
    commands: list[tuple[str, ...]] = []
    inputs: list[str | None] = []

    def runner(args, **kwargs):
        commands.append(tuple(args))
        inputs.append(kwargs.get("input_text"))
        if "get" in args:
            return _Result(stdout=json.dumps(resource))
        return _Result(stdout="{}")

    result = retire_source_release_ownership(
        runner,
        kube_context="ctx",
        ownership=ownership,
        assert_authority=lambda: None,
    )

    patch_command = next(command for command in commands if "patch" in command)
    assert "--type=merge" in patch_command
    assert "--type=json" not in patch_command
    assert json.loads(patch_command[patch_command.index("-p") + 1]) == {
        "metadata": {
            "uid": "source-hr",
            "resourceVersion": "10",
            "finalizers": [],
        }
    }
    delete_index = next(index for index, command in enumerate(commands) if "delete" in command)
    assert json.loads(inputs[delete_index] or "{}") == {
        "apiVersion": "meta.k8s.io/v1",
        "kind": "DeleteOptions",
        "propagationPolicy": "Orphan",
        "preconditions": {"uid": "source-hr"},
    }
    assert result["retiredCount"] == 1
