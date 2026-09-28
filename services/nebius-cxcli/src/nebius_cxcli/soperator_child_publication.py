"""Materialize explicit empty values maps left null by SSA field pruning.

This is an execution postcondition, never a null/empty equality exception.
Only fingerprints and identities enter the durable journal.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .soperator_failures import SoperatorSafetyPauseError
from .soperator_operation import soperator_sha256

if TYPE_CHECKING:
    from .soperator_graph_transition import NativeGraphTransition


def empty_values_postimage(live: Mapping[str, Any], desired: Mapping[str, Any]) -> bool:
    """All differences must be present null -> explicit {} below values."""

    def matches(before: Any, after: Any, *, values: bool = False) -> bool:
        if values and before is None and isinstance(after, Mapping) and not after:
            return True
        if isinstance(before, Mapping) and isinstance(after, Mapping):
            return before.keys() == after.keys() and all(
                matches(before[key], after[key], values=values) for key in before
            )
        # Arrays are atomic here: no admission of null elements or reordered rows.
        return soperator_sha256(before) == soperator_sha256(after)

    before = {key: value for key, value in live.items() if key != "suspend"}
    after = {key: value for key, value in desired.items() if key != "suspend"}
    return before.keys() == after.keys() and all(
        matches(before[key], after[key], values=key == "values") for key in before
    )


def _binding(
    operation: NativeGraphTransition,
    parent: Mapping[str, Any],
    child: Mapping[str, Any],
    manifest: list[Mapping[str, Any]],
) -> dict[str, Any]:
    from .soperator_graph_transition import identity, ownership_digest, spec_digest

    proof = operation.state.get("parentReconciliation", {})
    history = parent.get("status", {}).get("history", [])
    revision = str(history[0]["version"]) if history else ""
    manifest_sha = soperator_sha256(manifest)
    if (
        parent["spec"].get("suspend") is not True
        or parent["metadata"].get("deletionTimestamp")
        or child["metadata"].get("deletionTimestamp")
        or any(
            row.get("type") == "Reconciling" and row.get("status") == "True"
            for row in parent.get("status", {}).get("conditions", [])
        )
        or not history
        or history[0].get("status") != "deployed"
        or proof.get("revision") != revision
        or proof.get("manifestSha256") != manifest_sha
        or proof.get("specSha256")
        not in {
            operation.state.get("publishedSpecSha256"),
            *operation.state.get("parentPublication", {}).values(),
        }
    ):
        raise SoperatorSafetyPauseError("Native child publication has no quiescent parent proof")
    namespace, name = identity(child)
    return {
        "namespace": namespace,
        "name": name,
        "uid": child["metadata"]["uid"],
        "ownershipSha256": ownership_digest(child),
        "parentPublicationSha256": spec_digest(parent),
        "parentRevision": revision,
        "parentManifestSha256": manifest_sha,
    }


def pending_empty_values(
    operation: NativeGraphTransition,
    parent: Mapping[str, Any],
    child: Mapping[str, Any],
    manifest: list[Mapping[str, Any]],
    desired: Mapping[str, Any],
) -> bool:
    """Read-only pending classification; this never proves child convergence."""
    if child["spec"].get("suspend") is not True or not empty_values_postimage(
        child["spec"], desired
    ):
        return False
    binding = _binding(operation, parent, child, manifest)
    intent = operation.state.get("childMaterializations", {}).get(soperator_sha256(binding))
    if intent is not None:
        from .soperator_graph_transition import spec_digest

        allowed = {intent["afterSha256"]}
        if intent["status"] == "pending":
            allowed.add(intent["beforeSha256"])
        if spec_digest(child) not in allowed:
            raise SoperatorSafetyPauseError("Native child materialization postimage changed")
        return True
    spec = parent["spec"]
    metadata = json.loads(
        operation.run(
            [
                "helm",
                "get",
                "metadata",
                spec.get("releaseName") or parent["metadata"]["name"],
                "-n",
                spec.get("storageNamespace") or parent["metadata"]["namespace"],
                "--revision",
                binding["parentRevision"],
                "-o",
                "json",
            ]
        ).stdout
    )
    return (
        metadata.get("applyMethod") == "ssa"
        and str(metadata.get("revision")) == binding["parentRevision"]
        and metadata.get("status") == "deployed"
    )


def materialize_child(
    operation: NativeGraphTransition,
    parent: Mapping[str, Any],
    child: Mapping[str, Any],
    manifest: list[Mapping[str, Any]],
    desired: Mapping[str, Any],
) -> Mapping[str, Any]:
    from .soperator_graph_transition import HR, ownership_digest, spec_digest

    binding = _binding(operation, parent, child, manifest)
    key = soperator_sha256(binding)
    records = operation.state.setdefault("childMaterializations", {})
    intent = records.get(key)
    after_sha = spec_digest({"spec": desired})
    for previous in records.values():
        if (previous["namespace"], previous["name"]) == (
            binding["namespace"],
            binding["name"],
        ) and (
            previous["uid"] != binding["uid"]
            or previous["ownershipSha256"] != binding["ownershipSha256"]
            or (previous["status"] == "pending" and previous is not intent)
        ):
            raise SoperatorSafetyPauseError("Native child publication changed its durable identity")
    opened = operation.state.get("openedChildren", {}).get(
        binding["namespace"] + "/" + binding["name"]
    )
    if opened is not None and opened["uid"] != binding["uid"]:
        raise SoperatorSafetyPauseError("Native stage child changed after opening intent")
    if intent is None:
        if not pending_empty_values(operation, parent, child, manifest, desired):
            raise SoperatorSafetyPauseError(
                "Native stage child differs from its published Helm contract"
            )
        intent = {
            **binding,
            "beforeSha256": spec_digest(child),
            "afterSha256": after_sha,
            "status": "pending",
        }
        records[key] = intent
        operation.save(operation.state["phase"])
    if intent["afterSha256"] != after_sha or spec_digest(child) not in {
        intent["afterSha256"],
        *([intent["beforeSha256"]] if intent["status"] == "pending" else []),
    }:
        raise SoperatorSafetyPauseError("Native child materialization postimage changed")
    if spec_digest(child) != after_sha:
        if child["spec"].get("suspend") is not True:
            raise SoperatorSafetyPauseError(
                "Native child materialization lost its suspension fence"
            )
        # Recheck the parent immediately before the child CAS; the CAS itself
        # tests exact child UID, RV and the complete preimage specification.
        current_parent = operation._published_parent()
        if (
            _binding(operation, current_parent, child, operation.parent_manifest(current_parent))
            != binding
        ):
            raise SoperatorSafetyPauseError(
                "Native child publication changed before materialization"
            )
        operation._cas(child, {**copy.deepcopy(desired), "suspend": True})
        child = operation.get(HR, binding["namespace"], binding["name"])
    if (
        child.get("metadata", {}).get("uid") != binding["uid"]
        or child.get("metadata", {}).get("deletionTimestamp")
        or ownership_digest(child) != binding["ownershipSha256"]
        or spec_digest(child) != after_sha
    ):
        raise SoperatorSafetyPauseError(
            "Native child materialization did not reach its exact postimage"
        )
    intent["status"] = "verified"
    operation.save(operation.state["phase"])
    return child


def recover_pending_materializations(operation: NativeGraphTransition) -> None:
    """Finish durable child CAS intents before another parent publication."""
    from .soperator_graph_transition import HR, canonical_spec, identity, owned_by

    for intent in list(operation.state.get("childMaterializations", {}).values()):
        if intent["status"] != "pending":
            continue
        parent = operation._published_parent()
        manifest = operation.parent_manifest(parent)
        child = operation.get(HR, intent["namespace"], intent["name"])
        if not child or not owned_by(child, parent):
            raise SoperatorSafetyPauseError("Pending native child publication changed identity")
        binding = _binding(operation, parent, child, manifest)
        if any(value != intent.get(key) for key, value in binding.items()):
            raise SoperatorSafetyPauseError("Pending native child publication changed identity")
        matches = [
            row
            for row in manifest
            if row.get("kind") == "HelmRelease" and identity(row) == identity(child)
        ]
        if len(matches) != 1:
            raise SoperatorSafetyPauseError(
                "Pending native child disappeared from its parent manifest"
            )
        materialize_child(
            operation,
            parent,
            child,
            manifest,
            canonical_spec(operation.run, child, matches[0]["spec"]),
        )
