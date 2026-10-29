"""Identity-bound, resumable collector changes under the application lease.

The small cluster ConfigMap is a portable progress receipt, never a source of
desired configuration. Every retry rechecks live workloads. Staging uses private
copies of the frozen bundle; retained stores and PVCs are never removed here.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from .grafana_database import app_owner
from .observability_collectors import COMPONENT_ANNOTATION, SIGNALS_ANNOTATION, inactive_release
from .observability_e2e import kubectl

RECEIPT = "cxcli-observability-handover"
NAMESPACE = "observability"
OWNER = "cxcli.nebius.com/app-owner"


def _get(kind: str, namespace: str, name: str, env) -> dict:
    return kubectl(["-n", namespace, "get", kind, name, "--ignore-not-found", "-o", "json"], env)


def _clean(document: dict) -> dict:
    return {
        "apiVersion": document["apiVersion"],
        "kind": document["kind"],
        "metadata": {
            key: value
            for key, value in document["metadata"].items()
            if key in {"name", "namespace", "labels", "annotations"}
        },
        "spec": copy.deepcopy(document["spec"]),
    }


def _same(live: dict, desired: dict) -> bool:
    return (
        bool(live)
        and all(
            live.get("spec", {}).get(key) == desired["spec"].get(key)
            for key in ("values", "valuesFrom", "postRenderers")
        )
        and (
            live.get("metadata", {}).get("annotations", {}).get(SIGNALS_ANNOTATION)
            == desired["metadata"]["annotations"][SIGNALS_ANNOTATION]
        )
    )


def _current_ready(live: dict) -> bool:
    generation = live.get("metadata", {}).get("generation")
    return bool(generation) and any(
        condition.get("type") == "Ready"
        and condition.get("status") == "True"
        and condition.get("observedGeneration") == generation
        for condition in live.get("status", {}).get("conditions", [])
    )


def _vmagent_selectors(collectors: list[dict], env, previous: dict) -> dict[str, list[str]]:
    # Freeze CR identities before a disabled chart removes them. Keep them in
    # the receipt so a retry still sees terminating Pods after CR deletion.
    result = json.loads(previous.get("data", {}).get("vmAgentSelectors", "{}"))
    metrics = [
        row
        for row in collectors
        if row["metadata"]["annotations"][COMPONENT_ANNOTATION] == "victoria-metrics-k8s-stack"
    ]
    if not metrics or not kubectl(
        ["get", "crd", "vmagents.operator.victoriametrics.com", "--ignore-not-found", "-o", "json"],
        env,
    ):
        return result
    for row in metrics:
        meta = row["metadata"]
        key = meta["namespace"] + "/" + meta["name"]
        selectors = set(result.get(key, []))
        agents = kubectl(
            [
                "-n",
                meta["namespace"],
                "get",
                "vmagents",
                "-l",
                "app.kubernetes.io/instance=" + meta["name"],
                "-o",
                "json",
            ],
            env,
        )
        for agent in agents.get("items", []):
            metadata = agent["metadata"]
            release = metadata.get("annotations", {}).get("meta.helm.sh/release-name", meta["name"])
            if release == meta["name"]:
                selectors.add(
                    "app.kubernetes.io/name=vmagent,app.kubernetes.io/instance="
                    + metadata["name"]
                    + ",managed-by=vm-operator"
                )
        result[key] = sorted(selectors)
    return result


def _wait_stopped(
    collectors: list[dict], env, assert_authority: Callable, vmagent_selectors: dict
) -> None:
    deadline = time.monotonic() + 180
    while True:
        assert_authority()
        waiting = []
        for release in collectors:
            meta = release["metadata"]
            live = _get("helmrelease", meta["namespace"], meta["name"], env)
            if not _current_ready(live):
                waiting.append(meta["name"])
                continue
            component = meta["annotations"][COMPONENT_ANNOTATION]
            selectors = (
                vmagent_selectors.get(meta["namespace"] + "/" + meta["name"], [])
                if component == "victoria-metrics-k8s-stack"
                else [f"app.kubernetes.io/instance={meta['name']}"]
            )
            for selector in selectors:
                pods = kubectl(
                    ["-n", meta["namespace"], "get", "pods", "-l", selector, "-o", "json"], env
                )
                if any(
                    p.get("status", {}).get("phase") not in {"Succeeded", "Failed"}
                    for p in pods.get("items", [])
                ):
                    waiting.append(meta["name"])
        if not waiting:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Collector handover could not prove source quiescence: " + ", ".join(waiting)
            )
        time.sleep(2)


def _wait_reconciled(collectors: list[dict], env, assert_authority: Callable) -> None:
    deadline = time.monotonic() + 180
    while True:
        assert_authority()
        waiting = []
        for release in collectors:
            meta = release["metadata"]
            live = _get("helmrelease", meta["namespace"], meta["name"], env)
            if not _current_ready(live):
                waiting.append(meta["name"])
        if not waiting:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Destination collector generation is not reconciled: " + ", ".join(waiting)
            )
        time.sleep(2)


def handover(
    paths,
    *,
    config,
    target_ref: str,
    env,
    apply: Callable,
    assert_authority: Callable,
    emit: Callable[[str], None],
) -> bool:
    documents = {
        path.name: yaml.safe_load(path.read_text()) for path in paths.flux_dir.glob("*.yaml")
    }
    collectors = {
        name: doc
        for name, doc in documents.items()
        if isinstance(doc, dict)
        and doc.get("kind") == "HelmRelease"
        and COMPONENT_ANNOTATION in doc.get("metadata", {}).get("annotations", {})
    }
    if not collectors:
        return False
    assert_authority()
    if not kubectl(
        ["get", "crd", "helmreleases.helm.toolkit.fluxcd.io", "--ignore-not-found", "-o", "json"],
        env,
    ):
        return False
    live = {
        name: _get("helmrelease", doc["metadata"]["namespace"], doc["metadata"]["name"], env)
        for name, doc in collectors.items()
    }
    if not any(live.values()):
        return False
    owner = app_owner(config, target_ref)
    cluster_uid = kubectl(["get", "namespace", "kube-system", "-o", "json"], env)["metadata"]["uid"]
    desired_hash = hashlib.sha256(json.dumps(collectors, sort_keys=True).encode()).hexdigest()
    namespace = kubectl(["get", "namespace", NAMESPACE, "--ignore-not-found", "-o", "json"], env)
    if not namespace:
        assert_authority()
        kubectl(
            ["create", "-f", "-", "-o", "json"],
            env,
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": NAMESPACE, "annotations": {OWNER: owner}},
            },
        )
    receipt = _get("configmap", NAMESPACE, RECEIPT, env)
    if receipt:
        data = receipt.get("data", {})
        if (
            receipt["metadata"].get("annotations", {}).get(OWNER) != owner
            or data.get("clusterUid") != cluster_uid
            or data.get("target") != target_ref
        ):
            raise RuntimeError("Observability handover receipt belongs to another target or owner")
        if data.get("phase") != "activated" and data.get("desiredHash") != desired_hash:
            raise RuntimeError(
                "Resume the recorded observability handover before changing its desired configuration"
            )
    if all(_same(live[name], doc) for name, doc in collectors.items()) and (
        not receipt or receipt.get("data", {}).get("phase") == "activated"
    ):
        return False

    vmagent_selectors = _vmagent_selectors(list(collectors.values()), env, receipt)

    def checkpoint(phase: str) -> None:
        nonlocal receipt
        assert_authority()
        record: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": RECEIPT, "namespace": NAMESPACE, "annotations": {OWNER: owner}},
            "data": {
                "target": target_ref,
                "clusterUid": cluster_uid,
                "desiredHash": desired_hash,
                "phase": phase,
                "vmAgentSelectors": json.dumps(vmagent_selectors, sort_keys=True),
            },
        }
        if receipt:
            record["metadata"]["resourceVersion"] = receipt["metadata"]["resourceVersion"]
        receipt = kubectl(
            ["replace" if receipt else "create", "-f", "-", "-o", "json"], env, record
        )

    alias = documents.get("service-cxcli-otlp.yaml")
    old_alias = _get("service", NAMESPACE, "cxcli-otlp", env) if alias else {}
    if old_alias and old_alias["metadata"].get("annotations", {}).get(OWNER) != owner:
        raise RuntimeError("Trace ingress belongs to another owner")

    def stage(mode: str) -> None:
        with tempfile.TemporaryDirectory(prefix="cxcli-telemetry-stage-") as temporary:
            directory = Path(temporary)
            shutil.copytree(paths.flux_dir, directory, dirs_exist_ok=True)
            for name, desired in collectors.items():
                component = desired["metadata"]["annotations"][COMPONENT_ANNOTATION]
                staged = desired
                if mode == "prepare":
                    staged = (
                        _clean(live[name]) if live[name] else inactive_release(desired, component)
                    )
                    if live[name] and component == "victoria-metrics-k8s-stack":
                        # Prepare the storage destination without changing the live producer.
                        staged["spec"].setdefault("values", {})["vmsingle"] = copy.deepcopy(
                            desired["spec"]["values"]["vmsingle"]
                        )
                elif mode == "quiesce":
                    staged = inactive_release(desired, component)
                (directory / name).write_text(yaml.safe_dump(staged, sort_keys=False))
            if alias:
                # Preserve the old address until destination reconciliation completes.
                (directory / "service-cxcli-otlp.yaml").write_text(
                    yaml.safe_dump(
                        _clean(old_alias)
                        if old_alias
                        else {"apiVersion": "v1", "kind": "List", "items": []}
                    )
                )
            assert_authority()
            apply(replace(paths, flux_dir=directory))

    checkpoint("preparing")
    emit("Observability handover: preparing destination stores and inactive collectors.")
    stage("prepare")
    checkpoint("quiescing")
    emit("Observability handover: stopping source collection before activation.")
    stage("quiesce")
    _wait_stopped(list(collectors.values()), env, assert_authority, vmagent_selectors)
    checkpoint("quiesced")
    stage("activate")
    _wait_reconciled(list(collectors.values()), env, assert_authority)
    if alias:
        assert_authority()
        kubectl(["apply", "-f", "-", "-o", "json"], env, alias)
    checkpoint("activated")
    emit("Observability handover activated; end-to-end delivery verification is still required.")
    return True
