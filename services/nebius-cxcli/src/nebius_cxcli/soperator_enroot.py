"""Declarative Enroot user namespaces, owned at runtime by upstream SPO."""

from __future__ import annotations

import copy
import json
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

ENROOT_PROFILE_NAME = "cxcli-soperator-enroot-v1"
ENROOT_PROFILE_API = "security-profiles-operator.x-k8s.io/v1alpha1"
ENROOT_PROFILE_POLICY = (
    "abi <abi/4.0>,\n"
    f"profile {ENROOT_PROFILE_NAME} /usr/bin/enroot-nsenter flags=(unconfined) {{\n"
    "  userns,\n"
    "}\n"
)


def enroot_profile_document(labels: Mapping[str, str]) -> dict[str, Any]:
    return {
        "apiVersion": ENROOT_PROFILE_API,
        "kind": "AppArmorProfile",
        "metadata": {
            "name": ENROOT_PROFILE_NAME,
            "namespace": "soperator",
            "labels": dict(labels),
        },
        "spec": {"policy": ENROOT_PROFILE_POLICY},
    }


def split_enroot_profile_documents(
    documents: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate before mutation, deferring this CR until the upstream API exists."""
    profiles, other = [], []
    for doc in documents:
        if doc.get("kind") != "AppArmorProfile":
            other.append(doc)
            continue
        metadata = doc.get("metadata", {})
        labels = metadata.get("labels", {})
        if (
            doc.get("apiVersion") != ENROOT_PROFILE_API
            or metadata.get("name") != ENROOT_PROFILE_NAME
            or metadata.get("namespace") != "soperator"
            or labels.get("soperator.nebius.ai/managed-by") != "nebius-cxcli-adapter"
            or labels.get("soperator.nebius.ai/lifecycle") != "recreatable"
            or doc.get("spec") != {"policy": ENROOT_PROFILE_POLICY}
        ):
            raise ValueError("Soperator Enroot profile identity or permission changed")
        profiles.append(doc)
    if len(profiles) > 1:
        raise ValueError("Soperator Enroot profile is ambiguous")
    return other, profiles


def enroot_profile_nodes_ready(
    profile: Mapping[str, Any], nodes: Mapping[str, Any], statuses: Mapping[str, Any]
) -> bool:
    metadata = profile.get("metadata", {})
    if (
        not metadata.get("uid")
        or metadata.get("deletionTimestamp")
        or metadata.get("name") != ENROOT_PROFILE_NAME
        or metadata.get("namespace") != "soperator"
        or profile.get("spec") != {"policy": ENROOT_PROFILE_POLICY}
    ):
        raise RuntimeError("Soperator Enroot profile lost its declared identity")
    expected = {
        node["metadata"]["name"]
        for node in nodes.get("items", [])
        if node.get("metadata", {}).get("uid")
        and not node["metadata"].get("deletionTimestamp")
        and any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in node.get("status", {}).get("conditions", [])
        )
    }
    installed: set[str] = set()
    observed: set[str] = set()
    for status in statuses.get("items", []):
        meta = status.get("metadata", {})
        owners = [o for o in meta.get("ownerReferences", []) if o.get("controller") is True]
        if not any(o.get("uid") == metadata["uid"] for o in owners):
            continue
        if (
            len(owners) != 1
            or owners[0].get("kind") != "AppArmorProfile"
            or owners[0].get("name") != ENROOT_PROFILE_NAME
            or not meta.get("uid")
            or meta.get("deletionTimestamp")
            or meta.get("namespace") != "soperator"
        ):
            raise RuntimeError("Soperator Enroot per-node profile ownership changed")
        node = status.get("nodeName", "")
        if not node or node in observed:
            raise RuntimeError("Soperator Enroot per-node profile status is ambiguous")
        observed.add(node)
        if status.get("status") == "Installed":
            installed.add(node)
    return bool(expected) and expected <= installed


def apply_enroot_profile(
    profiles: Sequence[dict[str, Any]],
    *,
    env: Mapping[str, str],
    cache_dir: Path,
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> None:
    if not profiles:
        return
    _, validated = split_enroot_profile_documents(profiles)
    base = ["kubectl", "--cache-dir", str(cache_dir)]

    def read(args: list[str]) -> Mapping[str, Any]:
        result = subprocess.run(
            [*base, "get", *args, "-o", "json"],
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return json.loads(result.stdout or "{}")

    desired = validated[0]
    # Kernel profile names and executable attachments are node-wide even though
    # the upstream CR is namespaced. Do not overwrite another namespace's policy.
    inventory = read(["apparmorprofiles", "-A"])
    for existing in inventory.get("items", []):
        metadata = existing.get("metadata", {})
        if metadata.get("name") != ENROOT_PROFILE_NAME and "enroot-nsenter" not in str(
            existing.get("spec", {}).get("policy", "")
        ):
            continue
        labels = existing.get("metadata", {}).get("labels", {})
        if (
            metadata.get("name") != ENROOT_PROFILE_NAME
            or metadata.get("namespace") != "soperator"
            or metadata.get("deletionTimestamp")
            or existing.get("spec") != desired["spec"]
            or any(
                labels.get(k) != v
                for k, v in desired["metadata"]["labels"].items()
                if k in {"soperator.nebius.ai/managed-by", "soperator.nebius.ai/lifecycle"}
            )
        ):
            raise RuntimeError("Soperator Enroot profile collides with existing configuration")
    subprocess.run(
        [*base, "apply", "-f", "-"],
        env=dict(env),
        input=yaml.safe_dump_all(copy.deepcopy(validated), sort_keys=False),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    deadline = time.monotonic() + timeout_seconds
    while True:
        profile = read(["apparmorprofiles", ENROOT_PROFILE_NAME, "-n", "soperator"])
        nodes = read(["nodes"])
        statuses = read(["securityprofilenodestatuses", "-n", "soperator"])
        if enroot_profile_nodes_ready(profile, nodes, statuses):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "upstream SPO has not installed the Enroot profile on every ready node"
            )
        time.sleep(poll_interval_seconds)
