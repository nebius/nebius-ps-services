"""Recover the infrastructure identity of an already-bound installation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from .soperator_operation import SoperatorOperationSpec, soperator_sha256
from .soperator_receipt_io import read_owner_only_json
from .soperator_release_reconciler import SOPERATOR_RECONCILE_RECEIPT_SCHEMA


def bound_install_infrastructure_identity(
    reports_dir: Path, *, target_ref: str, cluster_id: str, operation_spec_sha256: str
) -> str:
    """Read the exact receipt selected by the cluster-authoritative scheduling journal.

    The caller must still check the freshly built complete operation against that
    journal. A new no-op Terraform plan is a verification artifact, not a new
    identity for infrastructure already completed by this installation.
    """
    matches = []
    for path in reports_dir.glob("soperator-release-reconcile-*.json"):
        receipt = read_owner_only_json(path, label="Soperator install resume receipt")
        if not isinstance(receipt, Mapping):
            continue
        operation = receipt.get("operation")
        spec = operation.get("spec") if isinstance(operation, Mapping) else None
        if isinstance(spec, Mapping) and soperator_sha256(spec) == operation_spec_sha256:
            matches.append(receipt)
    if not matches:
        from .soperator_install_observability_repair import pending_infrastructure_identity

        pending = pending_infrastructure_identity(
            reports_dir,
            target_ref=target_ref,
            cluster_id=cluster_id,
            operation_spec_sha256=operation_spec_sha256,
        )
        if pending is not None:
            return pending
    if len(matches) != 1:
        raise RuntimeError("Install resume requires one exact bound reconcile receipt")
    receipt = matches[0]
    try:
        spec = SoperatorOperationSpec(**receipt["operation"]["spec"])
    except TypeError as exc:
        raise RuntimeError("Install resume receipt has an invalid operation specification") from exc
    if (
        receipt.get("schema") != SOPERATOR_RECONCILE_RECEIPT_SCHEMA
        or spec.strategy != "install"
        or spec.current_release != ""
        or spec.target_ref != target_ref
        or spec.nebius_cluster_id != cluster_id
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", spec.infrastructure_plan_sha256)
    ):
        raise RuntimeError("Install resume receipt belongs to different immutable inputs")
    return spec.infrastructure_plan_sha256
