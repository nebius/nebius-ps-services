import copy
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import soperator_install_storage_cause as cause


def record():
    return {
        "msg": "High ephemeral storage usage detected",
        "logger": "soperatorchecks.pod-ephemeral-storage-check",
        "controller": "soperatorchecks.pod-ephemeral-storage-check",
        "Pod": {"name": "worker-0", "namespace": "soperator"},
        "pod": "worker-0",
        "namespace": "soperator",
        "usagePercent": "138.45%",
        "reconcileID": "reconcile-id",
    }


@pytest.mark.parametrize(
    "mutation",
    [None, "memory", "within-limit", "namespace", "pod", "controller", "late", "old", "truncated"],
)
def test_storage_cause_requires_native_over_limit_report_before_eviction(mutation):
    row, stamp = record(), "2026-01-01T00:00:59Z"
    if mutation == "memory":
        row["msg"] = "High memory usage detected"
    elif mutation == "within-limit":
        row["usagePercent"] = "99.99%"
    elif mutation == "namespace":
        row["Pod"]["namespace"] = "other"
    elif mutation == "pod":
        row["pod"] = "worker-1"
    elif mutation == "controller":
        row["controller"] = "other"
    elif mutation == "late":
        stamp = "2026-01-01T00:01:01Z"
    elif mutation == "old":
        stamp = "2026-01-01T00:00:40Z"
    text = stamp + " " + json.dumps(row)
    if mutation == "truncated":
        text += " " * (1024 * 1024)
    args = dict(
        worker="worker-0", started=datetime(2026, 1, 1), terminated=datetime(2026, 1, 1, 0, 1)
    )
    if mutation:
        with pytest.raises(RuntimeError):
            cause.storage_cause_from_logs(text, **args)
    else:
        assert cause.storage_cause_from_logs(text, **args)["usagePercent"] == "138.45%"


@pytest.mark.parametrize("mutation", [None, "image", "owner", "restart", "replaced"])
def test_storage_cause_binds_native_controller_lineage_and_unchanged_container(
    tmp_path, monkeypatch, mutation
):
    for name, values in {
        "soperator-fluxcd": {
            "soperator": {"namespace": "operators", "soperatorChecks": {"releaseName": "checks"}}
        },
        "soperatorchecks": {
            "checks": {"manager": {"image": {"repository": "example/checks", "tag": "4.1.5"}}}
        },
    }.items():
        path = tmp_path / "helm" / name / "values.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(yaml.safe_dump(values))
    deployment = {
        "metadata": {
            "uid": "deploy",
            "annotations": {
                "meta.helm.sh/release-name": "checks",
                "meta.helm.sh/release-namespace": "operators",
            },
        },
        "spec": {
            "selector": {"matchLabels": {"app": "checks"}},
            "template": {
                "spec": {"containers": [{"name": "manager", "image": "example/checks:4.1.5"}]}
            },
        },
    }
    replica = {
        "metadata": {
            "uid": "replica",
            "ownerReferences": [{"controller": True, "kind": "Deployment", "uid": "deploy"}],
        }
    }
    pod = {
        "spec": {"containers": [{"name": "manager", "image": "example/checks:4.1.5"}]},
        "metadata": {
            "uid": "pod",
            "name": "checks-pod",
            "ownerReferences": [
                {"controller": True, "kind": "ReplicaSet", "name": "checks-rs", "uid": "replica"}
            ],
        },
        "status": {
            "containerStatuses": [
                {
                    "name": "manager",
                    "restartCount": 0,
                    "imageID": "image-digest",
                    "state": {"running": {"startedAt": "2025-12-31T00:00:00Z"}},
                }
            ]
        },
    }
    if mutation == "image":
        deployment["spec"]["template"]["spec"]["containers"][0]["image"] = "other"
    elif mutation == "owner":
        replica["metadata"]["ownerReferences"][0]["uid"] = "other"
    elif mutation == "restart":
        pod["status"]["containerStatuses"][0]["restartCount"] = 1
    current = copy.deepcopy(pod)
    if mutation == "replaced":
        current["metadata"]["uid"] = "new-pod"
    runner = SimpleNamespace(
        _get=lambda kind, name="", namespace="": {
            "deployments": {"items": [deployment]},
            "replicaset": replica,
            "pod": current,
        }[kind],
        kube=lambda args, doc: {"items": [pod]},
    )

    def logs(command, **kwargs):
        assert command[5:9] == ["logs", "checks-pod", "-c", "manager"]
        return SimpleNamespace(stdout="2026-01-01T00:00:59Z " + json.dumps(record()))

    monkeypatch.setattr(cause.subprocess, "run", logs)
    failure = {
        "eviction": {"time": "2026-01-01T00:01:00"},
        "executionInterval": {"start": "2026-01-01T00:00:00"},
        "worker": {"name": "worker-0"},
    }
    args = dict(source_dir=tmp_path, values={}, env={}, kube_context="context")
    if mutation:
        with pytest.raises(RuntimeError):
            cause.capture_storage_cause(runner, failure, **args)
    else:
        result = cause.capture_storage_cause(runner, failure, **args)
        assert result["podUid"] == "pod" and result["imageId"] == "image-digest"
