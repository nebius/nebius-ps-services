"""Locate the exact local Nsight recovery owner by target, stage and predecessor UID."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping

from .deployment_applications import recorded_application_identity
from .deployment_local import LocalObjectStore
from .deployment_state import DeploymentState, digest
from .deployment_workflow import _semantic_controls
from .nsight_recovery import validate_chain
from .soperator_protected_data_plane import _kubernetes_operation_label, protected_workload_identity
from .soperator_recovery_journal import SOPERATOR_RECOVERY_JOURNAL_SCHEMA


def _chain_matches(chain, *, stage, job_uid):
    if (
        not isinstance(chain, dict)
        or not isinstance(chain.get("attempts"), list)
        or not any(
            isinstance(row, dict) and row.get("jobUid") == job_uid
            for row in chain.get("attempts", [])
        )
    ):
        return False
    validate_chain(chain)
    identity = protected_workload_identity(chain["baseManifest"])
    if identity.purpose != stage.replace("nsight-", "profiling-"):
        raise RuntimeError("Nsight recovery stage identity differs from its attempt")
    return True


def _owns_job(record, *, paths, target_ref, stage, job_uid):
    active = record.value.get("active")
    selection = active["plan"].get("semanticPlan") if active else None
    if (
        not isinstance(selection, Mapping)
        or not isinstance(selection.get("selectedTargets"), list)
        or target_ref not in selection["selectedTargets"]
    ):
        return False
    if active["plan"].get("kind") == "nsight-profiling":
        return _chain_matches(active["stages"].get(stage), stage=stage, job_uid=job_uid)
    prefix = f"{paths.path_tenant_folder}/{paths.path_project_folder}/generated/reports/"
    for key, encoded in active["recovery"].items():
        if (
            not isinstance(key, str)
            or not key.startswith(prefix)
            or not re.fullmatch(r"soperator-recovery-[a-f0-9]{20}\.json", key.removeprefix(prefix))
        ):
            continue
        try:
            journal = json.loads(base64.b64decode(encoded, validate=True))
        except (ValueError, TypeError, binascii.Error):
            continue
        if (
            not isinstance(journal, dict)
            or journal.get("schema") != SOPERATOR_RECOVERY_JOURNAL_SCHEMA
        ):
            continue
        stages = journal.get("stages")
        row = stages.get("rootfs-" + stage) if isinstance(stages, Mapping) else None
        chain = row.get("nsight") if isinstance(row, Mapping) else None
        if not _chain_matches(chain, stage=stage, job_uid=job_uid):
            continue
        identity = recorded_application_identity(record.value, paths=paths, target_ref=target_ref)
        operation = str(journal.get("operationId") or "")
        if (
            not identity
            or journal.get("clusterId") != identity["cluster_id"]
            or journal.get("kubernetesUid") != identity["kubernetes_uid"]
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", operation)
            or protected_workload_identity(chain["baseManifest"]).operation_id
            != _kubernetes_operation_label(operation)
            or key != prefix + f"soperator-recovery-{operation[7:27]}.json"
            or journal.get("status") != "active"
        ):
            raise RuntimeError("Nsight recovery journal identity differs from its frozen attempt")
        return True
    return False


def recovery_owner(paths, backend, *, target_ref, stage, job_uid, assert_held):
    """Read-only lookup; the owning command rechecks this exact record after locking."""
    store = LocalObjectStore.for_project(paths)
    standalone = DeploymentState(
        store, backend, command="profiling-" + target_ref, assert_held=assert_held
    )
    candidates = [standalone]
    candidates.extend(
        DeploymentState(
            store, backend, attempt=key.rsplit("/", 1)[-1][:-5], assert_held=assert_held
        )
        for key in store.attempt_keys(standalone.prefix)
    )
    matches = []
    for state in candidates:
        # A missing or terminal standalone journal cannot seed this explicit
        # recovery lookup from the optional completion report.
        try:
            stored = store.read(state.record_key)
            if stored is None or not stored.value.get("active"):
                continue
            record = state.read()
        except (OSError, ValueError, RuntimeError):
            # Invalid history is not a candidate owner. Its bytes remain intact;
            # recovery still requires an exact valid UID and live authority.
            continue
        if record is None or not _owns_job(
            record, paths=paths, target_ref=target_ref, stage=stage, job_uid=job_uid
        ):
            continue
        active = record.value["active"]
        state.generation(active["generation"])
        if (
            state.attempt
            and state.attempt
            != digest(
                {
                    "generation": active["generation"],
                    "controls": _semantic_controls(active["plan"].get("controls", {})),
                }
            )[7:]
        ):
            raise RuntimeError("Nsight recovery attempt filename differs from its frozen controls")
        matches.append((state, record))
    if len(matches) != 1:
        raise RuntimeError(
            "Nsight recovery owner is ambiguous"
            if matches
            else "No active frozen deployment attempt owns this target, stage and Job UID"
        )
    return matches[0]
