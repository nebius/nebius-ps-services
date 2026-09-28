"""Durable completion evidence for the exact final Soperator operation."""

from __future__ import annotations

import json
import re
from dataclasses import asdict

from .deployment_state import digest
from .soperator_receipt_io import read_owner_only_json, write_owner_only_json


def completion_path(paths, target_ref):
    if not re.fullmatch(r"[a-z0-9-]{1,63}", target_ref):
        raise ValueError("Operation completion requires an exact target reference")
    return paths.reports_dir / ("soperator-operation-completion-" + target_ref + ".json")


def prepare_completion(anchor, paths, target_ref, *, source_paths=None, generation=None):
    """Freeze expected terminal evidence before sealing; readers must verify live."""
    result = anchor._kubectl("get", "configmap", anchor.name, "-o", "json")
    if result.returncode:
        raise RuntimeError("Completed operation evidence could not be read")
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Completed operation evidence is malformed") from exc
    data, meta = payload.get("data", {}), payload.get("metadata", {})
    if any(
        data.get(k) != v for k, v in anchor._expected_data(status="active").items()
    ) or not meta.get("uid"):
        raise RuntimeError("Final operation completion identity changed")
    from .deployment_applications import target_bundle_digest
    from .deployment_state import DeploymentGeneration
    from .generated_manifest import load_generated_manifest

    if generation is None:
        source = source_paths or paths
        generation = DeploymentGeneration.capture(
            source, load_generated_manifest(source.generated_dir)
        )
    body = {
        "desiredBundle": target_bundle_digest(generation, target_ref),
        "schema": "nebius-cxcli.operation-completion.v1",
        "name": anchor.name,
        "uid": meta["uid"],
        "data": {**data, "status": "complete"},
        "spec": asdict(anchor.operation_spec),
    }
    proof = {**body, "receiptSha256": digest(body)}
    write_owner_only_json(completion_path(paths, target_ref), proof)
    return proof


def verify_completion(proof, *, identity, target_ref, desired_bundle, env):
    from .installation_reconciliation import _read
    from .soperator_operation import SOPERATOR_OPERATION_ANCHOR_SCHEMA

    body = {k: v for k, v in proof.items() if k != "receiptSha256"}
    data = proof.get("data", {})
    if (
        proof.get("schema") != "nebius-cxcli.operation-completion.v1"
        or proof.get("receiptSha256") != digest(body)
        or data.get("schema") != SOPERATOR_OPERATION_ANCHOR_SCHEMA
        or digest(proof.get("spec")) != data.get("operationSpecSha256")
        or data.get("operationId") != data.get("operationSpecSha256")
        or data.get("status") != "complete"
        or data.get("targetRef") != target_ref
        or data.get("clusterId") != identity["cluster_id"]
        or data.get("kubernetesUid") != identity["kubernetes_uid"]
    ):
        raise RuntimeError("Final operation completion receipt is invalid")
    if proof.get("desiredBundle") != desired_bundle:
        return None
    current = _read(["get", "configmap", proof["name"], "-n", "kube-system", "-o", "json"], env)
    live = current.get("data", {})
    authority_fields = {"leaseUid", "holderIdentitySha256", "fencingEpoch", "status"}
    if (
        current.get("metadata", {}).get("uid") == proof.get("uid")
        and live.get("status") == "active"
    ) and (
        {k: v for k, v in live.items() if k not in authority_fields}
        == {k: v for k, v in data.items() if k not in authority_fields}
        and str(live.get("fencingEpoch", "")).isdigit()
        and int(live["fencingEpoch"]) >= int(data["fencingEpoch"])
    ):
        return None
    if current.get("metadata", {}).get("uid") != proof.get("uid") or current.get("data") != data:
        raise RuntimeError("Final operation completion evidence changed in the cluster")
    return proof


def load_completion(paths, target_ref):
    path = completion_path(paths, target_ref)
    return read_owner_only_json(path, label="Operation completion") if path.exists() else None
