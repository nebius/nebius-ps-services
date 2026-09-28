"""Bind a campaign's jail projection to its completed release child."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

import yaml

from .deployment_jail_state import jail_values
from .deployment_state import _read_regular
from .soperator_jail_protection import apply_frozen_jail_protection, freeze_jail_protection
from .soperator_operation import load_completed_soperator_release_intent, soperator_sha256
from .soperator_receipt_io import read_owner_only_json
from .soperator_rootfs_transition import recover_soperator_rootfs_transition


def completed_release_handoff(
    cli: Any,
    *,
    paths: Any,
    target: Mapping[str, Any],
    intent: Any,
    config_store: Any,
) -> dict[str, Any] | None:
    """A completed child plus its campaign config transition closes the lost-ack window."""
    transition = config_store.get("release-admission")
    if transition is None:
        return None
    child_paths = cli._paths_for_target_flux_dir(paths, target)
    child = load_completed_soperator_release_intent(paths=child_paths, target_ref=intent.target_ref)
    if child is None:
        return None
    if transition.status != "applied":
        raise RuntimeError("Completed release child has an unapplied config transition")
    if (
        child.target_ref != intent.target_ref
        or child.ownership != intent.ownership
        or child.nebius_cluster_id != intent.cluster_id
        or child.kubernetes_uid != intent.kubernetes_uid
        or child.target_release != intent.target_release
        or child.release_snapshot_sha256 != intent.checks_release_snapshot_sha256
    ):
        raise RuntimeError("Completed release child differs from the campaign authority")
    admission = read_owner_only_json(
        cli._soperator_upgrade_admission_path(child_paths, intent.target_ref),
        label="Completed release admission",
    )
    if (
        not isinstance(admission, Mapping)
        or admission.get("targetRef") != intent.target_ref
        or admission.get("targetRelease") != child.target_release
        or admission.get("projectGenerationSha256") != transition.project_generation_sha256
    ):
        raise RuntimeError("Completed release admission differs from its config transition")
    # Repair operations carry a monotonically increasing generation in their
    # immutable spec. Inventory must still resolve to exactly one completed child.
    matches = []
    for path in child_paths.reports_dir.glob("soperator-release-reconcile-*.json"):
        receipt = read_owner_only_json(path, label="Completed release reconcile")
        if not isinstance(receipt, Mapping) or not isinstance(receipt.get("operation"), Mapping):
            raise RuntimeError("Completed release reconcile inventory is invalid")
        spec = receipt["operation"].get("spec", {})
        if not isinstance(spec, Mapping):
            raise RuntimeError("Completed release reconcile spec is invalid")
        if soperator_sha256(spec) == child.operation_spec_sha256:
            matches.append(receipt)
    if len(matches) != 1 or matches[0].get("status") != "complete":
        raise RuntimeError("Completed release child has no exact completed reconcile receipt")
    receipt = matches[0]
    spec = receipt["operation"]["spec"]
    if (
        spec.get("admission_sha256") != soperator_sha256(admission)
        or admission.get("clusterId") != intent.cluster_id
        or admission.get("kubernetesUid") != intent.kubernetes_uid
        or admission.get("releaseSnapshotSha256") != child.release_snapshot_sha256
    ):
        raise RuntimeError("Completed release admission is not bound by its operation")
    values = apply_frozen_jail_protection({}, intent.jail_protection)
    rootfs_evidence: dict[str, Any] = {}
    if child.strategy in {"in-place", "noop"}:
        if admission.get("rootfsPreflight") != {"mode": "not-required"} or admission.get(
            "rootfsTransition"
        ) != {"mode": "not-required"}:
            raise RuntimeError("In-place release has unexpected rootfs transition evidence")
    else:
        owner, predecessor = cli._soperator_upgrade_canonical_rootfs_predecessor(
            paths=child_paths,
            target_ref=intent.target_ref,
            predecessor_receipt=receipt,
            predecessor_operation_spec_sha256=child.operation_spec_sha256,
            predecessor_intervention_generation=int(spec["intervention_generation"]),
        )
        preflight = cli.SoperatorRootfsAdmissionPreflight.from_payload(admission["rootfsPreflight"])
        owner_spec = predecessor["operation"]["spec"]
        journal = read_owner_only_json(
            child_paths.reports_dir
            / f"soperator-recovery-{owner.removeprefix('sha256:')[:20]}.json",
            label="Completed rootfs materialization",
        )
        materialization = cli._soperator_upgrade_sealed_rootfs_materialization(
            journal,
            expected_operation_spec_sha256=owner,
            expected_cluster_id=intent.cluster_id,
            expected_kubernetes_uid=intent.kubernetes_uid,
            expected_source_release=owner_spec["current_release"],
            expected_target_release=owner_spec["target_release"],
            expected_infrastructure_sha256=owner_spec["infrastructure_plan_sha256"],
            preflight=preflight,
        )
        values = recover_soperator_rootfs_transition(
            apply_frozen_jail_protection({}, intent.jail_protection),
            admission["rootfsTransition"],
            target_ref=intent.target_ref,
            layout="external" if intent.ownership == "onboarded" else "managed",
        )
        rootfs_evidence = {
            "rootfsAdmissionSha256": preflight.receipt_sha256,
            "materialization": materialization,
            "directoryIdentities": list(preflight.directory_identities),
        }
    protection = freeze_jail_protection(values)
    committed = yaml.safe_load(_read_regular(paths.config_path))
    if not isinstance(committed, Mapping) or protection != freeze_jail_protection(
        jail_values(committed, intent.target_ref)
    ):
        raise RuntimeError("Completed release physical mapping differs from the committed config")
    return {
        "jailProtection": protection,
        "childIntentSha256": soperator_sha256(asdict(child)),
        "operationSpecSha256": child.operation_spec_sha256,
        "reconcileReceiptSha256": soperator_sha256(receipt),
        "configTransitionSha256": transition.evidence_sha256,
        **rootfs_evidence,
    }
