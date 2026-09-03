from __future__ import annotations

import copy
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console
from rich.markdown import Markdown

from nebius_cxcli import soperator_discovery, soperator_registration
from nebius_cxcli.soperator_discovery import (
    build_soperator_public_discovery_report,
    write_soperator_discovery_bundle,
    write_soperator_public_discovery_report,
)

_SENSITIVE_SENTINEL = "SENSITIVE_COLLECTION_OUTPUT"


def _public_detection_lanes(*, detected: bool = True) -> list[dict[str, object]]:
    return [
        {"name": "kubernetes-crds", "status": "succeeded", "item_count": int(detected)},
        {
            "name": "soperator-resources",
            "status": "succeeded" if detected else "not-applicable",
            "item_count": int(detected),
        },
        {"name": "soperator-helm", "status": "succeeded", "item_count": int(detected)},
    ]


def test_public_snapshot_records_ambiguous_identity_while_default_stays_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _kubectl_json(args, *_args, **_kwargs):
        command = tuple(args)
        if command[-4:] == ("get", "crd", "-o", "json"):
            return {
                "items": [
                    {"metadata": {"name": "slurmclusters.slurm.nebius.ai"}},
                ]
            }
        if command[-4:] == ("get", "namespace", "-o", "json"):
            return {
                "items": [
                    {"metadata": {"name": "kube-system", "uid": "cluster-uid-a"}},
                    {"metadata": {"name": "soperator", "uid": "soperator-uid-a"}},
                ]
            }
        return {"items": []}

    def _kubectl_list(args, *_args, **_kwargs):
        command = tuple(args)
        if "slurmclusters" in command:
            return {
                "items": [
                    {
                        "kind": "SlurmCluster",
                        "metadata": {
                            "name": "cluster-a",
                            "namespace": "soperator",
                            "uid": "slurm-uid-a",
                        },
                    },
                    {
                        "kind": "SlurmCluster",
                        "metadata": {
                            "name": "cluster-b",
                            "namespace": "soperator",
                            "uid": "slurm-uid-b",
                        },
                    },
                ]
            }
        return {"items": []}

    monkeypatch.setattr(soperator_registration, "_kubectl_json", _kubectl_json)
    monkeypatch.setattr(
        soperator_registration,
        "_kubectl_list_json_with_bounded_retry",
        _kubectl_list,
    )
    monkeypatch.setattr(soperator_registration, "_helm_json", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        soperator_registration,
        "_collect_worker_topology_by_nodeset",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        soperator_registration,
        "_collect_slurm_health_from_login",
        lambda **_kwargs: {"checked": False, "healthy": False},
    )

    snapshot = soperator_registration.collect_kubectl_soperator_snapshot(
        kube_context="ctx-a",
        require_complete_identity=False,
    )

    assert snapshot["cluster_identity"]["slurmcluster_uid"] == ""
    assert snapshot["cluster_identity"]["jail_filesystem_id"] == ""
    assert any(
        error.get("collector") == "soperator-identity" for error in snapshot["collection_errors"]
    )
    with pytest.raises(RuntimeError, match="expected exactly one discovered SlurmCluster"):
        soperator_registration.collect_kubectl_soperator_snapshot(kube_context="ctx-a")


def test_slurm_health_collection_failure_is_sanitized_and_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        soperator_registration.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            soperator_registration.subprocess.TimeoutExpired("kubectl", 30)
        ),
    )
    errors: list[dict[str, object]] = []
    result = soperator_registration._collect_slurm_health_from_login(
        kube_context="ctx-a",
        workloads={
            "items": [
                {
                    "kind": "Pod",
                    "metadata": {
                        "name": "login-0",
                        "labels": {"app.kubernetes.io/component": "login"},
                    },
                    "status": {"phase": "Running"},
                }
            ]
        },
        timeout=30,
        extra_env=None,
        errors=errors,
    )

    assert result == {
        "checked": False,
        "healthy": False,
        "pod": "login-0",
        "reason": "scontrol ping collector timed out",
    }
    assert errors == [
        {
            "collector": "slurm-health",
            "message": "scontrol ping collector timed out",
        }
    ]


@pytest.mark.parametrize(
    ("error", "safe_message"),
    (
        (
            subprocess.CalledProcessError(
                7,
                ("kubectl", "get", "pods"),
                output=f"stdout {_SENSITIVE_SENTINEL}",
                stderr=f"stderr {_SENSITIVE_SENTINEL}",
            ),
            "collector command failed with exit code 7",
        ),
        (
            subprocess.TimeoutExpired(
                ("kubectl", "get", "pods"),
                30,
                output=f"stdout {_SENSITIVE_SENTINEL}",
                stderr=f"stderr {_SENSITIVE_SENTINEL}",
            ),
            "collector command timed out",
        ),
        (
            json.JSONDecodeError(
                f"invalid {_SENSITIVE_SENTINEL}",
                _SENSITIVE_SENTINEL,
                0,
            ),
            "collector command returned invalid JSON",
        ),
        (
            OSError(f"execution failed {_SENSITIVE_SENTINEL}"),
            "collector command could not be executed",
        ),
    ),
)
def test_discovery_bundle_does_not_persist_collector_subprocess_output(
    tmp_path: Path,
    error: BaseException,
    safe_message: str,
) -> None:
    error = soperator_registration._subprocess_error_payload(
        ("kubectl", "get", "pods"),
        error,
    )

    manifest = write_soperator_discovery_bundle(
        tmp_path,
        target_ref="cluster-a",
        snapshot={},
        report={},
        source_kind="onboarded",
        slurm_snapshot={"collection_errors": [error]},
    )

    serialized_bundle = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(manifest.parent.iterdir())
        if path.is_file()
    )
    assert _SENSITIVE_SENTINEL not in serialized_bundle
    assert safe_message in serialized_bundle


def test_public_discovery_writes_support_safe_version_evidence(tmp_path: Path) -> None:
    snapshot = {
        "helm_releases": [
            {
                "name": "soperator",
                "namespace": "soperator",
                "chart": "helm-soperator-1.22.3",
                "app_version": "1.22.3",
                "status": "deployed",
            }
        ],
        "soperator_resources": [
            {
                "kind": "SlurmCluster",
                "metadata": {"name": "soperator", "namespace": "soperator"},
                "spec": {
                    "populateJail": {"image": "registry.example.invalid/populate-jail:cuda13.0"}
                },
            }
        ],
        "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
        "kubernetes_nodes": [
            {
                "metadata": {
                    "name": "worker-a",
                    "labels": {
                        "nebius.com/node-group-id": "mk8snodegroup-a",
                        "nebius.com/node-group": "worker",
                        "topology.kubernetes.io/region": "eu-north1",
                        "nvidia.com/gpu.product": "H100",
                        "unrelated.example.invalid/customer": "must-not-be-copied",
                    },
                },
                "spec": {"unschedulable": False},
                "status": {
                    "conditions": [
                        {
                            "type": "Ready",
                            "status": "True",
                            "reason": "KubeletReady",
                            "message": "must-not-be-copied",
                        }
                    ],
                    "nodeInfo": {
                        "kubeletVersion": "v1.33.7",
                        "osImage": "Ubuntu 24.04",
                    },
                },
            }
        ],
        "gpu_stack": {
            "helm_releases": [
                {
                    "name": "gpu-operator",
                    "namespace": "nvidia-gpu-operator",
                    "chart": "gpu-operator-v25.3.2",
                }
            ],
            "policies": [
                {
                    "kind": "ClusterPolicy",
                    "metadata": {"name": "cluster-policy"},
                    "spec": {"private": "must-not-be-copied"},
                }
            ],
        },
        "slurm_health": {"checked": True, "healthy": True},
        "collection_errors": [],
        "collection_lanes": _public_detection_lanes(),
    }
    provider = {
        "control_plane_version": "1.33",
        "node_groups": [
            {
                "id": "mk8snodegroup-a",
                "name": "worker",
                "kubernetes_version": "1.33",
                "os": "ubuntu24.04-cuda",
                "drivers_preset": "cuda13.0",
                "gpu": True,
                "actual_node_count": 1,
                "target_node_count": 1,
            }
        ],
    }

    report = build_soperator_public_discovery_report(
        tenant_id="tenant-a",
        project_id="project-a",
        cluster_id="mk8scluster-a",
        cluster_name="training-a",
        region_id="eu-north1",
        access="external",
        snapshot=snapshot,
        provider=provider,
    )
    json_path, markdown_path, rendered_markdown = write_soperator_public_discovery_report(
        tmp_path,
        report=report,
    )

    assert report["status"] == "complete"
    assert report["schema"] == "nebius-cxcli.soperator-public-discovery.v2"
    assert json_path == (tmp_path / "soperator-discovery" / "mk8scluster-a" / "report.json")
    assert markdown_path == json_path.with_name("report.md")
    components = {item["component"]: item for item in report["summary"]["components"]}
    assert components["Kubernetes control plane"]["classification"] == "provider-configured"
    assert components["Soperator chart"]["classification"] == "kubernetes-observed"
    assert components["GPU driver runtime"]["classification"] == "unknown"
    assert components["CUDA runtime"]["classification"] == "unknown"
    assert report["summary"]["node_groups"][0]["counts"] == {
        "ready": 1,
        "observed": 1,
        "actual": 1,
        "target": 1,
        "readiness_unknown": 0,
        "unschedulable": 0,
    }
    assert len(report["inventory"]["kubernetes"]["nodes"]) == 1
    serialized = json_path.read_text(encoding="utf-8")
    assert "must-not-be-copied" not in serialized
    markdown = markdown_path.read_text(encoding="utf-8")
    assert rendered_markdown == markdown
    assert "| Component | Version / state | Evidence |" in markdown
    assert "| Group | Ready / Actual / Target |" in markdown
    assert "| worker | 1 / 1 / 1 |" in markdown
    assert "worker-a" not in markdown
    assert "Runtime GPU driver and CUDA versions remain unknown" in markdown
    assert "Report directory: `soperator-discovery/mk8scluster-a`" in markdown
    assert str(tmp_path) not in markdown


def test_public_discovery_not_detected_still_has_report_payload() -> None:
    report = build_soperator_public_discovery_report(
        tenant_id="tenant-a",
        project_id="project-a",
        cluster_id="mk8scluster-a",
        cluster_name="training-a",
        region_id="eu-north1",
        access="external",
        snapshot={
            "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
            "collection_errors": [],
            "collection_lanes": _public_detection_lanes(detected=False),
        },
        provider={"control_plane_version": "1.33", "node_groups": []},
    )

    assert report["status"] == "not-detected"
    components = {item["component"]: item for item in report["summary"]["components"]}
    assert components["Soperator chart"]["state"] == "not-detected"


def test_public_discovery_markdown_neutralizes_terminal_and_structure_injection(
    tmp_path: Path,
) -> None:
    report = build_soperator_public_discovery_report(
        tenant_id="tenant-a",
        project_id="project-a",
        cluster_id="mk8scluster-a",
        cluster_name="training`\x1b[2J\n# forged heading",
        region_id="eu-north1",
        access="external",
        snapshot={
            "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
            "collection_errors": [],
            "collection_lanes": _public_detection_lanes(detected=False),
        },
        provider={"control_plane_version": "1.33", "node_groups": []},
    )
    report["summary"]["components"] = [
        {
            "component": "soperator|forged\n| row",
            "value": "`spoofed`",
            "state": "unsafe\r\n## forged detail",
            "classification": "unknown",
        }
    ]
    report["summary"]["node_groups"] = [
        {
            "name": "group\x1b]52;c;Y2xpcGJvYXJk\x07",
            "counts": {"ready": 0, "actual": None, "target": None},
            "kubernetes": {"configured": None, "observed": []},
            "os": {"configured": None, "observed": []},
            "gpu": {"preset": None, "products": []},
        }
    ]
    report["collection"]["incomplete_reasons"] = ["probe\u2028## forged reason\u202e"]

    _, markdown_path, markdown = write_soperator_public_discovery_report(
        tmp_path,
        report=report,
    )

    assert markdown_path.read_bytes().decode("utf-8") == markdown
    assert "\x1b" not in markdown
    assert "\x07" not in markdown
    assert "\r" not in markdown
    assert "\u2028" not in markdown
    assert "\u202e" not in markdown
    assert "\n# forged heading" not in markdown
    assert "\n| row" not in markdown
    assert "\n## forged detail" not in markdown
    assert "\n## forged reason" not in markdown
    assert r"\u001b" in markdown
    assert r"\u0007" in markdown
    assert r"\u2028" in markdown
    assert r"\u202e" in markdown
    assert "| Component | Version / state | Evidence |" in markdown
    assert "unsafe\\\\u000d\\\\u000a\\#\\# forged detail" in markdown
    evidence_row = next(line for line in markdown.splitlines() if line.startswith("| soperator"))
    assert evidence_row.count(" | ") == 2


def test_public_discovery_keeps_4000_nodes_in_json_and_bounds_markdown(
    tmp_path: Path,
) -> None:
    nodes = []
    for index in range(4000):
        group_index = index % 5
        nodes.append(
            {
                "metadata": {
                    "name": f"computeinstance-{index:04d}",
                    "uid": f"node-uid-{index:04d}",
                    "labels": {
                        "nebius.com/node-group-id": f"group-{group_index}",
                        "nebius.com/node-group": f"worker-{group_index}",
                        "nvidia.com/gpu.product": "H100",
                        "unrelated.example.invalid/sentinel": _SENSITIVE_SENTINEL,
                    },
                },
                "status": {
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "nodeInfo": {
                        "kubeletVersion": "v1.35.6",
                        "osImage": "Ubuntu 24.04.4 LTS",
                    },
                },
            }
        )
    provider_groups = [
        {
            "id": f"group-{index}",
            "name": f"worker-{index}",
            "kubernetes_version": "1.35",
            "os": "ubuntu24.04",
            "drivers_preset": "cuda13.0",
            "gpu": True,
            "actual_node_count": 800,
            "target_node_count": 800,
        }
        for index in range(5)
    ]
    report = build_soperator_public_discovery_report(
        tenant_id="tenant-a",
        project_id="project-a",
        cluster_id="mk8scluster-large",
        cluster_name="large-cluster",
        region_id="eu-north1",
        access="external",
        snapshot={
            "helm_releases": [
                {
                    "name": "soperator-controller",
                    "namespace": "soperator-system",
                    "chart": "helm-soperator-4.1.7",
                    "app_version": "4.1.7",
                }
            ],
            "soperator_resources": [
                {
                    "apiVersion": "slurm.nebius.ai/v1",
                    "kind": "SlurmCluster",
                    "metadata": {
                        "name": "large-cluster",
                        "namespace": "soperator",
                        "uid": "slurm-uid",
                    },
                    "spec": {"private": _SENSITIVE_SENTINEL},
                }
            ],
            "soperator_namespace_resources": [
                {
                    "kind": "Deployment",
                    "metadata": {
                        "name": "sconfigcontroller",
                        "namespace": "soperator",
                        "labels": {"private": _SENSITIVE_SENTINEL},
                    },
                    "status": {
                        "readyReplicas": 1,
                        "message": _SENSITIVE_SENTINEL,
                    },
                },
                {
                    "kind": "Secret",
                    "metadata": {"name": "credentials", "namespace": "soperator"},
                    "type": "Opaque",
                    "data_keys": ["username", "password"],
                    "data": {"password": _SENSITIVE_SENTINEL},
                },
            ],
            "pvs": [
                {
                    "kind": "PersistentVolume",
                    "metadata": {"name": "jail-pv", "uid": "pv-uid"},
                    "spec": {
                        "storageClassName": "network-ssd",
                        "csi": {"volumeHandle": _SENSITIVE_SENTINEL},
                    },
                    "status": {"phase": "Bound"},
                }
            ],
            "cluster_identity": {"kubernetes_uid": "cluster-uid-large"},
            "kubernetes_nodes": nodes,
            "gpu_stack": {
                "helm_releases": [
                    {
                        "name": "gpu-operator",
                        "namespace": "nvidia-gpu-operator",
                        "chart": "gpu-operator-v25.10.0",
                    }
                ]
            },
            "slurm_health": {
                "checked": True,
                "healthy": True,
                "output": _SENSITIVE_SENTINEL,
            },
            "collection_errors": [],
            "collection_lanes": _public_detection_lanes(),
        },
        provider={"control_plane_version": "1.35", "node_groups": provider_groups},
    )

    json_path, _, markdown = write_soperator_public_discovery_report(tmp_path, report=report)

    assert report["status"] == "complete"
    assert len(report["inventory"]["kubernetes"]["nodes"]) == 4000
    assert sum(group["counts"]["observed"] for group in report["summary"]["node_groups"]) == 4000
    assert len(markdown.splitlines()) < 50
    assert len(markdown.encode("utf-8")) < 16 * 1024
    assert "computeinstance-0000" not in markdown
    assert "computeinstance-3999" not in markdown
    assert _SENSITIVE_SENTINEL not in json_path.read_text(encoding="utf-8")


def test_public_discovery_keeps_only_storage_referenced_by_soperator_components() -> None:
    report = build_soperator_public_discovery_report(
        tenant_id="tenant-a",
        project_id="project-a",
        cluster_id="mk8scluster-a",
        cluster_name="cluster-a",
        region_id="eu-north1",
        access="external",
        snapshot={
            "helm_releases": [{"name": "soperator", "chart": "helm-soperator-4.1.7"}],
            "soperator_resources": [
                {
                    "kind": "SlurmCluster",
                    "metadata": {"name": "cluster-a", "namespace": "soperator"},
                    "spec": {"jail": {"persistentVolumeClaim": {"claimName": "jail-rootfs"}}},
                }
            ],
            "pvcs": [
                {
                    "kind": "PersistentVolumeClaim",
                    "metadata": {
                        "name": "jail-rootfs",
                        "namespace": "soperator",
                        "uid": "pvc-in-scope",
                    },
                    "spec": {"volumeName": "pv-in-scope", "storageClassName": "network-ssd"},
                },
                {
                    "kind": "PersistentVolumeClaim",
                    "metadata": {
                        "name": "customer-data",
                        "namespace": "unrelated-customer-app",
                        "uid": _SENSITIVE_SENTINEL,
                    },
                    "spec": {"volumeName": "pv-unrelated"},
                },
            ],
            "pvs": [
                {
                    "kind": "PersistentVolume",
                    "metadata": {"name": "pv-in-scope", "uid": "pv-in-scope-uid"},
                    "spec": {"claimRef": {"name": "jail-rootfs", "namespace": "soperator"}},
                },
                {
                    "kind": "PersistentVolume",
                    "metadata": {"name": "pv-unrelated", "uid": _SENSITIVE_SENTINEL},
                    "spec": {
                        "claimRef": {
                            "name": "customer-data",
                            "namespace": "unrelated-customer-app",
                        }
                    },
                },
            ],
            "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
            "slurm_health": {"checked": True, "healthy": True},
            "collection_errors": [],
            "collection_lanes": _public_detection_lanes(),
        },
        provider={"control_plane_version": "1.35", "node_groups": []},
    )

    storage = report["inventory"]["storage"]
    assert [item["name"] for item in storage["persistent_volume_claims"]] == ["jail-rootfs"]
    assert [item["name"] for item in storage["persistent_volumes"]] == ["pv-in-scope"]
    assert _SENSITIVE_SENTINEL not in json.dumps(report)


def test_public_discovery_rejects_ambiguous_or_contradictory_node_correlation() -> None:
    report = build_soperator_public_discovery_report(
        tenant_id="tenant-a",
        project_id="project-a",
        cluster_id="mk8scluster-a",
        cluster_name="cluster-a",
        region_id="eu-north1",
        access="external",
        snapshot={
            "helm_releases": [{"name": "soperator", "chart": "helm-soperator-4.1.7"}],
            "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
            "kubernetes_nodes": [
                {
                    "metadata": {
                        "name": "unknown-id",
                        "labels": {
                            "nebius.com/node-group-id": "missing",
                            "nebius.com/node-group": "worker-a",
                        },
                    }
                },
                {
                    "metadata": {
                        "name": "contradiction",
                        "labels": {
                            "nebius.com/node-group-id": "group-a",
                            "nebius.com/node-group": "worker-b",
                        },
                    }
                },
                {
                    "metadata": {
                        "name": "ambiguous-name",
                        "labels": {"nebius.com/node-group": "duplicate"},
                    }
                },
            ],
            "slurm_health": {"checked": True, "healthy": True},
            "collection_errors": [],
            "collection_lanes": _public_detection_lanes(),
        },
        provider={
            "control_plane_version": "1.35",
            "node_groups": [
                {"id": "group-a", "name": "worker-a"},
                {"id": "group-b", "name": "worker-b"},
                {"id": "group-c", "name": "duplicate"},
                {"id": "group-d", "name": "duplicate"},
            ],
        },
    )

    assert report["status"] == "partial"
    assert report["summary"]["cluster"]["unmatched_node_count"] == 3
    assert report["collection"]["correlation_reasons"] == [
        "ambiguous-provider-group-name",
        "contradictory-provider-group-labels",
        "unknown-provider-group-id",
    ]
    unmatched = report["summary"]["node_groups"][-1]
    assert unmatched["name"] == "unmatched"
    assert unmatched["counts"]["observed"] == 3


def test_public_discovery_rejects_conflicting_node_labels_and_duplicate_provider_ids() -> None:
    report = build_soperator_public_discovery_report(
        tenant_id="tenant-a",
        project_id="project-a",
        cluster_id="mk8scluster-a",
        cluster_name="cluster-a",
        region_id="eu-north1",
        access="external",
        snapshot={
            "helm_releases": [{"name": "soperator", "chart": "helm-soperator-4.1.7"}],
            "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
            "kubernetes_nodes": [
                {
                    "metadata": {
                        "name": "conflicting-labels",
                        "labels": {
                            "nebius.com/node-group-id": "group-a",
                            "yandex.cloud/node-group-id": "group-b",
                        },
                    }
                },
                {
                    "metadata": {
                        "name": "duplicate-provider-id",
                        "labels": {"nebius.com/node-group-id": "group-duplicate"},
                    }
                },
            ],
            "slurm_health": {"checked": True, "healthy": True},
            "collection_errors": [],
            "collection_lanes": _public_detection_lanes(),
        },
        provider={
            "control_plane_version": "1.35",
            "node_groups": [
                {"id": "group-a", "name": "worker-a"},
                {"id": "group-b", "name": "worker-b"},
                {"id": "group-duplicate", "name": "worker-c"},
                {"id": "group-duplicate", "name": "worker-d"},
            ],
        },
    )

    assert report["status"] == "partial"
    assert report["summary"]["cluster"]["unmatched_node_count"] == 2
    assert report["collection"]["correlation_reasons"] == [
        "conflicting-provider-group-id-labels",
        "duplicate-provider-group-id",
    ]


def test_public_discovery_failed_detection_lane_is_partial_not_absent() -> None:
    base_snapshot = {
        "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
        "collection_errors": [],
        "collection_lanes": [
            {"name": "kubernetes-crds", "status": "succeeded", "item_count": 0},
            {"name": "soperator-resources", "status": "not-applicable", "item_count": 0},
            {"name": "soperator-helm", "status": "succeeded", "item_count": 0},
        ],
    }
    complete_absence = build_soperator_public_discovery_report(
        tenant_id="tenant-a",
        project_id="project-a",
        cluster_id="mk8scluster-a",
        cluster_name="cluster-a",
        region_id="eu-north1",
        access="external",
        snapshot=base_snapshot,
        provider={"control_plane_version": "1.35", "node_groups": []},
    )
    failed_snapshot = copy.deepcopy(base_snapshot)
    failed_snapshot["collection_lanes"][2]["status"] = "failed"
    failed_snapshot["collection_lanes"][2]["item_count"] = None
    failed_snapshot["collection_errors"] = [
        {"collector": "helm", "message": f"collector command failed: {_SENSITIVE_SENTINEL}"}
    ]
    failed = build_soperator_public_discovery_report(
        tenant_id="tenant-a",
        project_id="project-a",
        cluster_id="mk8scluster-a",
        cluster_name="cluster-a",
        region_id="eu-north1",
        access="external",
        snapshot=failed_snapshot,
        provider={"control_plane_version": "1.35", "node_groups": []},
    )
    missing_lanes = build_soperator_public_discovery_report(
        tenant_id="tenant-a",
        project_id="project-a",
        cluster_id="mk8scluster-a",
        cluster_name="cluster-a",
        region_id="eu-north1",
        access="external",
        snapshot={"cluster_identity": {"kubernetes_uid": "cluster-uid-a"}},
        provider={"control_plane_version": "1.35", "node_groups": []},
    )

    assert complete_absence["status"] == "not-detected"
    assert failed["status"] == "partial"
    assert missing_lanes["status"] == "partial"
    missing_components = {
        item["component"]: item for item in missing_lanes["summary"]["components"]
    }
    assert missing_components["Soperator chart"]["state"] == "unknown"
    assert _SENSITIVE_SENTINEL not in json.dumps(failed)


def test_public_discovery_bounds_mixed_values_and_keeps_zero_node_groups(
    tmp_path: Path,
) -> None:
    nodes = [
        {
            "metadata": {
                "name": f"node-{index}",
                "labels": {"nebius.com/node-group-id": "group-a"},
            },
            "status": {
                "conditions": [{"type": "Ready", "status": "True"}],
                "nodeInfo": {"kubeletVersion": f"v1.35.{index}"},
            },
        }
        for index in range(4)
    ]
    report = build_soperator_public_discovery_report(
        tenant_id="tenant-a",
        project_id="project-a",
        cluster_id="mk8scluster-a",
        cluster_name="cluster-a",
        region_id="eu-north1",
        access="external",
        snapshot={
            "helm_releases": [{"name": "soperator", "chart": "helm-soperator-4.1.7"}],
            "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
            "kubernetes_nodes": nodes,
            "slurm_health": {"checked": True, "healthy": True},
            "collection_errors": [],
            "collection_lanes": _public_detection_lanes(),
        },
        provider={
            "control_plane_version": "1.35",
            "node_groups": [
                {
                    "id": "group-a",
                    "name": "worker",
                    "actual_node_count": 4,
                    "target_node_count": 4,
                },
                {
                    "id": "group-zero",
                    "name": "burst",
                    "actual_node_count": 0,
                    "target_node_count": 0,
                },
            ],
        },
    )
    _, _, markdown = write_soperator_public_discovery_report(tmp_path, report=report)

    assert report["status"] == "complete"
    groups = {group["name"]: group for group in report["summary"]["node_groups"]}
    assert len(groups["worker"]["kubernetes"]["observed"]) == 4
    assert groups["burst"]["counts"] == {
        "ready": 0,
        "observed": 0,
        "actual": 0,
        "target": 0,
        "readiness_unknown": 0,
        "unschedulable": 0,
    }
    assert r"v1.35.0, v1.35.1, v1.35.2 \+1" in markdown


def test_public_discovery_marks_multiple_component_candidates_ambiguous() -> None:
    report = build_soperator_public_discovery_report(
        tenant_id="tenant-a",
        project_id="project-a",
        cluster_id="mk8scluster-a",
        cluster_name="cluster-a",
        region_id="eu-north1",
        access="external",
        snapshot={
            "helm_releases": [
                {"name": "soperator", "namespace": "a", "chart": "helm-soperator-4.1.7"},
                {"name": "soperator", "namespace": "b", "chart": "helm-soperator-4.1.7"},
            ],
            "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
            "slurm_health": {"checked": True, "healthy": True},
            "collection_errors": [],
            "collection_lanes": _public_detection_lanes(),
        },
        provider={"control_plane_version": "1.35", "node_groups": []},
    )

    components = {item["component"]: item for item in report["summary"]["components"]}
    assert report["status"] == "partial"
    assert components["Soperator chart"]["state"] == "ambiguous"
    assert len(report["inventory"]["components"]["soperator_helm_releases"]) == 2


def test_public_discovery_classifies_gpu_and_network_operator_releases_separately() -> None:
    report = build_soperator_public_discovery_report(
        tenant_id="tenant-a",
        project_id="project-a",
        cluster_id="mk8scluster-a",
        cluster_name="cluster-a",
        region_id="eu-north1",
        access="external",
        snapshot={
            "helm_releases": [{"name": "soperator", "chart": "helm-soperator-4.1.7"}],
            "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
            "gpu_stack": {
                "helm_releases": [
                    {
                        "name": "network-operator",
                        "namespace": "nvidia-network-operator",
                        "chart": "network-operator-v25.7.0",
                    },
                    {
                        "name": "gpu-operator",
                        "namespace": "nvidia-gpu-operator",
                        "chart": "gpu-operator-v25.10.0",
                    },
                ]
            },
            "slurm_health": {"checked": True, "healthy": True},
            "collection_errors": [],
            "collection_lanes": _public_detection_lanes(),
        },
        provider={
            "control_plane_version": "1.35",
            "node_groups": [{"id": "gpu", "name": "gpu", "gpu": True}],
        },
    )

    components = {item["component"]: item for item in report["summary"]["components"]}
    assert report["status"] == "complete"
    assert components["GPU Operator"]["state"] == "observed"
    assert components["GPU Operator"]["value"] == "gpu-operator-v25.10.0"
    assert components["Network Operator"]["state"] == "observed"
    assert components["Network Operator"]["value"] == "network-operator-v25.7.0"
    assert len(report["inventory"]["components"]["gpu_helm_releases"]) == 2


def test_public_discovery_markdown_remains_bounded_in_narrow_tty_and_logs(
    tmp_path: Path,
) -> None:
    report = build_soperator_public_discovery_report(
        tenant_id="tenant-a",
        project_id="project-a",
        cluster_id="mk8scluster-a",
        cluster_name="cluster-a",
        region_id="eu-north1",
        access="external",
        snapshot={
            "helm_releases": [{"name": "soperator", "chart": "helm-soperator-4.1.7"}],
            "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
            "slurm_health": {"checked": True, "healthy": True},
            "collection_errors": [],
            "collection_lanes": _public_detection_lanes(),
        },
        provider={"control_plane_version": "1.35", "node_groups": []},
    )
    _, _, markdown = write_soperator_public_discovery_report(tmp_path, report=report)

    for force_terminal in (False, True):
        output = StringIO()
        console = Console(
            file=output,
            width=60,
            force_terminal=force_terminal,
            color_system="standard" if force_terminal else None,
        )
        console.print(Markdown(markdown, hyperlinks=False))
        rendered = output.getvalue()
        assert "Soperator Discovery Report" in rendered
        assert "Node groups" in rendered
        assert len(rendered.splitlines()) < 180
        if not force_terminal:
            assert "\x1b" not in rendered


def test_public_discovery_is_deterministic_and_publishes_matching_pairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(soperator_discovery, "_now_z", lambda: "2026-09-03T00:00:00Z")
    provider_groups = [
        {"id": "b", "name": "worker-b", "actual_node_count": 1, "target_node_count": 1},
        {"id": "a", "name": "worker-a", "actual_node_count": 1, "target_node_count": 1},
    ]
    nodes = [
        {
            "metadata": {
                "name": f"node-{group_id}",
                "uid": f"uid-{group_id}",
                "labels": {"nebius.com/node-group-id": group_id},
            },
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
        for group_id in ("b", "a")
    ]
    tied_releases = [
        {
            "name": "network-operator",
            "namespace": "nvidia-network-operator",
            "chart": "network-operator-v25.7.0",
            "app_version": "25.7.0",
            "status": "deployed",
        },
        {
            "name": "network-operator",
            "namespace": "nvidia-network-operator",
            "chart": "network-operator-v25.7.0",
            "app_version": "25.7.1",
            "status": "pending-upgrade",
        },
    ]

    def build(name: str, *, reverse: bool) -> dict[str, object]:
        return build_soperator_public_discovery_report(
            tenant_id="tenant-a",
            project_id="project-a",
            cluster_id="mk8scluster-a",
            cluster_name=name,
            region_id="eu-north1",
            access="external",
            snapshot={
                "helm_releases": [{"name": "soperator", "chart": "helm-soperator-4.1.7"}],
                "cluster_identity": {"kubernetes_uid": "cluster-uid-a"},
                "kubernetes_nodes": list(reversed(nodes)) if reverse else nodes,
                "gpu_stack": {
                    "helm_releases": list(reversed(tied_releases)) if reverse else tied_releases
                },
                "slurm_health": {"checked": True, "healthy": True},
                "collection_errors": [],
                "collection_lanes": _public_detection_lanes(),
            },
            provider={
                "control_plane_version": "1.35",
                "node_groups": list(reversed(provider_groups)) if reverse else provider_groups,
            },
        )

    stable_a = build("cluster", reverse=False)
    stable_b = build("cluster", reverse=True)
    assert stable_a == stable_b

    reports = [build("writer-a", reverse=False), build("writer-b", reverse=True)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(
            executor.map(
                lambda report: write_soperator_public_discovery_report(tmp_path, report=report),
                reports,
            )
        )
    report_dir = tmp_path / "soperator-discovery" / "mk8scluster-a"
    persisted = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    markdown = (report_dir / "report.md").read_text(encoding="utf-8")
    assert f"Report ID: `{persisted['report_id']}`" in markdown

    committed_json = (report_dir / "report.json").read_text(encoding="utf-8")
    committed_markdown = (report_dir / "report.md").read_text(encoding="utf-8")
    real_replace = soperator_discovery.os.replace

    def fail_json_commit(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == report_dir / "report.json" and str(source).endswith(".tmp"):
            raise OSError("injected JSON commit failure")
        real_replace(source, destination)

    monkeypatch.setattr(soperator_discovery.os, "replace", fail_json_commit)
    with pytest.raises(OSError, match="injected JSON commit failure"):
        write_soperator_public_discovery_report(
            tmp_path,
            report=build("writer-failure", reverse=False),
        )

    assert (report_dir / "report.json").read_text(encoding="utf-8") == committed_json
    assert (report_dir / "report.md").read_text(encoding="utf-8") == committed_markdown
    assert not (report_dir / ".report.json.previous").exists()
    assert not (report_dir / ".report.md.previous").exists()
