"""Pure release candidate preparation, separate from live authority and execution."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from .deployment_preparation import PreparedRelease


def prepare_release_candidate(
    cli: Any,
    *,
    binding: str,
    source_payload: dict[str, Any],
    target: Any,
    active_intent: Any,
    live_release: str,
    target_selector: str,
    target_snapshot_sha256: str | None,
    jail_protection: str,
    checks_policy_proposal: str,
    desired_state_changed: bool,
    external_scheduling_evidence: Mapping[str, Any] | None,
    upgrade_progress: Any,
) -> PreparedRelease:
    source_payload = copy.deepcopy(source_payload)
    if active_intent is None:
        admission_row = cli._source_helm_chart_row(source_payload, target)
        admission_values = admission_row.get("values") or {}
        if jail_protection:
            from .soperator_jail_protection import apply_frozen_jail_protection

            admission_values = apply_frozen_jail_protection(admission_values, jail_protection)
        proposal = checks_policy_proposal or cli.freeze_checks_proposal(admission_values)
        admission_values, _ = cli.apply_checks_proposal(admission_values, proposal)
        artifact_request = cli.SoperatorArtifactRequest.deployment(
            target.target_ref,
            admission_values,
            payload=source_payload,
            post_render_patches=tuple(admission_row.get("post_render_patches") or ()),
        )
        if upgrade_progress is not None:
            with upgrade_progress.phase(
                "release-authority",
                f"Re-verifying frozen Soperator release {target_selector}",
                success=f"Soperator release {target_selector} authority verified",
            ) as release_phase:
                frozen_target = cli.freeze_soperator_release(
                    target_selector,
                    current_release=live_release,
                    snapshot_sha256=target_snapshot_sha256,
                    target_ref=target.target_ref,
                    request=artifact_request,
                    emit=release_phase.update,
                )
        else:
            frozen_target = cli.freeze_soperator_release(
                target_selector,
                current_release=live_release,
                snapshot_sha256=target_snapshot_sha256,
                target_ref=target.target_ref,
                request=artifact_request,
            )
        operation_source_release = live_release
        _source_metadata, source_release_receipt, source_contract, source_capability_sha = (
            cli.inspect_soperator_release_contract(live_release)
        )
    else:
        release_intent, frozen_snapshot = active_intent
        frozen_target = cli.frozen_soperator_release_from_snapshot(frozen_snapshot)
        operation_source_release = release_intent.source_release
        _source_metadata, source_release_receipt, observed_contract, observed_capability_sha = (
            cli.inspect_soperator_release_contract(operation_source_release)
        )
        if (
            observed_contract != release_intent.source_contract
            or observed_capability_sha != release_intent.source_capability_sha256
        ):
            raise RuntimeError("recovery-required: the frozen Soperator source capability changed")
        source_contract = release_intent.source_contract
        source_capability_sha = release_intent.source_capability_sha256
    target_version = frozen_target.snapshot.release
    chart_row = cli._source_helm_chart_row(source_payload, target)
    if jail_protection:
        from .soperator_jail_protection import apply_frozen_jail_protection

        chart_row["values"] = apply_frozen_jail_protection(
            chart_row.get("values") or {}, jail_protection
        )
    from .soperator_values import validate_observability_values

    validate_observability_values(chart_row.get("values") or {})
    if cli.EXPLICIT_VALUES_FIELD in chart_row:
        cli.validate_frozen_input(cli.soperator_explicit_values(chart_row), frozen_target)
    if not checks_policy_proposal:
        if active_intent is not None:
            raise RuntimeError("recovery-required: frozen checks policy proposal is missing")
        checks_policy_proposal = cli.freeze_checks_proposal(chart_row.get("values") or {})
    chart_row["values"], check_changes = cli.apply_checks_proposal(
        chart_row.get("values") or {}, checks_policy_proposal
    )
    for change in check_changes:
        cli.console.print(f"Proposed upstream checks policy: {change}")
    target_values = chart_row.get("values")
    if not isinstance(target_values, Mapping):
        raise RuntimeError("Soperator upgrade requires chart values for rootfs authority")
    target_jail_authority = cli.resolve_soperator_jail_image_authority(
        target_values,
        release=frozen_target.snapshot,
    )
    if active_intent is not None and (
        release_intent.target_jail_image != target_jail_authority.image
        or release_intent.target_jail_image_source != target_jail_authority.source
    ):
        raise RuntimeError(
            "recovery-required: the effective target Jail image changed after planning"
        )
    plan = cli._plan_helm_chart_upgrade(
        payload=source_payload,
        target=target,
        target_version=target_version,
    )
    strategy = cli.resolve_soperator_reconcile_strategy(
        current_release=operation_source_release,
        target_release=frozen_target.snapshot.release,
        source_contract=source_contract,
        target_contract=frozen_target.snapshot.capability_contract,
        desired_state_changed=(
            desired_state_changed
            or cli.checks_proposal_changed(checks_policy_proposal)
            or bool(
                external_scheduling_evidence
                and external_scheduling_evidence.get("requiresFreshChecks")
            )
        ),
    )
    if active_intent is not None and strategy.strategy.value != release_intent.strategy:
        raise RuntimeError("recovery-required: the frozen Soperator capability strategy changed")
    return PreparedRelease(
        binding,
        copy.deepcopy(
            {
                "source_payload": source_payload,
                "frozen_target": frozen_target,
                "operation_source_release": operation_source_release,
                "source_release_receipt": source_release_receipt,
                "source_contract": source_contract,
                "source_capability_sha": source_capability_sha,
                "target_version": target_version,
                "chart_row": chart_row,
                "checks_policy_proposal": checks_policy_proposal,
                "target_values": target_values,
                "target_jail_authority": target_jail_authority,
                "plan": plan,
                "strategy": strategy,
            }
        ),
    )


def assert_scheduling_inputs(
    cli: Any,
    *,
    target_ref: str,
    cluster_id: str,
    kubernetes_uid: str,
    kube_context: str,
    extra_env: Mapping[str, str],
    snapshot_sha256: str | None,
    policy: Mapping[str, object],
) -> None:
    """Reject proven conflicts before expensive release preparation; never grant authority."""
    record = cli._read_soperator_slurm_cluster_journal(
        target_ref=target_ref,
        cluster_id=cluster_id,
        kube_context=kube_context,
        extra_env=extra_env,
    )
    if record is None or record[0].get("status") == "restored":
        return
    journal = record[0]
    if (
        journal.get("schema") != cli.SOPERATOR_SLURM_RECOVERY_SCHEMA
        or journal.get("targetRef") != target_ref
    ):
        raise cli.SoperatorSafetyPauseError(
            "Unfinished Soperator scheduling journal identity is invalid"
        )
    recorded_policy = journal.get("policy")
    if not isinstance(recorded_policy, Mapping) or any(
        recorded_policy.get(key) != value for key, value in policy.items()
    ):
        raise cli.SoperatorSafetyPauseError(
            "recovery-required: restore the original Soperator execution controls"
        )
    operation = str(journal.get("operationSpecSha256") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", operation):
        # An unbound journal is decided by the existing under-lease binder.
        return
    cluster_digest = hashlib.sha256(cluster_id.encode()).hexdigest()[:10]
    name = f"nebius-cxcli-soperator-op-{cluster_digest}-{operation[7:17]}"
    result = cli._run_soperator_upgrade_kubectl_cluster(
        ["-n", "kube-system", "get", "configmap", name, "-o", "json"],
        kube_context=kube_context,
        extra_env=extra_env,
    )
    try:
        data = json.loads(result.stdout).get("data", {})
    except (ValueError, AttributeError) as exc:
        raise cli.SoperatorSafetyPauseError("Soperator operation anchor is invalid") from exc
    from .soperator_operation import SOPERATOR_OPERATION_ANCHOR_SCHEMA

    expected = {
        "schema": SOPERATOR_OPERATION_ANCHOR_SCHEMA,
        "clusterId": cluster_id,
        "kubernetesUid": kubernetes_uid,
        "targetRef": target_ref,
        "operationSpecSha256": operation,
        "operationId": operation,
    }
    if not isinstance(data, Mapping) or any(
        data.get(key) != value for key, value in expected.items()
    ):
        raise cli.SoperatorSafetyPauseError(
            "Soperator scheduling anchor identity differs from the verified target"
        )
    if snapshot_sha256 is not None and data.get("releaseSnapshotSha256") != snapshot_sha256:
        raise cli.SoperatorSafetyPauseError(
            "recovery-required: unfinished Soperator scheduling uses a different frozen release snapshot; "
            "restore the original generated bundle and execution controls before rerunning deploy"
        )
