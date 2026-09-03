from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import paramiko
import pytest
import typer
import yaml
from typer.testing import CliRunner

import nebius_vpngw.cli as cli_module
from nebius_vpngw.agent.vm_ha.auto_healing import (
    AutoHealingPolicyPhase,
    AutoHealingPolicyRecord,
    AutoHealingPolicyStore,
    AutoHealingRecoveryPhase,
    AutoHealingRecoveryRecord,
    StandbyAutoHealing,
    auto_healing_recovery_digest,
    decode_policy_request,
    policy_decision_digest,
)
from nebius_vpngw.cli import (
    _commit_missing_vm_ha_standby_replacement_active,
    _create_missing_vm_ha_standby_under_owner_inhibition,
    _emit_vm_ha_progress,
    _execute_vm_ha_apply_convergence,
    _execute_vm_ha_artifact_standby_recovery,
    _file_fingerprint,
    _inspect_vm_ha_command_status,
    _plan_vm_ha_apply_convergence,
    _reconcile_vm_ha_replacement_auto_healing_policy,
    _release_missing_vm_ha_standby_inhibition,
    _resolve_vm_ha_effective_config,
    _validate_vm_ha_expected_apply_plan,
    _vm_ha_apply_plan_impact,
    _vm_ha_artifact_recovery_owner_is_safe,
    _vm_ha_artifact_recovery_topology,
    _vm_ha_auto_healing_approval_digest,
    _vm_ha_auto_healing_recovery_required,
    _vm_ha_auto_healing_transaction_for_statuses,
    _vm_ha_replacement_policy_adoption_request,
    _vm_ha_result_from_snapshot,
    _vm_ha_snapshot_digest,
    _vm_ha_status_spinner,
    _VMHAActivationFailed,
    _VMHAActivationUnsafe,
    _VMHAApplyConvergenceFailed,
    _VMHAApplyPlanCaptured,
    _VMHAApplyPlanningFailed,
    _VMHAApplyPlanReport,
    _VMHAArtifactRecoveryContext,
    _VMHAAutoHealingTransaction,
    _VMHACloudAuthority,
    _VMHACommandInspection,
    _VMHAEffectiveConfig,
    _VMHAMemberEvidence,
    _VMHAMissingStandbyReplacementPlan,
    _VMHAProgressEvent,
    _VMHAProgressPhase,
    _VMHAProgressReporter,
    _VMHAProgressState,
    _VMHAStatusSnapshot,
    _VMHAStatusView,
    app,
)
from nebius_vpngw.deploy.ssh_push import (
    VMHAAgentArtifact,
    VMHAAgentArtifactError,
    VMHAAgentArtifactProblem,
)
from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStatus
from nebius_vpngw.nebius_auth import NebiusCLIAuthenticationError
from nebius_vpngw.vm_ha_command import (
    VMHACommandClassification,
    VMHACommandHealth,
    VMHACommandImpact,
    VMHACommandOutcome,
    VMHACommandResult,
)
from nebius_vpngw.vm_ha_credentials import VMHACredentialIdentityError


class _ContextManagedFake:
    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        return None


def _write_vm_ha_agent_wheel(wheel: Path) -> None:
    dist_info = "nebius_vpngw-1.2.3.dist-info"
    members = {
        "nebius_vpngw/agent/main.py": (
            b"import argparse\nimport json\n"
            b"from .vm_ha.inhibition import LIVE_PEER_REPLACEMENT_CAPABILITY\n"
            b"from .vm_ha.restoration import STANDBY_RESTORATION_CAPABILITY\n"
            b"def main():\n"
            b"    parser = argparse.ArgumentParser()\n"
            b"    group = parser.add_mutually_exclusive_group()\n"
            b"    group.add_argument('--agent-capabilities', action='store_true')\n"
            b"    args = parser.parse_args()\n"
            b"    if args.agent_capabilities:\n"
            b"        print(json.dumps({'features': "
            b"[LIVE_PEER_REPLACEMENT_CAPABILITY, STANDBY_RESTORATION_CAPABILITY], "
            b"'schema': 'nebius-vpngw.agent-capabilities.v1'}))\n"
            b"        return\n"
            b"if __name__ == '__main__':\n"
            b"    main()\n"
        ),
        "nebius_vpngw/agent/vm_ha/restoration.py": (
            b'STANDBY_RESTORATION_CAPABILITY = "vm-ha-standby-restoration-v2"\n'
        ),
        "nebius_vpngw/agent/vm_ha/inhibition.py": (
            b'LIVE_PEER_REPLACEMENT_CAPABILITY = "vm-ha-live-peer-replacement-v4"\n'
        ),
        f"{dist_info}/METADATA": b"Metadata-Version: 2.4\nName: nebius-vpngw\nVersion: 1.2.3\n",
        f"{dist_info}/WHEEL": (b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"),
    }
    for asset_name in (
        "nebius-vpngw-agent.service",
        "nebius-vpngw-esp4-preflight.sh",
        "nebius-vpngw-fix-routes.service",
        "nebius-vpngw-fix-routes.timer",
        "nebius-vpngw-health-monitor.service",
        "nebius-vpngw-ufw-lock.conf",
        "nebius-vpngw-vm-ha-guard.service",
        "nebius-vpngw-vm-ha-ordering.conf",
        "nebius-vpngw-vm-ha-peer-firewall.sh",
        "nebius-vpngw-vm-ha-rearm.service",
        "nebius-vpngw-vm-ha.service",
        "setup-vpngw-firewall.sh",
    ):
        members[f"nebius_vpngw/systemd/{asset_name}"] = f"fixture:{asset_name}\n".encode()
    record_path = f"{dist_info}/RECORD"
    record_lines = []
    for name, payload in members.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
        record_lines.append(f"{name},sha256={digest},{len(payload)}")
    record_lines.append(f"{record_path},,")
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
        archive.writestr(record_path, "\n".join(record_lines) + "\n")


def _snapshot(
    *,
    overall: str,
    action: str,
    reasons: tuple[str, ...] = (),
    summary_rows: tuple[tuple[str, str, str], ...] = (),
    digest: str = "a" * 64,
    lifecycle_status: VMHALifecycleStatus = VMHALifecycleStatus.ACTIVE,
    authority_owner_node_id: str = "node-0",
    authority_observation_digest: str = "",
    member_compute_states: tuple[tuple[str, str], ...] = (
        ("node-0", "running"),
        ("node-1", "running"),
    ),
) -> _VMHAStatusSnapshot:
    authority = _VMHACloudAuthority(
        lifecycle=lifecycle_status.value,
        condition="exact",
        owner_name="gateway-0",
        owner_node_id=authority_owner_node_id,
        operation_id=None,
        reasons=(),
        observation_digest=authority_observation_digest,
        member_compute_states=member_compute_states,
    )
    members = (
        _VMHAMemberEvidence(
            name="gateway-0",
            configured_role="active",
            node_id="node-0",
            condition="exact",
            reason="",
            record={},
        ),
        _VMHAMemberEvidence(
            name="gateway-1",
            configured_role="passive",
            node_id="node-1",
            condition="exact",
            reason="",
            record={},
        ),
    )
    return _VMHAStatusSnapshot(
        view=_VMHAStatusView(
            overall=overall,
            summary_rows=summary_rows,
            member_rows=(),
            action=action,
            reasons=reasons,
        ),
        lifecycle_state=SimpleNamespace(status=lifecycle_status),
        authority=authority,
        members=members,
        authority_digest=digest,
    )


def _inspection(snapshot: _VMHAStatusSnapshot) -> _VMHACommandInspection:
    return _VMHACommandInspection(
        snapshot=snapshot,
        project_id="project-a",
        gateway_name="gateway",
    )


def _completed_recovery_statuses(
    state_dir: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    record = AutoHealingPolicyStore(state_dir).initialize(
        cluster_id="cluster-a",
        node_id="node-0",
        peer_node_id="node-1",
        generation_id="a" * 64,
        updated_at=1.0,
    )
    peer_record = AutoHealingPolicyRecord(
        **{
            **record.__dict__,
            "node_id": "node-1",
            "peer_node_id": "node-0",
            "phase": AutoHealingPolicyPhase.COMMITTED,
        }
    )
    recovery = AutoHealingRecoveryRecord(
        cluster_id=record.cluster_id,
        node_id=record.node_id,
        target_node_id=record.peer_node_id,
        generation_id=record.generation_id,
        desired=StandbyAutoHealing.ENABLED,
        operation_id=record.operation_id,
        approval_digest="d" * 64,
        policy_digest=record.decision_digest,
        predecessor_digest=record.predecessor_digest,
        promotion_receipt_id="receipt-a",
        allocation_id="allocation-a",
        ownership_epoch="7",
        stopped_revision="revision-a",
        phase=AutoHealingRecoveryPhase.COMPLETED,
        rearm_operation_id="rearm-a",
        updated_at=2.0,
    )
    owner_status: dict[str, object] = {
        "accepted_start": False,
        "cluster_id": record.cluster_id,
        "configured_role": "active",
        "decision_digest": record.decision_digest,
        "desired": record.desired.value,
        "generation_id": record.generation_id,
        "node_id": record.node_id,
        "operation_id": record.operation_id,
        "peer_agrees": False,
        "phase": record.phase.value,
        "record": record.to_dict(),
        "recovery": recovery.to_dict(),
        "recovery_authority": {
            "allocation_id": recovery.allocation_id,
            "ownership_epoch": "8",
            "promotion_receipt_id": "receipt-current",
        },
        "recovery_phase": recovery.phase.value,
        "schema": "nebius-vpngw/vm-ha-auto-healing-policy-status-v2",
    }
    peer_status: dict[str, object] = {
        **owner_status,
        "configured_role": "passive",
        "node_id": peer_record.node_id,
        "record": peer_record.to_dict(),
        "recovery": None,
        "recovery_authority": None,
        "recovery_phase": None,
    }
    return owner_status, peer_status


def _terminal_enabled_recovery_statuses(
    *,
    peer_agrees: bool,
    operation_id: str = "c" * 64,
    predecessor_digest: str = "b" * 64,
) -> tuple[dict[str, object], dict[str, object]]:
    decision_digest = policy_decision_digest(
        cluster_id="cluster-a",
        member_node_ids=("node-0", "node-1"),
        generation_id="a" * 64,
        desired=StandbyAutoHealing.ENABLED,
        operation_id=operation_id,
        coordinator_node_id="node-0",
        predecessor_digest=predecessor_digest,
    )
    owner_record = AutoHealingPolicyRecord(
        cluster_id="cluster-a",
        node_id="node-0",
        peer_node_id="node-1",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.ENABLED,
        operation_id=operation_id,
        coordinator_node_id="node-0",
        predecessor_digest=predecessor_digest,
        phase=AutoHealingPolicyPhase.COMMITTED,
        decision_digest=decision_digest,
        peer_ack_digest=decision_digest,
        updated_at=3.0,
    )
    peer_record = AutoHealingPolicyRecord(
        **{
            **owner_record.__dict__,
            "node_id": "node-1",
            "peer_node_id": "node-0",
        }
    )
    recovery = AutoHealingRecoveryRecord(
        cluster_id=owner_record.cluster_id,
        node_id=owner_record.node_id,
        target_node_id=owner_record.peer_node_id,
        generation_id=owner_record.generation_id,
        desired=StandbyAutoHealing.ENABLED,
        operation_id=owner_record.operation_id,
        approval_digest="d" * 64,
        policy_digest=owner_record.predecessor_digest,
        predecessor_digest=owner_record.predecessor_digest,
        promotion_receipt_id="receipt-a",
        allocation_id="allocation-a",
        ownership_epoch="7",
        stopped_revision="revision-a",
        phase=AutoHealingRecoveryPhase.COMPLETED,
        rearm_operation_id="rearm-a",
        updated_at=4.0,
    )

    def status(
        record: AutoHealingPolicyRecord,
        *,
        configured_role: str,
        completed_recovery: AutoHealingRecoveryRecord | None,
    ) -> dict[str, object]:
        return {
            "accepted_start": False,
            "cluster_id": record.cluster_id,
            "configured_role": configured_role,
            "decision_digest": record.decision_digest,
            "desired": record.desired.value,
            "generation_id": record.generation_id,
            "node_id": record.node_id,
            "operation_id": record.operation_id,
            "peer_agrees": peer_agrees,
            "phase": record.phase.value,
            "record": record.to_dict(),
            "recovery": (None if completed_recovery is None else completed_recovery.to_dict()),
            "recovery_authority": None,
            "recovery_phase": (
                None if completed_recovery is None else completed_recovery.phase.value
            ),
            "schema": "nebius-vpngw/vm-ha-auto-healing-policy-status-v2",
        }

    return (
        status(owner_record, configured_role="active", completed_recovery=recovery),
        status(peer_record, configured_role="passive", completed_recovery=None),
    )


def _terminal_enabled_dual_recovery_statuses(
    *,
    peer_agrees: bool,
) -> tuple[dict[str, object], dict[str, object]]:
    statuses = list(_terminal_enabled_recovery_statuses(peer_agrees=peer_agrees))
    for index, status in enumerate(statuses):
        record = AutoHealingPolicyRecord.from_mapping(status["record"])
        recovery = AutoHealingRecoveryRecord(
            cluster_id=record.cluster_id,
            node_id=record.node_id,
            target_node_id=record.peer_node_id,
            generation_id=record.generation_id,
            desired=StandbyAutoHealing.ENABLED,
            operation_id=record.operation_id,
            approval_digest=chr(ord("d") + index) * 64,
            policy_digest=record.decision_digest,
            predecessor_digest=record.predecessor_digest,
            promotion_receipt_id=f"receipt-{index}",
            allocation_id=f"allocation-{index}",
            ownership_epoch=str(index + 7),
            stopped_revision=f"revision-{index}",
            phase=AutoHealingRecoveryPhase.COMPLETED,
            rearm_operation_id=f"rearm-{index}",
            updated_at=4.0 + index,
        )
        status["recovery"] = recovery.to_dict()
        status["recovery_phase"] = recovery.phase.value
    return statuses[0], statuses[1]


def test_vm_ha_result_contract_keeps_json_detail_and_concise_healthy_text(
    tmp_path: Path,
) -> None:
    result = VMHACommandResult(
        outcome=VMHACommandOutcome.HEALTHY,
        classification=VMHACommandClassification.HEALTHY,
        health=VMHACommandHealth.HEALTHY,
        effective_config_file=tmp_path / "gateway.vm-ha.config.yaml",
        actions=("candidate-reused",),
    )

    payload = json.loads(result.to_json())

    assert payload == {
        "schema": "nebius-vpngw/vm-ha-result-v1",
        "outcome": "healthy",
        "classification": "healthy",
        "health": "healthy",
        "verification_scope": "passive-current-state-v1",
        "failover_tested": False,
        "effective_config_file": str(tmp_path / "gateway.vm-ha.config.yaml"),
        "actions": ["candidate-reused"],
        "reasons": [],
        "impact": None,
        "approval": None,
        "next_action": None,
    }
    assert result.exit_code == 0
    assert result.to_text() == "VM-HA is healthy now."


@pytest.mark.parametrize(
    ("outcome", "health", "action", "headline"),
    (
        (
            VMHACommandOutcome.HEALTHY,
            VMHACommandHealth.HEALTHY,
            "standby-auto-healing-already-enabled",
            "VM-HA standby auto-healing is already enabled.",
        ),
        (
            VMHACommandOutcome.MAINTENANCE,
            VMHACommandHealth.MAINTENANCE,
            "standby-auto-healing-already-disabled",
            "VM-HA standby auto-healing is already disabled.",
        ),
        (
            VMHACommandOutcome.HEALTHY,
            VMHACommandHealth.HEALTHY,
            "standby-auto-healing-enabled",
            "VM-HA standby auto-healing was enabled successfully.",
        ),
        (
            VMHACommandOutcome.MAINTENANCE,
            VMHACommandHealth.MAINTENANCE,
            "standby-auto-healing-disabled",
            "VM-HA standby auto-healing was disabled successfully.",
        ),
    ),
)
def test_vm_ha_result_text_names_the_requested_policy_outcome(
    tmp_path: Path,
    outcome: VMHACommandOutcome,
    health: VMHACommandHealth,
    action: str,
    headline: str,
) -> None:
    result = VMHACommandResult(
        outcome=outcome,
        classification=VMHACommandClassification.MAINTENANCE_POLICY,
        health=health,
        effective_config_file=tmp_path / "gateway.vm-ha.config.yaml",
        actions=("standby-auto-healing-recovery-cleared", action),
        reasons=("maintenance-ready",),
    )

    text = result.to_text()

    assert text.splitlines()[0] == headline
    assert "standby-auto-healing-recovery-cleared" not in text


@pytest.mark.parametrize(
    "action",
    (
        "standby-auto-healing-already-enabled",
        "standby-auto-healing-already-disabled",
        "standby-auto-healing-enabled",
        "standby-auto-healing-disabled",
    ),
)
@pytest.mark.parametrize(
    ("outcome", "health", "headline"),
    (
        (
            VMHACommandOutcome.BLOCKED,
            VMHACommandHealth.BLOCKED,
            "VM-HA convergence is blocked.",
        ),
        (
            VMHACommandOutcome.PLANNED,
            VMHACommandHealth.TRANSITIONING,
            "VM-HA plan is ready; no changes were made.",
        ),
        (
            VMHACommandOutcome.ACTION_REQUIRED,
            VMHACommandHealth.TRANSITIONING,
            "VM-HA needs operator action.",
        ),
    ),
)
def test_vm_ha_result_text_never_claims_policy_success_for_nonterminal_outcome(
    tmp_path: Path,
    action: str,
    outcome: VMHACommandOutcome,
    health: VMHACommandHealth,
    headline: str,
) -> None:
    result = VMHACommandResult(
        outcome=outcome,
        classification=VMHACommandClassification.MAINTENANCE_POLICY,
        health=health,
        effective_config_file=tmp_path / "gateway.vm-ha.config.yaml",
        actions=(action,),
        reasons=("standby-auto-healing-policy-change-planned",),
    )

    text = result.to_text()

    assert text.splitlines()[0] == headline
    assert "successfully" not in text
    assert "is already" not in text


@pytest.mark.parametrize(
    ("kind", "destructive", "traffic_interruption", "summary"),
    (
        (
            "artifact-standby-recovery",
            False,
            True,
            "May briefly interrupt VPN traffic while the serving owner is upgraded; "
            "no gateway VM or disk is deleted",
        ),
        (
            "failed-passive-replacement",
            True,
            False,
            "Deletes and recreates the failed standby VM and boot disk; "
            "VPN traffic is expected to remain available",
        ),
        (
            "active-standby-replacement",
            False,
            False,
            "Creates a fresh non-owner VM and boot disk and may rotate only its managed "
            "SSH identity; existing disks are left untouched and the serving owner is "
            "not restarted",
        ),
        (
            "unknown-plan-kind",
            None,
            None,
            "Impact is not classified; operator approval is required",
        ),
    ),
)
def test_vm_ha_apply_plan_impact_is_typed_and_fail_closed(
    kind: str,
    destructive: bool | None,
    traffic_interruption: bool | None,
    summary: str,
) -> None:
    impact = _vm_ha_apply_plan_impact(
        kind,
        has_destructive_changes=False,
    )

    assert impact.summary == summary
    assert impact.destructive is destructive
    assert impact.vpn_traffic_interruption is traffic_interruption
    assert impact.approval_required is True


def test_vm_ha_destructive_apply_impact_overrides_plan_kind() -> None:
    impact = _vm_ha_apply_plan_impact(
        "apply-convergence",
        has_destructive_changes=True,
    )

    assert impact.to_dict() == {
        "summary": "Deletes and recreates gateway VM resources and may interrupt VPN traffic",
        "destructive": True,
        "vpn_traffic_interruption": True,
        "resource_creation": True,
        "approval_required": True,
    }


def test_missing_standby_owner_upgrade_impact_is_explicit() -> None:
    impact = _vm_ha_apply_plan_impact(
        "active-standby-replacement",
        has_destructive_changes=False,
        owner_refresh_required=True,
    )

    assert impact.vpn_traffic_interruption is True
    assert impact.summary == (
        "Upgrades and restarts the serving-owner VM-HA control services, then creates a "
        "fresh non-owner VM and boot disk; existing disks are left untouched and VPN "
        "traffic may be briefly interrupted"
    )


@pytest.mark.parametrize("owner_refresh_required", (False, True))
def test_missing_standby_orchestration_inhibits_owner_before_create(
    monkeypatch: pytest.MonkeyPatch,
    owner_refresh_required: bool,
) -> None:
    trace: list[str] = []
    operation_id = "a" * 64
    transaction = SimpleNamespace(
        completed_effects=(),
        pending_effect=None,
        operation_id=operation_id,
    )

    class Journal:
        state = SimpleNamespace(transaction=transaction)

        def rewind_standby_replacement_inhibition_for_owner_refresh(
            self,
            *,
            owner_refresh_effect: str,
            inhibition_effect: str,
        ) -> None:
            trace.append(f"rewind:{inhibition_effect}:{owner_refresh_effect}")

        def begin(self, effect: str) -> None:
            trace.append(f"begin:{effect}")
            transaction.pending_effect = effect

        def complete(self, effect: str) -> None:
            assert transaction.pending_effect == effect
            trace.append(f"complete:{effect}")
            transaction.completed_effects = (*transaction.completed_effects, effect)
            transaction.pending_effect = None

    owner = SimpleNamespace(
        hostname="gateway-0",
        vm_ha_node=SimpleNamespace(node_id="node-0"),
        vm_ha_generation=SimpleNamespace(generation_id="b" * 64),
    )
    plan = SimpleNamespace(
        vm_ha=SimpleNamespace(cluster_id="cluster-1"),
        gateway_group=object(),
        gateway={"local_prefixes": ["10.0.0.0/8"]},
    )
    replacement = _VMHAMissingStandbyReplacementPlan(
        target_instance_name="gateway-1",
        owner_instance_name="gateway-0",
        approval_digest="c" * 64,
        operation_id=operation_id,
        replacement_cycle=1,
        replacement_disk_name="gateway-1-replacement-1",
        retired_compute_id="compute-old",
        retired_disk_id="disk-old",
        primary_allocation_id="primary-1",
        public_allocation_id="public-1",
        authorization_persisted=True,
    )
    inhibition = {
        "schema": "nebius-vpngw/vm-ha-standby-replacement-inhibition-v1",
        "operation_id": operation_id,
    }
    provisioning = SimpleNamespace(vm_ha_runtime_binding=object())

    class SSH:
        def ensure_vm_ha_agent_package(self, *_args, **_kwargs) -> None:
            trace.append("package-owner")

        def refresh_vm_ha_control_services(self, *_args, **_kwargs) -> None:
            trace.append("refresh-owner")

        def inhibit_vm_ha_standby_replacement(self, *_args, **_kwargs):
            trace.append("inhibit-owner")
            return inhibition

        def verify_vm_ha_standby_replacement_quiescent(self, *_args, **_kwargs) -> None:
            trace.append("verify-owner-inhibition")

        def install_vm_ha_apply_lock(self, *_args, **_kwargs) -> None:
            raise AssertionError("the serving owner must not receive an apply lock")

    class Manager:
        def observe_vm_ha_migration_state(self, *_args, **_kwargs):
            trace.append("revalidate-cloud")
            return {"fresh": True}

        def validate_missing_vm_ha_standby_replacement(self, *_args, **_kwargs) -> None:
            trace.append("validate-retained-authority")

        def replace_missing_vm_ha_standby(self, *_args, **_kwargs):
            trace.append("create-missing-target")
            return provisioning

    monkeypatch.setattr(
        cli_module,
        "_vm_ha_missing_standby_replacement_plan",
        lambda *_args, **_kwargs: trace.append("replan") or replacement,
    )

    observed = _create_missing_vm_ha_standby_under_owner_inhibition(
        plan=plan,
        planned_instances=(owner,),
        existing_members={"gateway-0": "203.0.113.10"},
        local_config={},
        apply_report=SimpleNamespace(
            artifact=object(),
            owner_refresh_required=owner_refresh_required,
        ),
        lifecycle_journal=Journal(),
        vm_manager=Manager(),
        ssh=SSH(),
        replacement=replacement,
    )

    assert observed == (inhibition, provisioning)
    initial_revalidation = [
        "revalidate-cloud",
        "replan",
        "validate-retained-authority",
    ]
    refresh_trace = (
        [
            "rewind:install-standby-replacement-inhibition-node-0:"
            "prepare-live-peer-replacement-owner-v5-node-0",
            "begin:prepare-live-peer-replacement-owner-v5-node-0",
            "package-owner",
            "refresh-owner",
            "complete:prepare-live-peer-replacement-owner-v5-node-0",
            *initial_revalidation,
        ]
        if owner_refresh_required
        else []
    )
    assert trace == [
        *initial_revalidation,
        *refresh_trace,
        "begin:install-standby-replacement-inhibition-node-0",
        "inhibit-owner",
        "verify-owner-inhibition",
        "complete:install-standby-replacement-inhibition-node-0",
        "create-missing-target",
    ]


def test_missing_standby_revalidates_authority_before_owner_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    operation_id = "a" * 64
    transaction = SimpleNamespace(
        completed_effects=(),
        pending_effect=None,
        operation_id=operation_id,
    )

    class Journal:
        state = SimpleNamespace(transaction=transaction)

        def begin(self, effect: str) -> None:
            trace.append(f"begin:{effect}")
            transaction.pending_effect = effect

        def complete(self, effect: str) -> None:
            trace.append(f"complete:{effect}")
            transaction.completed_effects = (*transaction.completed_effects, effect)
            transaction.pending_effect = None

    owner = SimpleNamespace(
        hostname="gateway-0",
        vm_ha_node=SimpleNamespace(node_id="node-0"),
        vm_ha_generation=SimpleNamespace(generation_id="b" * 64),
    )
    plan = SimpleNamespace(
        vm_ha=SimpleNamespace(cluster_id="cluster-1"),
        gateway_group=object(),
        gateway={"local_prefixes": ["10.0.0.0/8"]},
    )
    replacement = _VMHAMissingStandbyReplacementPlan(
        target_instance_name="gateway-1",
        owner_instance_name="gateway-0",
        approval_digest="c" * 64,
        operation_id=operation_id,
        replacement_cycle=1,
        replacement_disk_name="gateway-1-replacement-1",
        retired_compute_id="compute-old",
        retired_disk_id="disk-old",
        primary_allocation_id="primary-1",
        public_allocation_id="public-1",
        authorization_persisted=True,
    )

    class SSH:
        def ensure_vm_ha_agent_package(self, *_args, **_kwargs) -> None:
            trace.append("package-owner")

        def refresh_vm_ha_control_services(self, *_args, **_kwargs) -> None:
            trace.append("refresh-owner")

    class Manager:
        def observe_vm_ha_migration_state(self, *_args, **_kwargs):
            trace.append("revalidate-cloud")
            return {"fresh": True}

    monkeypatch.setattr(
        cli_module,
        "_vm_ha_missing_standby_replacement_plan",
        lambda *_args, **_kwargs: trace.append("replan") or object(),
    )

    with pytest.raises(
        RuntimeError,
        match="authority changed before inhibition",
    ):
        _create_missing_vm_ha_standby_under_owner_inhibition(
            plan=plan,
            planned_instances=(owner,),
            existing_members={"gateway-0": "203.0.113.10"},
            local_config={},
            apply_report=SimpleNamespace(
                artifact=object(),
                owner_refresh_required=True,
            ),
            lifecycle_journal=Journal(),
            vm_manager=Manager(),
            ssh=SSH(),
            replacement=replacement,
        )

    assert trace == ["revalidate-cloud", "replan"]


def test_missing_standby_release_resumes_after_remote_receipt_before_active_commit() -> None:
    trace: list[str] = []
    effect = "release-standby-replacement-inhibition-node-0"
    transaction = SimpleNamespace(completed_effects=(), pending_effect=None)

    class State:
        def __init__(self) -> None:
            self.transaction = transaction

        def with_status(self, status, *, checkpoint: str):
            trace.append(f"successor:{status.value}:{checkpoint}")
            return SimpleNamespace(status=status, checkpoint=checkpoint)

    class Journal:
        state = State()

        def begin(self, pending: str) -> None:
            trace.append(f"begin:{pending}")
            transaction.pending_effect = pending

        def complete(self, completed: str) -> None:
            assert transaction.pending_effect == completed
            trace.append(f"complete:{completed}")
            transaction.completed_effects = (*transaction.completed_effects, completed)
            transaction.pending_effect = None

        def transition(self, successor) -> None:
            trace.append(f"transition:{successor.status.value}")

    class SSH:
        attempts = 0

        def release_vm_ha_standby_replacement_inhibition(self, *_args, **_kwargs) -> None:
            self.attempts += 1
            trace.append(f"remote-release:{self.attempts}")
            if self.attempts == 1:
                raise RuntimeError("crash after durable remote release receipt")

    journal = Journal()
    ssh = SSH()
    kwargs = {
        "lifecycle_journal": journal,
        "ssh": ssh,
        "owner_target": "203.0.113.10",
        "owner_config": SimpleNamespace(hostname="gateway-0"),
        "local_config": {},
        "inhibition": {"operation_id": "a" * 64},
        "effect": effect,
    }

    with pytest.raises(RuntimeError, match="durable remote release receipt"):
        _release_missing_vm_ha_standby_inhibition(**kwargs)
    assert transaction.pending_effect == effect

    _release_missing_vm_ha_standby_inhibition(**kwargs)
    _commit_missing_vm_ha_standby_replacement_active(journal)

    assert trace == [
        f"begin:{effect}",
        "remote-release:1",
        "remote-release:2",
        f"complete:{effect}",
        "successor:active:missing-standby-replacement-complete",
        "transition:active",
    ]


def test_vm_ha_file_fingerprint_never_follows_symbolic_links(tmp_path: Path) -> None:
    target = tmp_path / "private.config.yaml"
    target.write_text("secret: private\n", encoding="utf-8")
    link = tmp_path / "candidate.config.yaml"
    link.symlink_to(target)

    with pytest.raises(OSError):
        _file_fingerprint(link)


def test_vm_ha_snapshot_digest_binds_lifecycle_record() -> None:
    snapshot = _snapshot(overall="HEALTHY", action="none")

    first = _vm_ha_snapshot_digest(
        snapshot.authority,
        snapshot.members,
        lifecycle_record_sha256="a" * 64,
    )
    second = _vm_ha_snapshot_digest(
        snapshot.authority,
        snapshot.members,
        lifecycle_record_sha256="b" * 64,
    )

    assert first != second

    unavailable_authority = _VMHACloudAuthority(
        **{
            **snapshot.authority.__dict__,
            "unavailable_member_node_ids": ("node-1",),
        }
    )
    assert first != _vm_ha_snapshot_digest(
        unavailable_authority,
        snapshot.members,
        lifecycle_record_sha256="a" * 64,
    )


def test_vm_ha_apply_planner_stops_at_typed_pre_mutation_boundary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("reconcile-managed-routes-through-apply-owner",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    observed: dict[str, object] = {}

    def fake_apply(**kwargs) -> None:
        observed.update(kwargs)
        raise _VMHAApplyPlanCaptured(report)

    monkeypatch.setattr("nebius_vpngw.cli._apply_impl", fake_apply)

    assert _plan_vm_ha_apply_convergence(config_path, region="eu-north1") == report
    assert observed["local_config_file"] == config_path
    assert observed["region"] == "eu-north1"
    assert observed["dry_run"] is True
    assert observed["stop_after_vm_ha_plan"] is True
    assert observed["approve_vm_ha_migration"] is None
    assert observed["recover_vm_ha_migration"] is None
    assert observed["replace_failed_vm_ha_passive"] is None


def test_vm_ha_apply_engine_rejects_a_changed_expected_plan() -> None:
    expected = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("typed-effect",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    changed = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="f" * 64,
        engine_digest="e" * 64,
        effects=("typed-effect",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )

    _validate_vm_ha_expected_apply_plan(expected, expected)
    with pytest.raises(RuntimeError, match="plan changed after approval"):
        _validate_vm_ha_expected_apply_plan(changed, expected)
    with pytest.raises(RuntimeError, match="plan changed after approval"):
        _validate_vm_ha_expected_apply_plan(None, expected)


def test_vm_ha_apply_planner_suppresses_raw_engine_output(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("typed-effect",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )

    def fake_apply(**_kwargs) -> None:
        print("TOP_SECRET_PROVIDER_DETAIL")
        raise _VMHAApplyPlanCaptured(report)

    monkeypatch.setattr("nebius_vpngw.cli._apply_impl", fake_apply)

    assert _plan_vm_ha_apply_convergence(tmp_path / "gateway.config.yaml") == report
    captured = capsys.readouterr()
    assert "TOP_SECRET_PROVIDER_DETAIL" not in captured.out
    assert "TOP_SECRET_PROVIDER_DETAIL" not in captured.err


def test_vm_ha_apply_planner_preserves_typed_preflight_action(
    monkeypatch,
    tmp_path: Path,
) -> None:
    failure = _VMHAApplyPlanningFailed(
        reason="replacement-ssh-identity-unavailable",
        next_action=(
            "restore the missing non-owner's original private SSH host key "
            "matching its exact pin, then rerun vm-ha"
        ),
    )

    def fail_preflight(**_kwargs) -> None:
        raise typer.Exit(code=1) from failure

    monkeypatch.setattr("nebius_vpngw.cli._apply_impl", fail_preflight)

    with pytest.raises(_VMHAApplyPlanningFailed) as raised:
        _plan_vm_ha_apply_convergence(tmp_path / "gateway.config.yaml")

    assert raised.value is failure


def test_vm_ha_apply_planner_never_infers_authentication_from_bare_exit(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fail_preflight(**_kwargs) -> None:
        raise typer.Exit(code=1) from ValueError("PRIVATE_PREFLIGHT_DETAIL")

    monkeypatch.setattr("nebius_vpngw.cli._apply_impl", fail_preflight)

    with pytest.raises(_VMHAApplyPlanningFailed) as raised:
        _plan_vm_ha_apply_convergence(tmp_path / "gateway.config.yaml")

    assert raised.value.reason == "apply-planning-prerequisite-unavailable"
    assert raised.value.classification is VMHACommandClassification.FAILED
    assert "PRIVATE_PREFLIGHT_DETAIL" not in str(raised.value)


@pytest.mark.parametrize(
    ("credential_reason", "public_reason"),
    (
        ("authentication-failed", "runtime-credential-authentication-failed"),
        ("file-unavailable", "runtime-credential-identity-invalid"),
    ),
)
def test_vm_ha_apply_planner_projects_runtime_credential_failure(
    monkeypatch,
    tmp_path: Path,
    credential_reason: str,
    public_reason: str,
) -> None:
    def fail_preflight(**_kwargs) -> None:
        raise typer.Exit(code=1) from VMHACredentialIdentityError(credential_reason)

    monkeypatch.setattr("nebius_vpngw.cli._apply_impl", fail_preflight)

    with pytest.raises(_VMHAApplyPlanningFailed) as raised:
        _plan_vm_ha_apply_convergence(tmp_path / "gateway.config.yaml")

    assert raised.value.reason == public_reason
    assert raised.value.classification is VMHACommandClassification.EXTERNAL_PREREQUISITE


@pytest.mark.parametrize(
    ("kind", "approval_field"),
    (
        ("migration", "approve_vm_ha_migration"),
        ("recovery", "recover_vm_ha_migration"),
        ("failed-passive-replacement", "replace_failed_vm_ha_passive"),
        ("active-standby-replacement", "replace_missing_vm_ha_standby"),
        ("resume-transaction", None),
        ("apply-convergence", None),
    ),
)
def test_vm_ha_apply_executor_maps_only_the_typed_engine_approval(
    monkeypatch,
    tmp_path: Path,
    kind: str,
    approval_field: str | None,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    report = _VMHAApplyPlanReport(
        kind=kind,
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("typed-effect",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        "nebius_vpngw.cli._apply_impl",
        lambda **kwargs: observed.update(kwargs),
    )

    _execute_vm_ha_apply_convergence(config_path, report, region="eu-north1")

    approval_fields = {
        "approve_vm_ha_migration",
        "recover_vm_ha_migration",
        "replace_failed_vm_ha_passive",
        "replace_missing_vm_ha_standby",
    }
    for field in approval_fields:
        assert observed[field] == ("e" * 64 if field == approval_field else None)
    assert observed["dry_run"] is False
    assert observed["recreate_gw"] is False
    assert observed["region"] == "eu-north1"
    assert observed["vm_ha_progress_sink"] is None
    assert observed["expected_vm_ha_plan"] == report


def test_vm_ha_apply_executor_suppresses_raw_engine_output(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("typed-effect",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )

    def fake_apply(**_kwargs) -> None:
        print("TOP_SECRET_PROVIDER_DETAIL")

    monkeypatch.setattr("nebius_vpngw.cli._apply_impl", fake_apply)

    _execute_vm_ha_apply_convergence(tmp_path / "gateway.config.yaml", report)
    captured = capsys.readouterr()
    assert "TOP_SECRET_PROVIDER_DETAIL" not in captured.out
    assert "TOP_SECRET_PROVIDER_DETAIL" not in captured.err


def test_vm_ha_apply_executor_streams_typed_progress_without_raw_output(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("typed-effect",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    events = []

    def fake_apply(**kwargs) -> None:
        print("TOP_SECRET_PROVIDER_DETAIL")
        sink = kwargs["vm_ha_progress_sink"]
        _emit_vm_ha_progress(
            sink,
            _VMHAProgressPhase.RELOAD_STANDBY_SERVICES,
            _VMHAProgressState.STARTED,
        )
        _emit_vm_ha_progress(
            sink,
            _VMHAProgressPhase.RELOAD_STANDBY_SERVICES,
            _VMHAProgressState.COMPLETED,
        )

    monkeypatch.setattr("nebius_vpngw.cli._apply_impl", fake_apply)

    _execute_vm_ha_apply_convergence(
        tmp_path / "gateway.config.yaml",
        report,
        progress_sink=events.append,
    )

    assert [(event.phase, event.state) for event in events] == [
        (_VMHAProgressPhase.EXECUTE_APPLY, _VMHAProgressState.STARTED),
        (
            _VMHAProgressPhase.RELOAD_STANDBY_SERVICES,
            _VMHAProgressState.STARTED,
        ),
        (
            _VMHAProgressPhase.RELOAD_STANDBY_SERVICES,
            _VMHAProgressState.COMPLETED,
        ),
        (_VMHAProgressPhase.EXECUTE_APPLY, _VMHAProgressState.COMPLETED),
    ]
    captured = capsys.readouterr()
    assert "TOP_SECRET_PROVIDER_DETAIL" not in captured.out
    assert "TOP_SECRET_PROVIDER_DETAIL" not in captured.err


def test_artifact_recovery_topology_requires_stale_running_owner_and_stopped_standby() -> None:
    snapshot = _snapshot(
        overall="BLOCKED",
        action="reconcile-generation",
        reasons=("agent-status-stale",),
        member_compute_states=(("node-0", "running"), ("node-1", "stopped")),
    )
    snapshot = _VMHAStatusSnapshot(
        view=snapshot.view,
        lifecycle_state=SimpleNamespace(
            status=VMHALifecycleStatus.ACTIVE,
            transaction=SimpleNamespace(pending_effect=None),
        ),
        authority=snapshot.authority,
        members=(
            _VMHAMemberEvidence(
                name="gateway-0",
                configured_role="active",
                node_id="node-0",
                condition="blocked",
                reason="agent-status-stale",
            ),
            _VMHAMemberEvidence(
                name="gateway-1",
                configured_role="passive",
                node_id="node-1",
                condition="unknown",
                reason="agent-status-unavailable",
            ),
        ),
        authority_digest=snapshot.authority_digest,
    )

    topology = _vm_ha_artifact_recovery_topology(snapshot)

    assert topology is not None
    assert [member.node_id for member in topology] == ["node-0", "node-1"]
    drifted = _VMHAStatusSnapshot(
        view=snapshot.view,
        lifecycle_state=snapshot.lifecycle_state,
        authority=_VMHACloudAuthority(**{**snapshot.authority.__dict__, "condition": "blocked"}),
        members=snapshot.members,
        authority_digest=snapshot.authority_digest,
    )
    assert _vm_ha_artifact_recovery_topology(drifted) is None


def test_artifact_recovery_admits_only_exact_pre_v2_policy_deadlocks() -> None:
    owner = {
        "state": "active",
        "promotion_ready": True,
        "promotion_committed": True,
        "data_plane_mode": "active",
        "observed_owner_node_id": "node-0",
        "apply_locked": False,
        "apply_operation_id": None,
        "pending_operation_id": None,
        "repair": None,
        "transfer_inhibition_operation_id": None,
        "rearm_phase": "inhibited",
        "rearm_reason": "standby-auto-healing-peer-policy-unavailable",
        "auto_healing": {
            "state": "blocked",
            "peer_agrees": False,
            "accepted_start": False,
        },
        "mtls": {"state": "healthy", "operation_id": None, "inhibited": False},
    }

    assert _vm_ha_artifact_recovery_owner_is_safe(
        owner,
        owner_node_id="node-0",
    )
    owner["rearm_reason"] = "standby restoration policy authority changed"
    owner["auto_healing"]["state"] = "transitioning"
    assert _vm_ha_artifact_recovery_owner_is_safe(
        owner,
        owner_node_id="node-0",
    )
    owner["auto_healing"]["state"] = "blocked"
    assert not _vm_ha_artifact_recovery_owner_is_safe(
        owner,
        owner_node_id="node-0",
    )
    owner["rearm_reason"] = "standby-auto-healing-policy-disabled"
    assert not _vm_ha_artifact_recovery_owner_is_safe(
        owner,
        owner_node_id="node-0",
    )


def test_artifact_recovery_admits_only_writer_owned_restoration_progress() -> None:
    owner = {
        "state": "active",
        "promotion_ready": True,
        "promotion_committed": True,
        "data_plane_mode": "active",
        "observed_owner_node_id": "node-0",
        "apply_locked": False,
        "apply_operation_id": None,
        "pending_operation_id": None,
        "repair": None,
        "transfer_inhibition_operation_id": None,
        "rearm_phase": "starting",
        "rearm_reason": None,
        "auto_healing": {
            "state": "transitioning",
            "peer_agrees": False,
            "accepted_start": True,
        },
        "mtls": {"state": "healthy", "operation_id": None, "inhibited": False},
    }

    assert _vm_ha_artifact_recovery_owner_is_safe(
        owner,
        owner_node_id="node-0",
        allow_rearm_progress=True,
    )
    owner["auto_healing"]["accepted_start"] = False
    assert not _vm_ha_artifact_recovery_owner_is_safe(
        owner,
        owner_node_id="node-0",
        allow_rearm_progress=True,
    )


def test_artifact_recovery_upgrades_owner_before_rearm_and_reuses_artifact(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    wheel = tmp_path / "nebius_vpngw-1.2.3-py3-none-any.whl"
    _write_vm_ha_agent_wheel(wheel)
    artifact = VMHAAgentArtifact.from_wheel(wheel, source="test")
    report = _VMHAApplyPlanReport(
        kind="artifact-standby-recovery",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("typed-recovery",),
        has_destructive_changes=False,
        managed_ssh_action=None,
        artifact_sha256=artifact.sha256,
        artifact=artifact,
    )
    owner = SimpleNamespace(
        hostname="gateway-0",
        vm_ha_node=SimpleNamespace(node_id="node-0"),
    )
    standby = SimpleNamespace(
        hostname="gateway-1",
        vm_ha_node=SimpleNamespace(node_id="node-1"),
    )
    context = _VMHAArtifactRecoveryContext(
        local_config={"gateway_group": {"vm_spec": {}}},
        plan=SimpleNamespace(),  # type: ignore[arg-type]
        lifecycle=SimpleNamespace(),  # type: ignore[arg-type]
        owner_instance=owner,
        standby_instance=standby,
        owner_role="active",
        standby_role="passive",
        owner_target="owner-target",
        standby_target="standby-target",
        ssh_policy=object(),  # type: ignore[arg-type]
        owner_record={},
    )
    canonical = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="c" * 64,
        engine_digest="b" * 64,
        effects=("canonical-apply",),
        has_destructive_changes=False,
        managed_ssh_action=None,
        artifact_sha256=artifact.sha256,
        artifact=artifact,
    )
    order: list[str] = []

    class Push:
        def __init__(self, *, ssh_policy) -> None:
            assert ssh_policy is context.ssh_policy

        def ensure_vm_ha_agent_package(self, target, *_args, artifact=None, **_kwargs) -> None:
            assert artifact is report.artifact
            order.append(f"install:{target}")

        def refresh_vm_ha_control_services(self, target, *_args, **_kwargs) -> None:
            order.append(f"refresh:{target}")

    safe_owner = {
        "state": "active",
        "promotion_ready": True,
        "promotion_committed": True,
        "data_plane_mode": "active",
        "observed_owner_node_id": "node-0",
        "apply_locked": False,
        "apply_operation_id": None,
        "pending_operation_id": None,
        "repair": None,
        "transfer_inhibition_operation_id": None,
        "rearm_phase": "starting",
        "rearm_reason": None,
        "auto_healing": {
            "state": "transitioning",
            "peer_agrees": False,
            "accepted_start": True,
        },
        "mtls": {"state": "healthy", "operation_id": None, "inhibited": False},
        "controller_capabilities": ["vm-ha-standby-restoration-v2"],
    }

    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_status_with_region", lambda *_a, **_k: object()
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_artifact_standby_recovery",
        lambda *_a, **_k: (report, context),
    )
    monkeypatch.setattr("nebius_vpngw.cli.SSHPush", Push)
    monkeypatch.setattr("nebius_vpngw.cli._vm_ha_status_runtime_binding", lambda *_a: object())
    monkeypatch.setattr(
        "nebius_vpngw.cli._validate_vm_ha_planned_status",
        lambda payload, **_kwargs: payload,
    )

    def wait_for_owner(*, predicate, **_kwargs):
        order.append("reprove-owner")
        assert predicate(safe_owner)
        return safe_owner

    monkeypatch.setattr("nebius_vpngw.cli._wait_for_vm_ha_agent_status", wait_for_owner)

    def prepare(**_kwargs):
        order.append("owner-side-rearm")
        return SimpleNamespace(outcome="standby-ssh-ready")

    monkeypatch.setattr("nebius_vpngw.cli._prepare_vm_ha_planned_target", prepare)

    def plan(*_args, **_kwargs):
        order.append("canonical-plan")
        return canonical

    monkeypatch.setattr("nebius_vpngw.cli._plan_vm_ha_apply_convergence", plan)
    monkeypatch.setattr(
        "nebius_vpngw.cli._execute_vm_ha_apply_convergence",
        lambda *_a, **_k: order.append("canonical-apply"),
    )

    _execute_vm_ha_artifact_standby_recovery(
        config_path,
        report,
        region=None,
        progress_sink=None,
    )

    assert order == [
        "install:owner-target",
        "refresh:owner-target",
        "reprove-owner",
        "owner-side-rearm",
        "install:standby-target",
        "refresh:standby-target",
        "canonical-plan",
        "canonical-apply",
    ]


def test_vm_ha_apply_executor_never_completes_a_failed_phase(
    monkeypatch,
    tmp_path: Path,
) -> None:
    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("typed-effect",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    events = []
    monkeypatch.setattr(
        "nebius_vpngw.cli._apply_impl",
        Mock(side_effect=RuntimeError("TOP_SECRET_PROVIDER_DETAIL")),
    )

    with pytest.raises(
        _VMHAApplyConvergenceFailed,
        match="stopped after execution began",
    ) as raised:
        _execute_vm_ha_apply_convergence(
            tmp_path / "gateway.config.yaml",
            report,
            progress_sink=events.append,
        )
    assert "TOP_SECRET_PROVIDER_DETAIL" not in str(raised.value)

    assert [(event.phase, event.state) for event in events] == [
        (_VMHAProgressPhase.EXECUTE_APPLY, _VMHAProgressState.STARTED),
        (_VMHAProgressPhase.EXECUTE_APPLY, _VMHAProgressState.FAILED),
    ]


@pytest.mark.parametrize(
    "failure_type",
    (_VMHAActivationFailed, _VMHAActivationUnsafe),
)
def test_vm_ha_apply_executor_projects_typed_activation_exit_as_convergence_failure(
    monkeypatch,
    tmp_path: Path,
    failure_type: type[RuntimeError],
) -> None:
    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("typed-effect",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )

    def fail_activation(**_kwargs) -> None:
        error = failure_type("sanitized activation failure")
        raise typer.Exit(code=1) from error

    monkeypatch.setattr("nebius_vpngw.cli._apply_impl", fail_activation)

    with pytest.raises(RuntimeError, match="sanitized activation failure"):
        _execute_vm_ha_apply_convergence(tmp_path / "gateway.config.yaml", report)


def test_vm_ha_apply_executor_preserves_artifact_failure_from_apply_exit(
    monkeypatch,
    tmp_path: Path,
) -> None:
    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("typed-effect",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    artifact_error = VMHAAgentArtifactError(
        VMHAAgentArtifactProblem.CHANGED,
        "PRIVATE_ARTIFACT_DETAIL",
    )

    def fail_package(**_kwargs) -> None:
        raise typer.Exit(code=1) from artifact_error

    monkeypatch.setattr("nebius_vpngw.cli._apply_impl", fail_package)

    with pytest.raises(VMHAAgentArtifactError) as raised:
        _execute_vm_ha_apply_convergence(tmp_path / "gateway.config.yaml", report)
    assert raised.value is artifact_error


def test_vm_ha_apply_executor_projects_untyped_apply_exit_as_convergence_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("typed-effect",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._apply_impl",
        Mock(side_effect=typer.Exit(code=1)),
    )

    with pytest.raises(
        _VMHAApplyConvergenceFailed,
        match="stopped after execution began",
    ):
        _execute_vm_ha_apply_convergence(tmp_path / "gateway.config.yaml", report)


@pytest.mark.parametrize(
    "failure",
    (
        typer.Exit(code=1),
        RuntimeError("PRIVATE_RECOVERY_DETAIL"),
        subprocess.TimeoutExpired(
            cmd=("ssh", "PRIVATE_COMMAND_DETAIL"),
            timeout=5,
            output="PRIVATE_OUTPUT_DETAIL",
        ),
        paramiko.SSHException("PRIVATE_SSH_DETAIL"),
    ),
)
def test_vm_ha_artifact_recovery_executor_projects_post_owner_failure(
    monkeypatch,
    tmp_path: Path,
    failure: BaseException,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = _VMHAApplyPlanReport(
        kind="artifact-standby-recovery",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("typed-recovery",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )

    def fail_recovery(*_args, **_kwargs) -> None:
        print("RAW_ARTIFACT_RECOVERY_STDOUT")
        print("RAW_ARTIFACT_RECOVERY_STDERR", file=sys.stderr)
        raise failure

    monkeypatch.setattr(
        "nebius_vpngw.cli._execute_vm_ha_artifact_standby_recovery",
        fail_recovery,
    )

    with pytest.raises(
        _VMHAApplyConvergenceFailed,
        match="stopped after execution began",
    ) as raised:
        _execute_vm_ha_apply_convergence(tmp_path / "gateway.config.yaml", report)
    assert "PRIVATE_RECOVERY_DETAIL" not in str(raised.value)
    assert "PRIVATE_COMMAND_DETAIL" not in str(raised.value)
    assert "PRIVATE_OUTPUT_DETAIL" not in str(raised.value)
    assert "PRIVATE_SSH_DETAIL" not in str(raised.value)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_vm_ha_artifact_recovery_executor_preserves_typed_artifact_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    report = _VMHAApplyPlanReport(
        kind="artifact-standby-recovery",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("typed-recovery",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    artifact_error = VMHAAgentArtifactError(
        VMHAAgentArtifactProblem.CHANGED,
        "PRIVATE_ARTIFACT_DETAIL",
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._execute_vm_ha_artifact_standby_recovery",
        Mock(side_effect=artifact_error),
    )

    with pytest.raises(VMHAAgentArtifactError) as raised:
        _execute_vm_ha_apply_convergence(tmp_path / "gateway.config.yaml", report)
    assert raised.value is artifact_error


class _TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class _FakeStatus:
    def __init__(self, events: list[tuple[str, str]], label: str) -> None:
        self._events = events
        self._label = label

    def start(self) -> None:
        self._events.append(("start", self._label))

    def update(self, label: str) -> None:
        self._label = label
        self._events.append(("update", label))

    def stop(self) -> None:
        self._events.append(("stop", self._label))


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def test_vm_ha_status_factory_uses_rich_dots_spinner() -> None:
    status = _vm_ha_status_spinner(_TTYBuffer(), "working.")

    assert status.status == "working."
    assert status.renderable.name == "dots"


def test_vm_ha_status_spinner_does_not_proxy_process_streams() -> None:
    stream = _TTYBuffer()
    stdout_before = sys.stdout
    stderr_before = sys.stderr
    status = _vm_ha_status_spinner(stream, "working.")

    status.start()
    try:
        assert sys.stdout is stdout_before
        assert sys.stderr is stderr_before
    finally:
        status.stop()


def test_vm_ha_interactive_conversion_stops_resolve_spinner_before_prompt(
    monkeypatch,
    sample_config: dict,
    tmp_path: Path,
) -> None:
    source = tmp_path / "gateway.config.yaml"
    original = yaml.safe_dump(sample_config, sort_keys=False)
    source.write_text(original, encoding="utf-8")
    destination = tmp_path / "gateway.vm-ha.config.yaml"
    status_events: list[tuple[str, str]] = []
    reporter = _VMHAProgressReporter(
        _TTYBuffer(),
        status_factory=lambda _stream, label: _FakeStatus(status_events, label),
    )
    reserve = Mock(side_effect=AssertionError("prompt boundary performed a cloud effect"))

    def wizard(*_args, **_kwargs) -> None:
        assert reporter._active == []
        assert reporter._status is None
        assert [event[0] for event in status_events] == ["start", "stop"]
        raise cli_module.WizardCancelled

    monkeypatch.setattr("nebius_vpngw.cli._vm_ha_progress_sink", lambda _stream: reporter)
    monkeypatch.setattr("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", lambda: True)
    monkeypatch.setattr("nebius_vpngw.cli.run_vm_ha_conversion_wizard", wizard)
    monkeypatch.setattr("nebius_vpngw.cli._reserve_vm_ha_passive_public_ip", reserve)

    result = CliRunner().invoke(app, ["vm-ha", "-c", str(source)])

    assert result.exit_code == 3, result.output
    assert source.read_text(encoding="utf-8") == original
    assert not destination.exists()
    reserve.assert_not_called()
    assert [event[0] for event in status_events] == ["start", "stop"]


def test_vm_ha_interactive_conversion_interrupt_has_no_dangling_progress_or_effects(
    monkeypatch,
    sample_config: dict,
    tmp_path: Path,
) -> None:
    source = tmp_path / "gateway.config.yaml"
    original = yaml.safe_dump(sample_config, sort_keys=False)
    source.write_text(original, encoding="utf-8")
    destination = tmp_path / "gateway.vm-ha.config.yaml"
    progress_stream = _TTYBuffer()
    status_events: list[tuple[str, str]] = []
    reporter = _VMHAProgressReporter(
        progress_stream,
        status_factory=lambda _stream, label: _FakeStatus(status_events, label),
    )
    reserve = Mock(side_effect=AssertionError("interruption performed a cloud effect"))

    def wizard(*_args, **_kwargs) -> None:
        assert reporter._active == []
        assert reporter._status is None
        raise cli_module.WizardInterrupted

    monkeypatch.setattr("nebius_vpngw.cli._vm_ha_progress_sink", lambda _stream: reporter)
    monkeypatch.setattr(
        "nebius_vpngw.cli._ordinary_vm_ha_conversion_trust_prerequisite",
        lambda _path, _source: None,
    )
    monkeypatch.setattr("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", lambda: True)
    monkeypatch.setattr("nebius_vpngw.cli.run_vm_ha_conversion_wizard", wizard)
    monkeypatch.setattr("nebius_vpngw.cli._reserve_vm_ha_passive_public_ip", reserve)

    result = CliRunner().invoke(app, ["vm-ha", "-c", str(source)])

    assert result.exit_code == 130, result.output
    assert source.read_text(encoding="utf-8") == original
    assert not destination.exists()
    reserve.assert_not_called()
    assert reporter._active == []
    assert reporter._status is None
    assert [event[0] for event in status_events] == ["start", "stop"]
    assert "\x1b[31m" not in progress_stream.getvalue()


def test_vm_ha_progress_noninteractive_emits_one_terminal_row_per_phase() -> None:
    stream = io.StringIO()
    reporter = _VMHAProgressReporter(stream)
    phase = _VMHAProgressPhase.RELOAD_STANDBY_SERVICES

    reporter(_VMHAProgressEvent(phase, _VMHAProgressState.STARTED))
    reporter(_VMHAProgressEvent(phase, _VMHAProgressState.WAITING, 5.0))
    reporter(_VMHAProgressEvent(phase, _VMHAProgressState.COMPLETED))

    assert stream.getvalue() == (
        "✓ applying configuration and restarting VM-HA control services on the non-owner.\n"
    )


@pytest.mark.parametrize(
    ("phase", "expected"),
    (
        (
            _VMHAProgressPhase.EXECUTE_APPLY,
            "✓ approved VM-HA transaction completed.\n",
        ),
        (
            _VMHAProgressPhase.WAIT_BOOTSTRAP,
            "✓ both VM-HA members are ready for configuration.\n",
        ),
        (
            _VMHAProgressPhase.PREPARE_AGENT_PACKAGES,
            "✓ exact VM-HA agent packages are ready.\n",
        ),
    ),
)
def test_vm_ha_progress_uses_concise_completed_state_labels(
    phase: _VMHAProgressPhase,
    expected: str,
) -> None:
    stream = io.StringIO()
    reporter = _VMHAProgressReporter(stream)

    reporter(_VMHAProgressEvent(phase, _VMHAProgressState.STARTED))
    reporter(_VMHAProgressEvent(phase, _VMHAProgressState.COMPLETED))

    assert stream.getvalue() == expected


def test_vm_ha_progress_failure_rewrites_one_fully_red_terminal_row() -> None:
    stream = _TTYBuffer()
    status_events: list[tuple[str, str]] = []
    reporter = _VMHAProgressReporter(
        stream,
        status_factory=lambda _stream, label: _FakeStatus(status_events, label),
    )
    phase = _VMHAProgressPhase.RELOAD_STANDBY_SERVICES

    reporter(_VMHAProgressEvent(phase, _VMHAProgressState.STARTED))
    reporter(_VMHAProgressEvent(phase, _VMHAProgressState.WAITING, 5.0))
    reporter(_VMHAProgressEvent(phase, _VMHAProgressState.FAILED))

    output = stream.getvalue()
    expected = "✗ applying configuration and restarting VM-HA control services on the non-owner."
    assert output.count("\n") == 1
    assert f"\x1b[31m{expected}\x1b[0m\n" in output
    assert status_events[0] == (
        "start",
        "applying configuration and restarting VM-HA control services on the non-owner.",
    )
    assert status_events[-1][0] == "stop"
    assert "…" not in output
    assert "Starting:" not in output
    assert "Failed:" not in output


def test_vm_ha_progress_success_rewrites_one_fully_green_terminal_row() -> None:
    stream = _TTYBuffer()
    status_events: list[tuple[str, str]] = []
    reporter = _VMHAProgressReporter(
        stream,
        status_factory=lambda _stream, label: _FakeStatus(status_events, label),
    )
    phase = _VMHAProgressPhase.REVALIDATE_APPROVAL

    reporter(_VMHAProgressEvent(phase, _VMHAProgressState.STARTED))
    reporter(_VMHAProgressEvent(phase, _VMHAProgressState.COMPLETED))

    output = stream.getvalue()
    expected = "✓ revalidating the exact approved plan."
    assert output.count("\n") == 1
    assert f"\x1b[32m{expected}\x1b[0m\n" in output
    assert [event[0] for event in status_events] == ["start", "stop"]
    assert "…" not in output


def test_vm_ha_progress_resumes_outer_spinner_after_nested_phase() -> None:
    stream = _TTYBuffer()
    status_events: list[tuple[str, str]] = []
    reporter = _VMHAProgressReporter(
        stream,
        status_factory=lambda _stream, label: _FakeStatus(status_events, label),
    )
    outer = _VMHAProgressPhase.EXECUTE_APPLY
    inner = _VMHAProgressPhase.RELOAD_STANDBY_SERVICES

    reporter(_VMHAProgressEvent(outer, _VMHAProgressState.STARTED))
    reporter(_VMHAProgressEvent(inner, _VMHAProgressState.STARTED))
    reporter(_VMHAProgressEvent(inner, _VMHAProgressState.WAITING, 5.0))
    reporter(_VMHAProgressEvent(inner, _VMHAProgressState.COMPLETED))
    reporter(_VMHAProgressEvent(outer, _VMHAProgressState.COMPLETED))

    assert [event[0] for event in status_events] == [
        "start",
        "stop",
        "start",
        "update",
        "stop",
        "start",
        "stop",
    ]
    assert status_events[3][1].endswith("(5s elapsed).")
    assert status_events[5] == (
        "start",
        "applying the approved VM-HA transaction.",
    )
    output = stream.getvalue()
    assert output.count("✓ applying configuration") == 1
    assert output.count("✓ approved VM-HA transaction completed") == 1
    assert "…" not in output


def test_vm_ha_apply_capture_survives_nested_spinner_transitions(
    monkeypatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("typed-effect",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    stream = _TTYBuffer()
    reporter = _VMHAProgressReporter(stream)

    def fake_apply(**kwargs) -> None:
        sink = kwargs["vm_ha_progress_sink"]
        cli_module.print("Analyzing configuration changes...")
        print("gateway-0: No infrastructure changes")
        _emit_vm_ha_progress(
            sink,
            _VMHAProgressPhase.WAIT_BOOTSTRAP,
            _VMHAProgressState.STARTED,
        )
        print("gateway-0: VM ready")
        print("gateway-1: VM ready")
        _emit_vm_ha_progress(
            sink,
            _VMHAProgressPhase.WAIT_BOOTSTRAP,
            _VMHAProgressState.COMPLETED,
        )
        _emit_vm_ha_progress(
            sink,
            _VMHAProgressPhase.PREPARE_AGENT_PACKAGES,
            _VMHAProgressState.STARTED,
        )
        print("Prepared gateway-0 agent package")
        print("Prepared gateway-1 agent package")
        _emit_vm_ha_progress(
            sink,
            _VMHAProgressPhase.PREPARE_AGENT_PACKAGES,
            _VMHAProgressState.COMPLETED,
        )
        print("[VMManager] routine detail")
        print("[SSHPush] routine detail")

    monkeypatch.setattr("nebius_vpngw.cli._apply_impl", fake_apply)

    _execute_vm_ha_apply_convergence(
        tmp_path / "gateway.config.yaml",
        report,
        progress_sink=reporter,
    )
    reporter.close_unfinished()

    output = stream.getvalue()
    process_output = capsys.readouterr()
    escaped_output = process_output.out + process_output.err
    assert "Analyzing configuration changes" not in output
    assert "No infrastructure changes" not in output
    assert "VM ready" not in output
    assert "Prepared gateway" not in output
    assert "[VMManager]" not in output
    assert "[SSHPush]" not in output
    assert "Analyzing configuration changes" not in escaped_output
    assert "No infrastructure changes" not in escaped_output
    assert "VM ready" not in escaped_output
    assert "Prepared gateway" not in escaped_output
    assert "[VMManager]" not in escaped_output
    assert "[SSHPush]" not in escaped_output
    assert output.count("✓ both VM-HA members are ready for configuration.") == 1
    assert output.count("✓ exact VM-HA agent packages are ready.") == 1
    assert output.count("✓ approved VM-HA transaction completed.") == 1


@pytest.mark.parametrize(
    "retry_message",
    [
        "request attempt 1 for Request failed but will be retried",
        "The SDK request attempt 1 failed. The SDK will retry the request.",
    ],
)
def test_vm_ha_progress_suppresses_only_sdk_retry_diagnostics_and_restores_filter(
    retry_message: str,
) -> None:
    stream = io.StringIO()
    reporter = _VMHAProgressReporter(stream)
    logger = logging.getLogger("nebius.aio.request")
    filters_before = tuple(logger.filters)
    level_before = logger.level
    handler = _ListHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.ERROR)
    phase = _VMHAProgressPhase.CONFIRM_HEALTH
    try:
        reporter(_VMHAProgressEvent(phase, _VMHAProgressState.STARTED))
        logger.error(retry_message)
        logger.error("terminal provider diagnostic")
        reporter(_VMHAProgressEvent(phase, _VMHAProgressState.COMPLETED))
        assert tuple(logger.filters) == filters_before

        logger.error(retry_message)
    finally:
        reporter.close_unfinished()
        logger.removeHandler(handler)
        logger.setLevel(level_before)

    assert handler.messages == ["terminal provider diagnostic", retry_message]


def test_vm_ha_progress_closes_each_unfinished_nested_phase_once() -> None:
    stream = io.StringIO()
    reporter = _VMHAProgressReporter(stream)
    reporter(
        _VMHAProgressEvent(
            _VMHAProgressPhase.EXECUTE_APPLY,
            _VMHAProgressState.STARTED,
        )
    )
    reporter(
        _VMHAProgressEvent(
            _VMHAProgressPhase.RELOAD_STANDBY_SERVICES,
            _VMHAProgressState.STARTED,
        )
    )

    reporter.close_unfinished()

    assert stream.getvalue().splitlines() == [
        "✗ applying configuration and restarting VM-HA control services on the non-owner.",
        "✗ applying the approved VM-HA transaction.",
    ]


def test_vm_ha_progress_render_failure_never_interrupts_an_effect(
    monkeypatch,
    tmp_path: Path,
) -> None:
    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("typed-effect",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    effect = Mock()
    events = []

    def broken_sink(event) -> None:
        events.append(event)
        if event.state is _VMHAProgressState.COMPLETED:
            raise BrokenPipeError("stderr closed")

    monkeypatch.setattr("nebius_vpngw.cli._apply_impl", effect)

    _execute_vm_ha_apply_convergence(
        tmp_path / "gateway.config.yaml",
        report,
        progress_sink=broken_sink,
    )

    effect.assert_called_once()
    assert [event.state for event in events] == [
        _VMHAProgressState.STARTED,
        _VMHAProgressState.COMPLETED,
    ]


def test_vm_ha_json_noninteractive_ordinary_input_requires_conversion_input(
    monkeypatch,
    sample_config: dict,
    tmp_path: Path,
) -> None:
    source = tmp_path / "gateway.config.yaml"
    source.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(
        "nebius_vpngw.cli._ordinary_vm_ha_conversion_trust_prerequisite",
        lambda _path, _source: None,
    )

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(source),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["classification"] == "conversion-required"
    assert payload["reasons"] == ["conversion-input-required"]
    assert payload["actions"] == []
    assert payload["failover_tested"] is False
    assert not (tmp_path / "gateway.vm-ha.config.yaml").exists()


def test_vm_ha_rejects_invalid_standby_auto_healing_before_authentication(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.config.yaml"
    config_path.write_text("project_id: project-test\n", encoding="utf-8")
    authenticate = Mock(side_effect=AssertionError("invalid option reached authentication"))
    monkeypatch.setattr("nebius_vpngw.cli._ensure_authentication", authenticate)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--standby-auto-healing",
            "sometimes",
        ],
    )

    assert result.exit_code == 2
    assert "Invalid value for '--standby-auto-healing'" in result.output
    authenticate.assert_not_called()


def test_vm_ha_standby_auto_healing_dry_run_emits_exact_plan_without_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none"))
    statuses = [{"record": {"node_id": "node-a"}}, {"record": {"node_id": "node-b"}}]
    transaction = _VMHAAutoHealingTransaction(
        operation_id="b" * 64,
        coordinator_node_id="node-a",
        predecessor_digest="c" * 64,
        member_node_ids=("node-a", "node-b"),
    )
    lock = Mock(side_effect=AssertionError("dry-run acquired the writer lock"))
    effect = Mock(side_effect=AssertionError("dry-run changed the policy"))

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: healthy,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_statuses",
        lambda **_kwargs: statuses,
    )
    monkeypatch.setattr("nebius_vpngw.cli._vm_ha_auto_healing_is_terminal", lambda *_a: False)
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_auto_healing_transaction",
        lambda **_kwargs: transaction,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_auto_healing_recovery_required",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_auto_healing_approval_digest",
        lambda **_kwargs: "d" * 64,
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", lock)
    monkeypatch.setattr("nebius_vpngw.cli._execute_vm_ha_auto_healing_policy", effect)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--standby-auto-healing",
            "disabled",
            "--dry-run",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "planned"
    assert payload["classification"] == "maintenance-policy"
    assert payload["impact"] == {
        "summary": "No VPN traffic interruption or destructive changes are expected",
        "destructive": False,
        "vpn_traffic_interruption": False,
        "resource_creation": False,
        "approval_required": False,
    }
    assert payload["approval"] == {
        "kind": "standby-auto-healing-policy",
        "digest": "d" * 64,
        "effects": [
            "prepare-disable-on-coordinator-node-a",
            "prepare-disable-on-peer",
            "wait-for-accepted-start-quiescence",
            "commit-disable-on-peer",
            "commit-disable-on-coordinator-node-a",
            "verify-two-member-policy-agreement",
        ],
    }
    lock.assert_not_called()
    effect.assert_not_called()


def test_vm_ha_missing_owner_policy_dry_run_plans_exact_bootstrap_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    blocked = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="inspect",
            reasons=("standby-auto-healing-policy-invalid",),
        )
    )
    owner_status = {
        "accepted_start": False,
        "cluster_id": "cluster-a",
        "configured_role": "active",
        "decision_digest": None,
        "desired": None,
        "generation_id": "a" * 64,
        "node_id": "node-0",
        "operation_id": None,
        "peer_agrees": False,
        "phase": "blocked",
        "record": None,
        "recovery": None,
        "recovery_authority": {
            "allocation_id": "allocation-a",
            "ownership_epoch": "7",
            "promotion_receipt_id": "receipt-a",
        },
        "recovery_phase": None,
        "schema": "nebius-vpngw/vm-ha-auto-healing-policy-status-v2",
    }
    lock = Mock(side_effect=AssertionError("dry-run acquired the writer lock"))
    effect = Mock(side_effect=AssertionError("dry-run changed the policy"))

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: blocked,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_statuses",
        lambda **_kwargs: [owner_status],
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", lock)
    monkeypatch.setattr("nebius_vpngw.cli._execute_vm_ha_auto_healing_policy", effect)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--standby-auto-healing",
            "enabled",
            "--dry-run",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "planned"
    assert payload["classification"] == "maintenance-policy"
    assert payload["approval"]["effects"] == [
        "initialize-owner-policy",
        "arm-owner-local-standby-recovery",
        "request-owner-rearm-start",
        "wait-for-restored-standby-readiness",
        "initialize-restored-peer-policy",
        "wait-for-accepted-start-quiescence",
        "verify-two-member-policy-agreement",
    ]
    lock.assert_not_called()
    effect.assert_not_called()


@pytest.mark.parametrize(
    ("target_state", "start_effects"),
    (
        (
            "stopped",
            [
                "arm-owner-local-standby-recovery",
                "request-owner-rearm-start",
            ],
        ),
        ("running", []),
    ),
)
def test_vm_ha_completed_owner_recovery_dry_run_resumes_after_compute_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_state: str,
    start_effects: list[str],
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    blocked = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="inspect",
            reasons=("standby-auto-healing-policy-invalid",),
            member_compute_states=(
                ("node-0", "running"),
                ("node-1", target_state),
            ),
        )
    )
    owner_status, _peer_status = _completed_recovery_statuses(tmp_path / "state")
    policy_action = Mock(side_effect=(RuntimeError("peer policy unavailable"), [owner_status]))
    lock = Mock(side_effect=AssertionError("dry-run acquired the writer lock"))

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: blocked,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_action",
        policy_action,
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", lock)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--standby-auto-healing",
            "enabled",
            "--dry-run",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "planned"
    assert payload["classification"] == "maintenance-policy"
    assert payload["approval"]["kind"] == "standby-auto-healing-policy"
    assert payload["approval"]["digest"] != "d" * 64
    assert payload["approval"]["effects"] == [
        *start_effects,
        "wait-for-restored-standby-readiness",
        "initialize-restored-peer-policy",
        "wait-for-accepted-start-quiescence",
        "verify-two-member-policy-agreement",
    ]
    assert policy_action.call_count == 2
    lock.assert_not_called()


def test_vm_ha_completed_owner_recovery_approval_rearms_stopped_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    blocked = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="inspect",
            reasons=("standby-auto-healing-policy-invalid",),
            member_compute_states=(
                ("node-0", "running"),
                ("node-1", "stopped"),
            ),
        )
    )
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none"))
    inspect = Mock(side_effect=(blocked, blocked, blocked, blocked, healthy, healthy))
    owner_status, peer_status = _completed_recovery_statuses(tmp_path / "state")
    transaction = _vm_ha_auto_healing_transaction_for_statuses(
        desired=StandbyAutoHealing.ENABLED,
        inspection=blocked,
        statuses=[owner_status],
    )
    recovery_required = _vm_ha_auto_healing_recovery_required(
        desired=StandbyAutoHealing.ENABLED,
        inspection=blocked,
        statuses=[owner_status],
    )
    approval_digest = _vm_ha_auto_healing_approval_digest(
        desired=StandbyAutoHealing.ENABLED,
        inspection=blocked,
        statuses=[owner_status],
        transaction=transaction,
        recovery_required=recovery_required,
    )
    assert approval_digest != "d" * 64
    policy_statuses = Mock(return_value=[owner_status])
    prepare = Mock()
    terminal_statuses = list(
        _terminal_enabled_recovery_statuses(
            peer_agrees=True,
            operation_id=transaction.operation_id,
            predecessor_digest=transaction.predecessor_digest,
        )
    )
    execute = Mock(return_value=terminal_statuses)
    arm = Mock()

    def policy_action(**kwargs):
        action = kwargs["action"]
        if action == "initialize":
            return [peer_status]
        if action == "status":
            return [owner_status, peer_status]
        if action == "clear-recovery":
            recovery = AutoHealingRecoveryRecord.from_mapping(terminal_statuses[0]["recovery"])
            assert kwargs["node_ids"] == frozenset({recovery.node_id})
            request = decode_policy_request(kwargs["requests"][recovery.node_id])
            assert request == {
                "schema": "nebius-vpngw/vm-ha-auto-healing-policy-request-v3",
                "operation_id": recovery.operation_id,
                "recovery_digest": auto_healing_recovery_digest(recovery),
            }
            return [{**owner_status, "recovery": None, "recovery_phase": None}]
        raise AssertionError(f"unexpected policy action: {action}")

    class Lock:
        def __init__(self, **kwargs) -> None:
            assert kwargs == {"project_id": "project-a", "gateway_name": "gateway"}

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_statuses",
        policy_statuses,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_action",
        policy_action,
    )
    monkeypatch.setattr("nebius_vpngw.cli._prepare_vm_ha_planned_target", prepare)
    monkeypatch.setattr("nebius_vpngw.cli._arm_vm_ha_auto_healing_recovery", arm)
    monkeypatch.setattr("nebius_vpngw.cli._execute_vm_ha_auto_healing_policy", execute)
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", Lock)
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--standby-auto-healing",
            "enabled",
            "--approve",
            approval_digest,
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "healthy"
    assert payload["actions"] == ["standby-auto-healing-enabled"]
    assert prepare.call_count == 1
    before_rearm_request = prepare.call_args.kwargs["before_rearm_request"]
    before_rearm_request("node-0", "node-1", "revision-b")
    arm.assert_called_once()
    execute.assert_called_once()
    assert inspect.call_count == 6


def test_vm_ha_bare_policy_invalid_guidance_is_repeatable_and_mutation_free(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    blocked = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="inspect",
            reasons=("standby-auto-healing-policy-invalid",),
        )
    )
    lock = Mock(side_effect=AssertionError("bare status acquired the writer lock"))
    effect = Mock(side_effect=AssertionError("bare status attempted recovery"))

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: blocked,
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", lock)
    monkeypatch.setattr("nebius_vpngw.cli._prepare_vm_ha_planned_target", effect)

    payloads = []
    for _ in range(2):
        result = CliRunner().invoke(
            app,
            [
                "vm-ha",
                "--local-config-file",
                str(config_path),
                "--output-format",
                "json",
            ],
        )
        assert result.exit_code == 1
        payloads.append(json.loads(result.stdout))

    assert payloads[0] == payloads[1]
    assert payloads[0]["classification"] == "maintenance-policy"
    assert payloads[0]["reasons"] == ["standby-auto-healing-policy-invalid"]
    assert payloads[0]["next_action"] == (
        f"run nebius-vpngw vm-ha --local-config-file {config_path} --standby-auto-healing enabled"
    )
    lock.assert_not_called()
    effect.assert_not_called()


def test_terminal_replacement_policy_split_is_apply_owned() -> None:
    snapshot = _snapshot(
        overall="BLOCKED",
        action="inspect",
        reasons=("standby-auto-healing-policy-invalid",),
    )
    snapshot = _VMHAStatusSnapshot(
        **{
            **snapshot.__dict__,
            "lifecycle_state": SimpleNamespace(
                status=VMHALifecycleStatus.ACTIVE,
                transaction=SimpleNamespace(
                    checkpoint="missing-standby-replacement-complete",
                    pending_effect=None,
                    accepted_cloud_operation_id=None,
                ),
            ),
        }
    )

    result = _vm_ha_result_from_snapshot(
        config_path=Path("gateway.vm-ha.config.yaml"),
        snapshot=snapshot,
        actions=(),
        dry_run=True,
    )

    assert result.classification is VMHACommandClassification.APPLY_REQUIRED
    assert result.reasons == ("replacement-policy-convergence-required",)


def test_terminal_replacement_policy_split_requires_both_computes_running() -> None:
    snapshot = _snapshot(
        overall="BLOCKED",
        action="inspect",
        reasons=("standby-auto-healing-policy-invalid",),
        member_compute_states=(("node-0", "running"), ("node-1", "stopped")),
    )
    snapshot = _VMHAStatusSnapshot(
        **{
            **snapshot.__dict__,
            "lifecycle_state": SimpleNamespace(
                status=VMHALifecycleStatus.ACTIVE,
                transaction=SimpleNamespace(
                    checkpoint="missing-standby-replacement-complete",
                    pending_effect=None,
                    accepted_cloud_operation_id=None,
                ),
            ),
        }
    )

    result = _vm_ha_result_from_snapshot(
        config_path=Path("gateway.vm-ha.config.yaml"),
        snapshot=snapshot,
        actions=(),
        dry_run=True,
    )

    assert result.classification is VMHACommandClassification.MAINTENANCE_POLICY
    assert result.reasons == ("standby-auto-healing-policy-invalid",)


def test_replacement_policy_adoption_is_bound_before_activation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    replacement = AutoHealingPolicyStore(tmp_path).initialize(
        cluster_id="cluster-a",
        node_id="node-1",
        peer_node_id="node-0",
        generation_id="a" * 64,
        updated_at=1.0,
    )
    owner_operation = "e" * 64
    owner_predecessor = "b" * 64
    owner_digest = policy_decision_digest(
        cluster_id="cluster-a",
        member_node_ids=("node-0", "node-1"),
        generation_id="a" * 64,
        desired=StandbyAutoHealing.ENABLED,
        operation_id=owner_operation,
        coordinator_node_id="node-0",
        predecessor_digest=owner_predecessor,
    )
    owner = AutoHealingPolicyRecord(
        cluster_id="cluster-a",
        node_id="node-0",
        peer_node_id="node-1",
        generation_id="a" * 64,
        desired=StandbyAutoHealing.ENABLED,
        operation_id=owner_operation,
        coordinator_node_id="node-0",
        predecessor_digest=owner_predecessor,
        phase=AutoHealingPolicyPhase.COMMITTED,
        decision_digest=owner_digest,
        peer_ack_digest=owner_digest,
        updated_at=2.0,
    )
    adopted = AutoHealingPolicyRecord(
        **{
            **owner.__dict__,
            "node_id": "node-1",
            "peer_node_id": "node-0",
            "updated_at": 3.0,
        }
    )
    statuses = [
        {
            "accepted_start": False,
            "node_id": owner.node_id,
            "record": owner.to_dict(),
            "recovery": None,
            "recovery_phase": None,
        },
        {
            "accepted_start": False,
            "node_id": replacement.node_id,
            "record": replacement.to_dict(),
            "recovery": None,
            "recovery_phase": None,
        },
    ]
    calls: list[dict[str, object]] = []

    def fake_action(**kwargs):
        calls.append(kwargs)
        if kwargs.get("node_ids") == frozenset({owner.node_id}):
            return [statuses[0]]
        return [statuses[0], {**statuses[1], "record": adopted.to_dict()}]

    monkeypatch.setattr("nebius_vpngw.cli._run_vm_ha_auto_healing_action", fake_action)

    request = _vm_ha_replacement_policy_adoption_request(
        config_path=tmp_path / "gateway.config.yaml",
        owner_node_id=owner.node_id,
        apply_operation_id="c" * 64,
        mtls_apply_operation_id="f" * 64,
        mtls_inhibition_operation_id=None,
    )
    assert decode_policy_request(request) == {
        "schema": "nebius-vpngw/vm-ha-auto-healing-policy-request-v3",
        "apply_operation_id": "c" * 64,
        "mtls_apply_operation_id": "f" * 64,
        "mtls_inhibition_operation_id": None,
        "operation_id": owner.operation_id,
        "peer_record": owner.to_dict(),
    }
    assert (
        _reconcile_vm_ha_replacement_auto_healing_policy(
            config_path=tmp_path / "gateway.config.yaml",
            owner_node_id=owner.node_id,
        )
        is None
    )
    assert all(call["action"] == "status" for call in calls)


def test_replacement_policy_adoption_accepts_exact_default_unacknowledged_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    owner = AutoHealingPolicyStore(tmp_path / "owner").initialize(
        cluster_id="cluster-a",
        node_id="node-0",
        peer_node_id="node-1",
        generation_id="a" * 64,
        updated_at=1.0,
    )
    assert owner.peer_ack_digest is None
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_action",
        lambda **_kwargs: [
            {
                "accepted_start": False,
                "node_id": owner.node_id,
                "record": owner.to_dict(),
                "recovery": None,
                "recovery_phase": None,
            }
        ],
    )

    request = _vm_ha_replacement_policy_adoption_request(
        config_path=tmp_path / "gateway.config.yaml",
        owner_node_id=owner.node_id,
        apply_operation_id="c" * 64,
        mtls_apply_operation_id="f" * 64,
        mtls_inhibition_operation_id=None,
    )

    assert decode_policy_request(request)["peer_record"] == owner.to_dict()


def test_replacement_policy_reproof_converges_exact_default_split(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    owner = AutoHealingPolicyStore(tmp_path / "owner").initialize(
        cluster_id="cluster-a",
        node_id="node-0",
        peer_node_id="node-1",
        generation_id="a" * 64,
        updated_at=1.0,
    )
    replacement_store = AutoHealingPolicyStore(tmp_path / "replacement")
    replacement_store.initialize(
        cluster_id="cluster-a",
        node_id="node-1",
        peer_node_id="node-0",
        generation_id="a" * 64,
        updated_at=2.0,
    )
    replacement = replacement_store.adopt_replacement_peer(
        cluster_id="cluster-a",
        node_id="node-1",
        peer_node_id="node-0",
        generation_id="a" * 64,
        peer=owner,
        updated_at=3.0,
    )
    statuses = [
        {
            "accepted_start": False,
            "node_id": owner.node_id,
            "record": owner.to_dict(),
            "recovery": None,
            "recovery_phase": None,
        },
        {
            "accepted_start": False,
            "node_id": replacement.node_id,
            "record": replacement.to_dict(),
            "recovery": None,
            "recovery_phase": None,
        },
    ]
    captured: list[_VMHAAutoHealingTransaction] = []

    def execute_policy(**kwargs):
        transaction = kwargs["transaction"]
        captured.append(transaction)
        decision_digest = policy_decision_digest(
            cluster_id=owner.cluster_id,
            member_node_ids=transaction.member_node_ids,
            generation_id=owner.generation_id,
            desired=StandbyAutoHealing.ENABLED,
            operation_id=transaction.operation_id,
            coordinator_node_id=transaction.coordinator_node_id,
            predecessor_digest=transaction.predecessor_digest,
        )
        return [
            {
                "accepted_start": False,
                "decision_digest": decision_digest,
                "desired": StandbyAutoHealing.ENABLED.value,
                "node_id": node_id,
                "operation_id": transaction.operation_id,
                "peer_agrees": True,
                "phase": AutoHealingPolicyPhase.COMMITTED.value,
                "recovery_phase": None,
            }
            for node_id in transaction.member_node_ids
        ]

    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_action",
        lambda **_kwargs: statuses,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._execute_vm_ha_auto_healing_policy",
        execute_policy,
    )

    assert (
        _reconcile_vm_ha_replacement_auto_healing_policy(
            config_path=tmp_path / "gateway.config.yaml",
            owner_node_id=owner.node_id,
        )
        is None
    )
    assert len(captured) == 1
    assert captured[0].member_node_ids == ("node-0", "node-1")
    assert captured[0].predecessor_digest == owner.decision_digest
    assert captured[0].operation_id != owner.operation_id


def test_replacement_policy_reproof_resumes_after_coordinator_prepare(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    owner_store = AutoHealingPolicyStore(tmp_path / "owner")
    owner = owner_store.initialize(
        cluster_id="cluster-a",
        node_id="node-0",
        peer_node_id="node-1",
        generation_id="a" * 64,
        updated_at=1.0,
    )
    replacement_store = AutoHealingPolicyStore(tmp_path / "replacement")
    replacement_store.initialize(
        cluster_id="cluster-a",
        node_id="node-1",
        peer_node_id="node-0",
        generation_id="a" * 64,
        updated_at=2.0,
    )
    replacement = replacement_store.adopt_replacement_peer(
        cluster_id="cluster-a",
        node_id="node-1",
        peer_node_id="node-0",
        generation_id="a" * 64,
        peer=owner,
        updated_at=3.0,
    )
    member_node_ids = ("node-0", "node-1")
    operation_id = hashlib.sha256(
        json.dumps(
            {
                "cluster_id": owner.cluster_id,
                "coordinator_node_id": member_node_ids[0],
                "desired": StandbyAutoHealing.ENABLED.value,
                "generation_id": owner.generation_id,
                "member_node_ids": list(member_node_ids),
                "predecessor_digest": owner.decision_digest,
                "schema": "nebius-vpngw/vm-ha-auto-healing-transaction-v2",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    owner_prepared = owner_store.prepare(
        cluster_id=owner.cluster_id,
        node_id=owner.node_id,
        peer_node_id=owner.peer_node_id,
        generation_id=owner.generation_id,
        desired=StandbyAutoHealing.ENABLED,
        operation_id=operation_id,
        coordinator_node_id=member_node_ids[0],
        predecessor_digest=owner.decision_digest,
        peer=replacement,
        updated_at=4.0,
    )
    statuses = [
        {
            "accepted_start": False,
            "node_id": owner_prepared.node_id,
            "record": owner_prepared.to_dict(),
            "recovery": None,
            "recovery_phase": None,
        },
        {
            "accepted_start": False,
            "node_id": replacement.node_id,
            "record": replacement.to_dict(),
            "recovery": None,
            "recovery_phase": None,
        },
    ]
    captured: list[_VMHAAutoHealingTransaction] = []

    def execute_policy(**kwargs):
        transaction = kwargs["transaction"]
        captured.append(transaction)
        decision_digest = policy_decision_digest(
            cluster_id=owner.cluster_id,
            member_node_ids=transaction.member_node_ids,
            generation_id=owner.generation_id,
            desired=StandbyAutoHealing.ENABLED,
            operation_id=transaction.operation_id,
            coordinator_node_id=transaction.coordinator_node_id,
            predecessor_digest=transaction.predecessor_digest,
        )
        return [
            {
                "accepted_start": False,
                "decision_digest": decision_digest,
                "desired": StandbyAutoHealing.ENABLED.value,
                "node_id": node_id,
                "operation_id": transaction.operation_id,
                "peer_agrees": True,
                "phase": AutoHealingPolicyPhase.COMMITTED.value,
                "recovery_phase": None,
            }
            for node_id in transaction.member_node_ids
        ]

    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_action",
        lambda **_kwargs: statuses,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._execute_vm_ha_auto_healing_policy",
        execute_policy,
    )

    assert (
        _reconcile_vm_ha_replacement_auto_healing_policy(
            config_path=tmp_path / "gateway.config.yaml",
            owner_node_id=owner.node_id,
        )
        is None
    )
    assert captured == [
        _VMHAAutoHealingTransaction(
            operation_id=operation_id,
            coordinator_node_id="node-0",
            predecessor_digest=owner.decision_digest,
            member_node_ids=member_node_ids,
        )
    ]


def test_vm_ha_safe_standby_auto_healing_disable_executes_without_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none"))
    maintenance = _inspection(
        _snapshot(
            overall="MAINTENANCE",
            action="enable-standby-auto-healing",
            reasons=("standby-auto-healing-policy-disabled",),
        )
    )
    inspect = Mock(side_effect=(healthy, healthy, maintenance))
    statuses = [{"record": {"node_id": "node-a"}}, {"record": {"node_id": "node-b"}}]
    transaction = _VMHAAutoHealingTransaction(
        operation_id="b" * 64,
        coordinator_node_id="node-a",
        predecessor_digest="c" * 64,
        member_node_ids=("node-a", "node-b"),
    )
    effect = Mock()

    class Lock:
        def __init__(self, **kwargs) -> None:
            assert kwargs == {"project_id": "project-a", "gateway_name": "gateway"}

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_statuses",
        lambda **_kwargs: statuses,
    )
    monkeypatch.setattr("nebius_vpngw.cli._vm_ha_auto_healing_is_terminal", lambda *_a: False)
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_auto_healing_transaction",
        lambda **_kwargs: transaction,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_auto_healing_recovery_required",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_auto_healing_approval_digest",
        lambda **_kwargs: "d" * 64,
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", Lock)
    monkeypatch.setattr("nebius_vpngw.cli._execute_vm_ha_auto_healing_policy", effect)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--standby-auto-healing",
            "disabled",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "maintenance"
    assert payload["actions"] == ["standby-auto-healing-disabled"]
    assert payload["reasons"] == ["maintenance-ready"]
    assert "Proceed with this exact VM-HA plan?" not in result.stderr
    effect.assert_called_once_with(
        config_path=config_path,
        desired=StandbyAutoHealing.DISABLED,
        transaction=transaction,
        initial_statuses=statuses,
        authority_guard=effect.call_args.kwargs["authority_guard"],
    )
    assert callable(effect.call_args.kwargs["authority_guard"])
    assert inspect.call_count == 3


@pytest.mark.parametrize(
    ("drift_owner_node_id", "drift_observation_digest"),
    (("node-1", "cloud-a"), ("node-0", "cloud-b")),
)
def test_vm_ha_auto_healing_projection_fails_closed_on_cloud_authority_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    drift_owner_node_id: str,
    drift_observation_digest: str,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    healthy = _inspection(
        _snapshot(
            overall="HEALTHY",
            action="none",
            authority_observation_digest="cloud-a",
        )
    )
    stale_projection = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="inspect",
            reasons=("standby-auto-healing-policy-invalid",),
            authority_observation_digest="cloud-a",
        )
    )
    drifted_terminal_projection = _inspection(
        _snapshot(
            overall="MAINTENANCE",
            action="enable-standby-auto-healing",
            reasons=("standby-auto-healing-policy-disabled",),
            summary_rows=(
                ("Auto-healing", "disabled", "automatic standby restoration is disabled"),
            ),
            authority_owner_node_id=drift_owner_node_id,
            authority_observation_digest=drift_observation_digest,
        )
    )
    inspect = Mock(side_effect=(healthy, healthy, stale_projection, drifted_terminal_projection))
    statuses = [{"record": {"node_id": "node-a"}}, {"record": {"node_id": "node-b"}}]
    transaction = _VMHAAutoHealingTransaction(
        operation_id="b" * 64,
        coordinator_node_id="node-a",
        predecessor_digest="c" * 64,
        member_node_ids=("node-a", "node-b"),
    )
    execute = Mock()

    class Lock:
        def __init__(self, **kwargs) -> None:
            assert kwargs == {"project_id": "project-a", "gateway_name": "gateway"}

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_statuses",
        lambda **_kwargs: statuses,
    )
    monkeypatch.setattr("nebius_vpngw.cli._vm_ha_auto_healing_is_terminal", lambda *_a: False)
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_auto_healing_transaction",
        lambda **_kwargs: transaction,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_auto_healing_recovery_required",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_auto_healing_approval_digest",
        lambda **_kwargs: "d" * 64,
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", Lock)
    monkeypatch.setattr("nebius_vpngw.cli._execute_vm_ha_auto_healing_policy", execute)
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--standby-auto-healing",
            "disabled",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["reasons"] == ["convergence-failed-safely"]
    execute.assert_called_once()
    assert inspect.call_count == 4


def test_vm_ha_terminal_enabled_policy_retries_completed_recovery_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none"))
    statuses = list(_terminal_enabled_recovery_statuses(peer_agrees=True))
    cleaned_statuses = [{**status, "recovery": None, "recovery_phase": None} for status in statuses]
    cleanup = Mock(return_value=True)

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: healthy,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_statuses",
        Mock(side_effect=(statuses, cleaned_statuses)),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._clear_completed_vm_ha_auto_healing_recovery",
        cleanup,
    )
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--standby-auto-healing",
            "enabled",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "healthy"
    assert payload["actions"] == [
        "standby-auto-healing-recovery-cleared",
        "standby-auto-healing-already-enabled",
    ]
    cleanup.assert_called_once_with(
        config_path=config_path,
        statuses=statuses,
    )


def test_vm_ha_committed_enabled_policy_clears_completed_recovery_before_fresh_peer_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none"))
    statuses = list(_terminal_enabled_recovery_statuses(peer_agrees=False))
    cleaned_statuses = [{**status, "recovery": None, "recovery_phase": None} for status in statuses]
    planner = Mock(side_effect=AssertionError("cleanup created a new policy transaction"))

    def policy_action(**kwargs):
        assert kwargs["action"] == "clear-recovery"
        assert kwargs["node_ids"] == frozenset({"node-0"})
        return [{**statuses[0], "recovery": None, "recovery_phase": None}]

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: healthy,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_statuses",
        Mock(side_effect=(statuses, cleaned_statuses)),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_action",
        policy_action,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_auto_healing_transaction_for_statuses",
        planner,
    )

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--standby-auto-healing",
            "enabled",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "healthy"
    assert payload["actions"] == [
        "standby-auto-healing-recovery-cleared",
        "standby-auto-healing-already-enabled",
    ]
    planner.assert_not_called()


def test_vm_ha_completed_recovery_cleanup_failure_keeps_specific_maintenance_guidance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none"))
    statuses = list(_terminal_enabled_recovery_statuses(peer_agrees=True))
    cleanup = Mock(side_effect=RuntimeError("private cleanup detail"))
    planner = Mock(side_effect=AssertionError("cleanup failure planned a transaction"))

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: healthy,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_statuses",
        lambda **_kwargs: statuses,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._clear_completed_vm_ha_auto_healing_recovery",
        cleanup,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_auto_healing_transaction_for_statuses",
        planner,
    )

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--standby-auto-healing",
            "enabled",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["classification"] == "maintenance-policy"
    assert payload["health"] == "blocked"
    assert payload["reasons"] == ["standby-auto-healing-recovery-cleanup-required"]
    assert payload["next_action"] == (
        "inspect VM-HA agent journals for the recovery owner, then rerun the same "
        "requested vm-ha command"
    )
    assert "private cleanup detail" not in result.stdout
    planner.assert_not_called()


def test_vm_ha_completed_recovery_without_current_or_durable_agreement_stays_blocked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none"))
    statuses = list(_terminal_enabled_recovery_statuses(peer_agrees=False))
    for status in statuses:
        record = dict(status["record"])
        record["peer_ack_digest"] = None
        status["record"] = record
    cleanup = Mock(side_effect=AssertionError("unacknowledged recovery was cleared"))
    planner = Mock(side_effect=AssertionError("unacknowledged recovery was replanned"))

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: healthy,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_statuses",
        lambda **_kwargs: statuses,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._clear_completed_vm_ha_auto_healing_recovery",
        cleanup,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._vm_ha_auto_healing_transaction_for_statuses",
        planner,
    )

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--standby-auto-healing",
            "enabled",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["classification"] == "maintenance-policy"
    assert payload["reasons"] == ["standby-auto-healing-recovery-cleanup-required"]
    cleanup.assert_not_called()
    planner.assert_not_called()


def test_vm_ha_terminal_enabled_dry_run_never_clears_completed_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none"))
    statuses = list(_terminal_enabled_recovery_statuses(peer_agrees=True))
    cleanup = Mock(side_effect=AssertionError("dry-run cleared completed recovery"))

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: healthy,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_statuses",
        lambda **_kwargs: statuses,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._clear_completed_vm_ha_auto_healing_recovery",
        cleanup,
    )
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--standby-auto-healing",
            "enabled",
            "--dry-run",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "planned"
    assert payload["actions"] == ["standby-auto-healing-already-enabled"]
    assert payload["approval"]["effects"] == [
        "clear-completed-recovery",
        "verify-two-member-policy-agreement",
        "confirm-standby-auto-healing-already-enabled",
    ]
    cleanup.assert_not_called()


def test_vm_ha_disable_dry_run_orders_cleanup_before_requested_transition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none"))
    statuses = list(_terminal_enabled_recovery_statuses(peer_agrees=True))
    cleanup = Mock(side_effect=AssertionError("dry-run cleared completed recovery"))

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: healthy,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_statuses",
        lambda **_kwargs: statuses,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._clear_completed_vm_ha_auto_healing_recovery",
        cleanup,
    )

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--standby-auto-healing",
            "disabled",
            "--dry-run",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "planned"
    assert payload["actions"] == ["standby-auto-healing-disable"]
    assert payload["approval"]["effects"][0] == "clear-completed-recovery"
    assert payload["approval"]["effects"][1].startswith("prepare-disable-on-coordinator-")
    cleanup.assert_not_called()


def test_vm_ha_disable_cleanup_plan_has_a_distinct_approval_digest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none"))
    recovery_statuses = list(_terminal_enabled_recovery_statuses(peer_agrees=True))
    clean_statuses = [
        {**status, "recovery": None, "recovery_phase": None} for status in recovery_statuses
    ]
    current_statuses = [clean_statuses]

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: healthy,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_statuses",
        lambda **_kwargs: current_statuses[0],
    )

    clean_plan = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--standby-auto-healing",
            "disabled",
            "--dry-run",
            "--output-format",
            "json",
        ],
    )
    current_statuses[0] = recovery_statuses
    cleanup_plan = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--standby-auto-healing",
            "disabled",
            "--dry-run",
            "--output-format",
            "json",
        ],
    )

    assert clean_plan.exit_code == 0
    assert cleanup_plan.exit_code == 0
    clean_digest = json.loads(clean_plan.stdout)["approval"]["digest"]
    cleanup_payload = json.loads(cleanup_plan.stdout)
    assert cleanup_payload["approval"]["digest"] != clean_digest
    assert cleanup_payload["approval"]["effects"][0] == "clear-completed-recovery"


def test_vm_ha_stale_clean_plan_approval_cannot_clear_completed_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none"))
    recovery_statuses = list(_terminal_enabled_recovery_statuses(peer_agrees=True))
    clean_statuses = [
        {**status, "recovery": None, "recovery_phase": None} for status in recovery_statuses
    ]
    current_statuses = [clean_statuses]
    cleanup = Mock(side_effect=AssertionError("stale approval cleared recovery"))

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: healthy,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_statuses",
        lambda **_kwargs: current_statuses[0],
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._clear_completed_vm_ha_auto_healing_recovery",
        cleanup,
    )

    clean_plan = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--standby-auto-healing",
            "disabled",
            "--dry-run",
            "--output-format",
            "json",
        ],
    )
    assert clean_plan.exit_code == 0
    clean_digest = json.loads(clean_plan.stdout)["approval"]["digest"]
    current_statuses[0] = recovery_statuses

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--standby-auto-healing",
            "disabled",
            "--approve",
            clean_digest,
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["reasons"] == ["approval-digest-stale-or-incorrect"]
    cleanup.assert_not_called()


def test_vm_ha_disable_clears_completed_recovery_and_finishes_requested_transition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none"))
    maintenance = _inspection(
        _snapshot(
            overall="MAINTENANCE",
            action="enable-standby-auto-healing",
            reasons=("standby-auto-healing-policy-disabled",),
        )
    )
    inspect = Mock(side_effect=(healthy, healthy, maintenance))
    statuses = list(_terminal_enabled_dual_recovery_statuses(peer_agrees=True))
    cleaned_statuses = [{**status, "recovery": None, "recovery_phase": None} for status in statuses]
    policy_status = Mock(side_effect=(statuses, cleaned_statuses, cleaned_statuses))
    cleanup = Mock(return_value=True)
    execute = Mock(return_value=[])

    class Lock:
        def __init__(self, **kwargs) -> None:
            assert kwargs == {"project_id": "project-a", "gateway_name": "gateway"}

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_statuses",
        policy_status,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._clear_completed_vm_ha_auto_healing_recovery",
        cleanup,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._execute_vm_ha_auto_healing_policy",
        execute,
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", Lock)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--standby-auto-healing",
            "disabled",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "maintenance"
    assert payload["actions"] == [
        "standby-auto-healing-recovery-cleared",
        "standby-auto-healing-disabled",
    ]
    cleanup.assert_called_once_with(config_path=config_path, statuses=statuses)
    execute.assert_called_once()
    assert policy_status.call_count == 3
    assert inspect.call_count == 3


def test_vm_ha_force_never_replaces_a_conflicting_candidate(
    monkeypatch,
    sample_config: dict,
    tmp_path: Path,
) -> None:
    source = tmp_path / "gateway.config.yaml"
    destination = tmp_path / "gateway.vm-ha.config.yaml"
    source.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")
    original = b"foreign: true\n"
    destination.write_bytes(original)
    wizard = Mock(side_effect=AssertionError("conflicting candidate reached wizard"))
    monkeypatch.setattr("nebius_vpngw.cli.run_vm_ha_conversion_wizard", wizard)

    result = _resolve_vm_ha_effective_config(
        source_path=source,
        output=destination,
        force=True,
        dry_run=False,
        interactive=True,
        region=None,
    )

    assert isinstance(result, VMHACommandResult)
    assert result.classification is VMHACommandClassification.CONVERSION_REQUIRED
    assert result.reasons == ("candidate-conflicts-with-source",)
    assert destination.read_bytes() == original
    wizard.assert_not_called()


def test_vm_ha_healthy_requires_two_agreeing_fresh_samples(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none"))

    def inspect_with_retry(_path: Path) -> _VMHACommandInspection:
        try:
            raise RuntimeError("TRANSIENT_PROVIDER_DETAIL")
        except RuntimeError:
            logging.getLogger("nebius.aio.request").error(
                "request attempt 1 for Request failed but will be retried",
                exc_info=True,
            )
        return healthy

    inspect = Mock(side_effect=inspect_with_retry)
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ("candidate-reused",)),
    )
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "healthy"
    assert payload["actions"] == ["candidate-reused"]
    assert inspect.call_count == 2
    assert "request attempt" not in result.stderr
    assert "TRANSIENT_PROVIDER_DETAIL" not in result.stderr
    assert "Traceback" not in result.stderr


def test_vm_ha_region_override_reaches_every_health_observation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none"))
    observed_regions: list[str | None] = []

    def inspect(
        _path: Path,
        *,
        region: str | None = None,
    ) -> _VMHACommandInspection:
        observed_regions.append(region)
        return healthy

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--region",
            "eu-north1",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["outcome"] == "healthy"
    assert observed_regions == ["eu-north1", "eu-north1"]


def test_vm_ha_inspection_uses_the_plan_region_for_both_manager_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    local_cfg = {
        "project_id": "project-a",
        "region_id": "eu-east1",
        "gateway_group": {"region": "eu-central1"},
    }
    plan = SimpleNamespace(
        gateway_group=SimpleNamespace(region="eu-north1"),
        iter_instance_configs=lambda: (),
    )
    observed: dict[str, object] = {}

    class InspectionStopped(RuntimeError):
        pass

    class FakeVMManager(_ContextManagedFake):
        def __init__(self, *args, **kwargs) -> None:
            observed.update(kwargs)
            raise InspectionStopped

    monkeypatch.setattr(
        "nebius_vpngw.cli._load_config_with_region_override",
        lambda *_args, **_kwargs: local_cfg,
    )
    monkeypatch.setattr("nebius_vpngw.cli.merge_with_peer_configs", lambda *_args: plan)
    monkeypatch.setattr("nebius_vpngw.cli._enforce_command_applicability", lambda *_args: None)
    monkeypatch.setattr(
        "nebius_vpngw.cli._ensure_authentication",
        lambda **_kwargs: "token",
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMManager", FakeVMManager)

    with pytest.raises(InspectionStopped):
        _inspect_vm_ha_command_status(config_path, region="eu-north1")

    assert observed["region"] == "eu-north1"
    assert observed["region_id"] == "eu-north1"


def test_vm_ha_dry_run_plans_rearm_without_lock_or_effect(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    degraded = _inspection(
        _snapshot(
            overall="DEGRADED",
            action="nebius-vpngw vm-ha --local-config-file fixture",
            reasons=("standby-not-ready",),
        )
    )
    lock = Mock(side_effect=AssertionError("dry-run acquired a lock"))
    effect = Mock(side_effect=AssertionError("dry-run invoked rearm"))
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: degraded,
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", lock)
    monkeypatch.setattr("nebius_vpngw.cli._prepare_vm_ha_planned_target", effect)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--dry-run",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "planned"
    assert payload["classification"] == "standby-rearm"
    assert payload["actions"] == ["rearm-exact-standby"]
    lock.assert_not_called()
    effect.assert_not_called()


def test_vm_ha_rearm_reclassifies_under_lock_and_verifies_two_samples(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    degraded = _inspection(
        _snapshot(
            overall="DEGRADED",
            action="nebius-vpngw vm-ha --local-config-file fixture",
            reasons=("standby-not-ready",),
        )
    )
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none", digest="b" * 64))
    inspect = Mock(side_effect=(degraded, degraded, healthy, healthy))
    effect = Mock()

    class Lock:
        def __init__(self, **kwargs) -> None:
            assert kwargs == {"project_id": "project-a", "gateway_name": "gateway"}

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", Lock)
    monkeypatch.setattr("nebius_vpngw.cli._prepare_vm_ha_planned_target", effect)
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "healthy"
    assert payload["actions"] == ["standby-rearmed"]
    assert result.stderr.count("acquiring the VM-HA writer lock") == 1
    assert "✓ acquiring the VM-HA writer lock" in result.stderr
    assert result.stderr.count("verifying two agreeing fresh health samples") == 1
    assert "✓ verifying two agreeing fresh health samples" in result.stderr
    effect.assert_called_once()
    assert effect.call_args.kwargs["local_config_file"] == config_path
    assert effect.call_args.kwargs["target_role"] is None
    assert effect.call_args.kwargs["command"] == "vm-ha"
    assert effect.call_args.kwargs["show_auth_progress"] is False
    assert callable(effect.call_args.kwargs["progress_sink"])
    assert inspect.call_count == 4


def test_vm_ha_transition_is_action_required_and_never_calls_legacy_handlers(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    transitioning = _inspection(
        _snapshot(
            overall="TRANSITIONING",
            action="wait",
            reasons=("controller-operation-pending",),
        )
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    inspect = Mock(return_value=transitioning)
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)
    for name in ("apply", "status"):
        monkeypatch.setattr(
            f"nebius_vpngw.cli.{name}",
            Mock(side_effect=AssertionError(f"called legacy handler {name}")),
        )

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["classification"] == "controller-transition"
    assert payload["actions"] == ["controller-observed"]
    assert payload["reasons"] == [
        "controller-operation-pending",
        "controller-no-progress",
    ]
    assert payload["next_action"] == (
        "inspect VM-HA controller service journals on both members, then rerun vm-ha"
    )
    assert result.stderr.count("observing controller-owned recovery") == 1
    assert "✗ observing controller-owned recovery" in result.stderr
    assert inspect.call_count == 2


def test_vm_ha_observes_controller_progress_then_confirms_health(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    transitioning = _inspection(
        _snapshot(
            overall="TRANSITIONING",
            action="wait",
            reasons=("controller-operation-pending",),
        )
    )
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none", digest="b" * 64))
    inspect = Mock(side_effect=(transitioning, healthy, healthy))
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "healthy"
    assert payload["actions"] == ["controller-observed"]
    assert payload["approval"] is None
    assert "Proceed with this exact VM-HA plan?" not in result.stderr
    assert inspect.call_count == 3


def test_vm_ha_interrupted_apply_transaction_plans_resume_without_observer_wait(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    provisioning = _inspection(
        _snapshot(
            overall="TRANSITIONING",
            action="wait",
            reasons=("lifecycle-provisioning",),
            lifecycle_status=VMHALifecycleStatus.PROVISIONING,
        )
    )
    report = _VMHAApplyPlanReport(
        kind="resume-transaction",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("resume-exact-approved-vm-ha-transaction",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    inspect = Mock(return_value=provisioning)
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr(
        "nebius_vpngw.cli._plan_vm_ha_apply_convergence",
        lambda _path: report,
    )
    sleep = Mock(side_effect=AssertionError("apply-owned state entered observer wait"))
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", sleep)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--dry-run",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["classification"] == "vm-ha-required"
    assert payload["approval"]["kind"] == "resume-transaction"
    assert payload["reasons"] == ["apply-transaction-resume-required"]
    assert inspect.call_count == 1
    sleep.assert_not_called()


def test_vm_ha_removed_lifecycle_plans_fresh_provisioning(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    removed = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="inspect",
            reasons=("cloud-member-unavailable",),
            lifecycle_status=VMHALifecycleStatus.REMOVED,
        )
    )
    report = _VMHAApplyPlanReport(
        kind="provisioning",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("provision-warm-standby",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    planner = Mock(return_value=report)
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: removed,
    )
    monkeypatch.setattr("nebius_vpngw.cli._plan_vm_ha_apply_convergence", planner)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--dry-run",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["classification"] == "vm-ha-required"
    assert payload["approval"]["kind"] == "provisioning"
    assert payload["reasons"] == ["apply-provisioning-required"]
    planner.assert_called_once_with(config_path)


def test_vm_ha_rejects_approval_when_current_state_has_no_approval_domain(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none"))
    inspect = Mock(side_effect=(healthy, healthy))
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--approve",
            "f" * 64,
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["reasons"] == ["approval-not-applicable"]
    assert inspect.call_count == 2


def test_vm_ha_rejects_approval_when_conversion_still_needs_input(
    sample_config: dict,
    tmp_path: Path,
) -> None:
    source = tmp_path / "gateway.config.yaml"
    source.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(source),
            "--approve",
            "f" * 64,
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["reasons"] == ["approval-not-applicable"]


def test_vm_ha_missing_cloud_member_plans_exact_non_owner_replacement(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    snapshot = _snapshot(
        overall="BLOCKED",
        action="inspect",
        reasons=("cloud-member-unavailable",),
    )
    snapshot = _VMHAStatusSnapshot(
        view=snapshot.view,
        lifecycle_state=SimpleNamespace(
            status=VMHALifecycleStatus.ACTIVE,
            transaction=SimpleNamespace(
                pending_effect=None,
                accepted_cloud_operation_id=None,
            ),
        ),
        authority=_VMHACloudAuthority(
            lifecycle="active",
            condition="blocked",
            owner_name="gateway-0",
            owner_node_id="node-0",
            operation_id=None,
            reasons=("cloud-member-unavailable",),
            member_compute_states=(("node-0", "running"),),
            unavailable_member_node_ids=("node-1",),
        ),
        members=(
            snapshot.members[0],
            _VMHAMemberEvidence(
                name="gateway-1",
                configured_role="passive",
                node_id="node-1",
                condition="unknown",
                reason="member-address-unavailable",
            ),
        ),
        authority_digest=snapshot.authority_digest,
    )
    report = _VMHAApplyPlanReport(
        kind="active-standby-replacement",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("create-fresh-non-owner-boot-disk-and-compute",),
        has_destructive_changes=False,
        managed_ssh_action=None,
        impact=_vm_ha_apply_plan_impact(
            "active-standby-replacement",
            has_destructive_changes=False,
        ),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: _inspection(snapshot),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._plan_vm_ha_apply_convergence",
        lambda _path: report,
    )

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["classification"] == "vm-ha-required"
    assert payload["reasons"] == ["active-standby-replacement-required"]
    assert payload["approval"]["kind"] == "active-standby-replacement"
    assert payload["impact"]["resource_creation"] is True
    assert payload["impact"]["destructive"] is False
    assert payload["impact"]["vpn_traffic_interruption"] is False
    assert payload["next_action"] == (
        "run nebius-vpngw vm-ha --local-config-file "
        f"{config_path} --approve {'d' * 64} to create the missing non-owner VM"
    )

    text_result = CliRunner().invoke(
        app,
        ["vm-ha", "--local-config-file", str(config_path)],
    )

    assert text_result.exit_code == 3
    assert (
        "Next: run nebius-vpngw vm-ha --local-config-file "
        f"{config_path} --approve {'d' * 64} to create the missing non-owner VM"
        in text_result.stdout
    )
    assert "standby-cloud-resource-unavailable" not in text_result.stdout


@pytest.mark.parametrize(
    "contradictory_reason",
    ("cloud-member-identity-conflict", "route-records-not-exact"),
)
def test_vm_ha_missing_cloud_member_rejects_contradictory_authority(
    contradictory_reason: str,
) -> None:
    snapshot = _snapshot(
        overall="BLOCKED",
        action="inspect",
        reasons=("cloud-member-unavailable", contradictory_reason),
    )
    snapshot = _VMHAStatusSnapshot(
        view=snapshot.view,
        lifecycle_state=SimpleNamespace(
            status=VMHALifecycleStatus.ACTIVE,
            transaction=SimpleNamespace(
                pending_effect=None,
                accepted_cloud_operation_id=None,
            ),
        ),
        authority=_VMHACloudAuthority(
            lifecycle="active",
            condition="blocked",
            owner_name="gateway-0",
            owner_node_id="node-0",
            operation_id=None,
            reasons=("cloud-member-unavailable", contradictory_reason),
            member_compute_states=(("node-0", "running"),),
            unavailable_member_node_ids=("node-1",),
        ),
        members=(
            snapshot.members[0],
            _VMHAMemberEvidence(
                name="gateway-1",
                configured_role="passive",
                node_id="node-1",
                condition="unknown",
                reason="member-address-unavailable",
            ),
        ),
        authority_digest=snapshot.authority_digest,
    )

    assert not cli_module._vm_ha_snapshot_is_missing_non_owner_candidate(snapshot)


def test_vm_ha_failure_is_redacted_and_reports_effective_candidate(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "gateway.config.yaml"
    candidate = tmp_path / "gateway.vm-ha.config.yaml"
    source.write_text("version: 1\n", encoding="utf-8")
    candidate.write_text("version: 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(candidate, ("candidate-reused",)),
    )

    def fail_after_retry(_path: Path) -> _VMHACommandInspection:
        try:
            raise RuntimeError("TRANSIENT_PROVIDER_DETAIL")
        except RuntimeError:
            logging.getLogger("nebius.aio.request").error(
                "request attempt 1 for Request failed but will be retried",
                exc_info=True,
            )
        raise RuntimeError("TOP_SECRET_PROVIDER_DETAIL")

    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        Mock(side_effect=fail_after_retry),
    )

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(source),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["effective_config_file"] == str(candidate)
    assert payload["reasons"] == ["convergence-failed-safely"]
    assert "TOP_SECRET_PROVIDER_DETAIL" not in result.output
    assert "request attempt" not in result.stderr
    assert "TRANSIENT_PROVIDER_DETAIL" not in result.stderr
    assert "Traceback" not in result.stderr
    assert "✗ inspecting authoritative VM-HA state." in result.stderr


@pytest.mark.parametrize(
    ("problem", "reason", "action_fragment"),
    (
        (
            VMHAAgentArtifactProblem.MISSING,
            "agent-artifact-missing",
            "build the current project wheel",
        ),
        (
            VMHAAgentArtifactProblem.AMBIGUOUS,
            "agent-artifact-selection-ambiguous",
            "exactly one current compatible wheel",
        ),
        (
            VMHAAgentArtifactProblem.INCOMPATIBLE,
            "agent-artifact-incompatible",
            "rebuild the agent wheel from the current source",
        ),
        (
            VMHAAgentArtifactProblem.CHANGED,
            "agent-artifact-changed",
            "obtain a new exact plan",
        ),
    ),
)
def test_vm_ha_agent_artifact_prerequisite_is_actionable_and_zero_effect(
    monkeypatch,
    tmp_path: Path,
    problem: VMHAAgentArtifactProblem,
    reason: str,
    action_fragment: str,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    drift = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="repair-route-authority",
            reasons=("route-next-hop-not-exact",),
        )
    )
    planner = Mock(side_effect=VMHAAgentArtifactError(problem, "PRIVATE_ARTIFACT_DETAIL"))
    lock = Mock(side_effect=AssertionError("artifact prerequisite acquired the lock"))
    effect = Mock(side_effect=AssertionError("artifact prerequisite executed apply"))
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: drift,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._plan_vm_ha_apply_convergence",
        planner,
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", lock)
    monkeypatch.setattr(
        "nebius_vpngw.cli._execute_vm_ha_apply_convergence",
        effect,
    )

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "action-required"
    assert payload["classification"] == "external-prerequisite"
    assert payload["reasons"] == [reason]
    assert action_fragment in payload["next_action"]
    assert "PRIVATE_ARTIFACT_DETAIL" not in result.output
    assert "✗ planning exact VM-HA convergence." in result.stderr
    lock.assert_not_called()
    effect.assert_not_called()


def test_vm_ha_replacement_ssh_identity_prerequisite_is_actionable_and_zero_effect(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    drift = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="repair-route-authority",
            reasons=("route-next-hop-not-exact",),
        )
    )
    failure = _VMHAApplyPlanningFailed(
        reason="replacement-ssh-identity-unavailable",
        next_action=(
            "restore the missing non-owner's original private SSH host key "
            "matching its exact pin, then rerun vm-ha"
        ),
    )
    lock = Mock(side_effect=AssertionError("SSH prerequisite acquired the lock"))
    effect = Mock(side_effect=AssertionError("SSH prerequisite executed apply"))
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: drift,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._plan_vm_ha_apply_convergence",
        Mock(side_effect=failure),
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", lock)
    monkeypatch.setattr("nebius_vpngw.cli._execute_vm_ha_apply_convergence", effect)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "action-required"
    assert payload["classification"] == "external-prerequisite"
    assert payload["health"] == "blocked"
    assert payload["reasons"] == ["replacement-ssh-identity-unavailable"]
    assert payload["next_action"] == (
        "restore the missing non-owner's original private SSH host key "
        "matching its exact pin, then rerun vm-ha"
    )
    assert "authentication" not in payload["next_action"]
    lock.assert_not_called()
    effect.assert_not_called()

    text_result = CliRunner().invoke(
        app,
        ["vm-ha", "--local-config-file", str(config_path)],
    )

    assert text_result.exit_code == 3
    assert "Classification: external-prerequisite" in text_result.stdout
    assert "Reasons: replacement-ssh-identity-unavailable" in text_result.stdout
    assert (
        "Next: restore the missing non-owner's original private SSH host key "
        "matching its exact pin, then rerun vm-ha" in text_result.stdout
    )
    assert "authentication-or-provider-unavailable" not in text_result.stdout


def test_vm_ha_artifact_change_after_execution_starts_reports_partial_resume(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    drift = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="repair-route-authority",
            reasons=("route-next-hop-not-exact",),
        )
    )
    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("reconcile-managed-routes-through-apply-owner",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    planner = Mock(side_effect=(report, report))
    effect = Mock(
        side_effect=VMHAAgentArtifactError(
            VMHAAgentArtifactProblem.CHANGED,
            "PRIVATE_ARTIFACT_DETAIL",
        )
    )

    class Lock:
        def __init__(self, **kwargs) -> None:
            assert kwargs == {"project_id": "project-a", "gateway_name": "gateway"}

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: drift,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._plan_vm_ha_apply_convergence",
        planner,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._execute_vm_ha_apply_convergence",
        effect,
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", Lock)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--approve",
            "d" * 64,
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["classification"] == "external-prerequisite"
    assert payload["health"] == "blocked"
    assert payload["actions"] == ["convergence-effects-may-have-started"]
    assert payload["reasons"] == ["agent-artifact-changed-during-convergence"]
    assert "durable checkpoints and resume idempotently" in payload["next_action"]
    assert "gateway changes may already have started" in payload["next_action"]
    assert "PRIVATE_ARTIFACT_DETAIL" not in result.output
    assert planner.call_count == 2
    effect.assert_called_once()


@pytest.mark.parametrize(
    ("failure", "expected_reason", "expected_next_fragment"),
    (
        (
            _VMHAApplyConvergenceFailed("PRIVATE_APPLY_DETAIL"),
            "apply-convergence-interrupted",
            "resume idempotently",
        ),
        (
            _VMHAApplyConvergenceFailed(
                "PRIVATE_APPLY_DETAIL",
                reason="standby-replacement-inhibition-not-ready",
                next_action=(
                    "rerun vm-ha to resume the exact inhibition checkpoint; if it "
                    "times out again, inspect the serving owner's VM-HA controller journal"
                ),
            ),
            "standby-replacement-inhibition-not-ready",
            "exact inhibition checkpoint",
        ),
    ),
)
def test_vm_ha_apply_exit_reports_checkpoint_resume_not_authentication(
    monkeypatch,
    tmp_path: Path,
    failure: _VMHAApplyConvergenceFailed,
    expected_reason: str,
    expected_next_fragment: str,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    drift = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="repair-route-authority",
            reasons=("route-next-hop-not-exact",),
        )
    )
    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("reconcile-managed-routes-through-apply-owner",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    planner = Mock(side_effect=(report, report))

    class Lock:
        def __init__(self, **kwargs) -> None:
            assert kwargs == {"project_id": "project-a", "gateway_name": "gateway"}

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: drift,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._plan_vm_ha_apply_convergence",
        planner,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._execute_vm_ha_apply_convergence",
        Mock(side_effect=failure),
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", Lock)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--approve",
            "d" * 64,
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["health"] == "blocked"
    assert payload["actions"] == ["convergence-effects-may-have-started"]
    assert payload["reasons"] == [expected_reason]
    assert expected_next_fragment in payload["next_action"]
    assert "authentication" not in payload["next_action"]
    assert "PRIVATE_APPLY_DETAIL" not in result.output


def test_vm_ha_authentication_exit_keeps_authentication_guidance(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "gateway.config.yaml"
    source.write_text("version: 1\n", encoding="utf-8")

    def fail_authentication(**_kwargs) -> None:
        raise typer.Exit(code=1) from NebiusCLIAuthenticationError("redacted")

    monkeypatch.setattr("nebius_vpngw.cli._resolve_vm_ha_effective_config", fail_authentication)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(source),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["reasons"] == ["authentication-or-provider-unavailable"]
    assert payload["next_action"] == "restore authentication and rerun vm-ha"


def test_vm_ha_bare_exit_is_not_reported_as_authentication(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "gateway.config.yaml"
    source.write_text("version: 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        Mock(side_effect=typer.Exit(code=1)),
    )

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(source),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["reasons"] == ["command-preflight-failed"]
    assert "authentication" not in payload["next_action"]


def test_vm_ha_interruption_returns_130_with_stable_json(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "gateway.config.yaml"
    source.write_text("version: 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        Mock(side_effect=KeyboardInterrupt),
    )

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(source),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 130
    payload = json.loads(result.stdout)
    assert payload["schema"] == "nebius-vpngw/vm-ha-result-v1"
    assert payload["reasons"] == ["interrupted"]


@pytest.mark.parametrize("extra_args", ((), ("--output-format", "json")))
def test_vm_ha_dry_run_emits_domain_bound_apply_plan_without_prompting(
    monkeypatch,
    tmp_path: Path,
    extra_args: tuple[str, ...],
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    drift = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="repair-route-authority",
            reasons=("route-next-hop-not-exact",),
        )
    )
    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("reconcile-managed-routes-through-apply-owner",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: drift,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._plan_vm_ha_apply_convergence",
        lambda _path: report,
    )
    monkeypatch.setattr("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", lambda: True)
    effect = Mock(side_effect=AssertionError("dry-run executed apply"))
    monkeypatch.setattr("nebius_vpngw.cli._execute_vm_ha_apply_convergence", effect)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--dry-run",
            *extra_args,
        ],
        input="y\n",
    )

    assert result.exit_code == 0
    assert "Proceed with this exact VM-HA plan?" not in result.stderr
    if extra_args:
        payload = json.loads(result.stdout)
        assert payload["outcome"] == "planned"
        assert payload["classification"] == "vm-ha-required"
        assert payload["approval"] == {
            "kind": "apply-convergence",
            "digest": "d" * 64,
            "effects": ["reconcile-managed-routes-through-apply-owner"],
        }
    else:
        assert "VM-HA plan is ready; no changes were made." in result.stdout
    effect.assert_not_called()


@pytest.mark.parametrize("extra_args", ((), ("--output-format", "json")))
def test_vm_ha_explicitly_safe_plan_executes_without_confirmation(
    monkeypatch,
    tmp_path: Path,
    extra_args: tuple[str, ...],
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    drift = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="repair-route-authority",
            reasons=("route-next-hop-not-exact",),
        )
    )
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none", digest="b" * 64))
    inspect = Mock(side_effect=(drift, healthy, healthy))
    report = _VMHAApplyPlanReport(
        kind="safe-maintenance",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("verify-current-state",),
        has_destructive_changes=False,
        managed_ssh_action=None,
        impact=VMHACommandImpact(
            summary="No VPN traffic interruption or destructive changes are expected",
            destructive=False,
            vpn_traffic_interruption=False,
            resource_creation=False,
        ),
    )
    planner = Mock(side_effect=(report, report))
    effect = Mock()

    class Lock:
        def __init__(self, **kwargs) -> None:
            assert kwargs == {"project_id": "project-a", "gateway_name": "gateway"}

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr("nebius_vpngw.cli._plan_vm_ha_apply_convergence", planner)
    monkeypatch.setattr("nebius_vpngw.cli._execute_vm_ha_apply_convergence", effect)
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", Lock)
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", lambda: True)

    result = CliRunner().invoke(
        app,
        ["vm-ha", "--local-config-file", str(config_path), *extra_args],
        input="n\n",
    )

    assert result.exit_code == 0
    assert "Proceed with this exact VM-HA plan?" not in result.stderr
    if extra_args:
        payload = json.loads(result.stdout)
        assert payload["outcome"] == "healthy"
        assert result.stdout.count("\n") == 1
    else:
        assert result.stdout == "VM-HA is healthy now.\n"
    assert planner.call_count == 2
    effect.assert_called_once()
    assert effect.call_args.args == (config_path, report)
    assert inspect.call_count == 3


def test_vm_ha_disruptive_artifact_recovery_warns_and_requires_confirmation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    drift = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="repair-route-authority",
            reasons=("route-next-hop-not-exact",),
        )
    )
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none", digest="b" * 64))
    inspect = Mock(side_effect=(drift, drift, healthy, healthy))
    report = _VMHAApplyPlanReport(
        kind="artifact-standby-recovery",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=(
            "install-approved-artifact-on-serving-owner",
            "verify-owner-routes-forwarding-and-standby",
        ),
        has_destructive_changes=False,
        managed_ssh_action=None,
        impact=_vm_ha_apply_plan_impact(
            "artifact-standby-recovery",
            has_destructive_changes=False,
        ),
    )
    planner = Mock(side_effect=(report, report))
    effect = Mock()

    class Lock:
        def __init__(self, **kwargs) -> None:
            assert kwargs == {"project_id": "project-a", "gateway_name": "gateway"}

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr("nebius_vpngw.cli._plan_vm_ha_apply_convergence", planner)
    monkeypatch.setattr("nebius_vpngw.cli._execute_vm_ha_apply_convergence", effect)
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", Lock)
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", lambda: True)

    result = CliRunner().invoke(
        app,
        ["vm-ha", "--local-config-file", str(config_path)],
        input="y\n",
    )

    assert result.exit_code == 0
    assert "VM-HA is healthy now." in result.stdout
    assert "Approval kind: artifact-standby-recovery" in result.stderr
    assert f"Approval digest: {'d' * 64}" in result.stderr
    assert (
        "Impact: May briefly interrupt VPN traffic while the serving owner is upgraded; "
        "no gateway VM or disk is deleted."
    ) in result.stderr
    assert "install approved artifact on serving owner" in result.stderr
    assert "Proceed with this exact VM-HA plan? [y/N]" in result.stderr
    assert result.stderr.index("Planned effects:") < result.stderr.index("Proceed with")
    assert planner.call_count == 2
    effect.assert_called_once()
    assert effect.call_args.args == (config_path, report)
    assert callable(effect.call_args.kwargs["progress_sink"])
    assert inspect.call_count == 4


def test_vm_ha_missing_non_owner_confirms_and_executes_in_one_interactive_command(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    blocked = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="repair-route-authority",
            reasons=("route-next-hop-not-exact",),
        )
    )
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none", digest="b" * 64))
    inspect = Mock(side_effect=(blocked, blocked, healthy, healthy))
    report = _VMHAApplyPlanReport(
        kind="active-standby-replacement",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("create-fresh-non-owner-boot-disk-and-compute",),
        has_destructive_changes=False,
        managed_ssh_action=None,
        impact=_vm_ha_apply_plan_impact(
            "active-standby-replacement",
            has_destructive_changes=False,
        ),
    )
    planner = Mock(side_effect=(report, report))
    effect = Mock()

    class Lock:
        def __init__(self, **kwargs) -> None:
            assert kwargs == {"project_id": "project-a", "gateway_name": "gateway"}

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr("nebius_vpngw.cli._plan_vm_ha_apply_convergence", planner)
    monkeypatch.setattr("nebius_vpngw.cli._execute_vm_ha_apply_convergence", effect)
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", Lock)
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", lambda: True)

    result = CliRunner().invoke(
        app,
        ["vm-ha", "--local-config-file", str(config_path)],
        input="y\n",
    )

    assert result.exit_code == 0
    assert "Create the missing non-owner VM now? [y/N]" in result.stderr
    assert "Approval digest:" not in result.stderr
    assert "Artifact SHA-256:" not in result.stderr
    assert "serving owner is not restarted" in result.stderr
    effect.assert_called_once()
    assert effect.call_args.args == (config_path, report)


def test_vm_ha_missing_non_owner_decline_hides_automation_digest(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    blocked = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="repair-route-authority",
            reasons=("route-next-hop-not-exact",),
        )
    )
    report = _VMHAApplyPlanReport(
        kind="active-standby-replacement",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("create-fresh-non-owner-boot-disk-and-compute",),
        has_destructive_changes=False,
        managed_ssh_action=None,
        impact=_vm_ha_apply_plan_impact(
            "active-standby-replacement",
            has_destructive_changes=False,
        ),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: blocked,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._plan_vm_ha_apply_convergence",
        lambda _path: report,
    )
    monkeypatch.setattr("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", lambda: True)

    result = CliRunner().invoke(
        app,
        ["vm-ha", "--local-config-file", str(config_path)],
        input="n\n",
    )

    assert result.exit_code == 3
    assert "Create the missing non-owner VM now? [y/N]" in result.stderr
    assert "Approval digest:" not in result.output
    assert "--approve" not in result.output
    assert "rerun vm-ha and answer y" in result.stdout


@pytest.mark.parametrize("answer", ("\n", "n\n"))
def test_vm_ha_interactive_approval_defaults_no_and_performs_zero_effects(
    monkeypatch,
    tmp_path: Path,
    answer: str,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    drift = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="repair-route-authority",
            reasons=("route-next-hop-not-exact",),
        )
    )
    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("reconcile-managed-routes-through-apply-owner",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    lock = Mock(side_effect=AssertionError("declined approval acquired the lock"))
    effect = Mock(side_effect=AssertionError("declined approval executed apply"))
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: drift,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._plan_vm_ha_apply_convergence",
        lambda _path: report,
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", lock)
    monkeypatch.setattr("nebius_vpngw.cli._execute_vm_ha_apply_convergence", effect)
    monkeypatch.setattr("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", lambda: True)

    result = CliRunner().invoke(
        app,
        ["vm-ha", "--local-config-file", str(config_path)],
        input=answer,
    )

    assert result.exit_code == 3
    assert "operator-declined-approval" in result.stdout
    assert "Proceed with this exact VM-HA plan? [y/N]" in result.stderr
    lock.assert_not_called()
    effect.assert_not_called()


def test_vm_ha_destructive_plan_reports_impact_and_never_auto_executes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    drift = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="repair-route-authority",
            reasons=("route-next-hop-not-exact",),
        )
    )
    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("recreate-gateway-compute",),
        has_destructive_changes=True,
        managed_ssh_action=None,
        impact=_vm_ha_apply_plan_impact(
            "apply-convergence",
            has_destructive_changes=True,
        ),
    )
    lock = Mock(side_effect=AssertionError("destructive plan acquired the lock"))
    effect = Mock(side_effect=AssertionError("destructive plan executed apply"))
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: drift,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._plan_vm_ha_apply_convergence",
        lambda _path: report,
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", lock)
    monkeypatch.setattr("nebius_vpngw.cli._execute_vm_ha_apply_convergence", effect)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 3
    payload = json.loads(result.stdout)
    assert payload["reasons"] == ["destructive-gateway-recreation-required"]
    assert payload["approval"] is None
    assert payload["impact"] == {
        "summary": "Deletes and recreates gateway VM resources and may interrupt VPN traffic",
        "destructive": True,
        "vpn_traffic_interruption": True,
        "resource_creation": True,
        "approval_required": True,
    }
    lock.assert_not_called()
    effect.assert_not_called()


def test_vm_ha_interactive_approval_eof_returns_130_without_effects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    drift = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="repair-route-authority",
            reasons=("route-next-hop-not-exact",),
        )
    )
    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("reconcile-managed-routes-through-apply-owner",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    lock = Mock(side_effect=AssertionError("interrupted approval acquired the lock"))
    effect = Mock(side_effect=AssertionError("interrupted approval executed apply"))
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: drift,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._plan_vm_ha_apply_convergence",
        lambda _path: report,
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", lock)
    monkeypatch.setattr("nebius_vpngw.cli._execute_vm_ha_apply_convergence", effect)
    monkeypatch.setattr("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", lambda: True)

    result = CliRunner().invoke(
        app,
        ["vm-ha", "--local-config-file", str(config_path)],
        input="",
    )

    assert result.exit_code == 130
    assert "Reasons: interrupted" in result.stdout
    assert "Proceed with this exact VM-HA plan? [y/N]" in result.stderr
    lock.assert_not_called()
    effect.assert_not_called()


def test_vm_ha_json_approval_required_never_prompts_and_keeps_stdout_pure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    drift = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="repair-route-authority",
            reasons=("route-next-hop-not-exact",),
        )
    )
    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("reconcile-managed-routes-through-apply-owner",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: drift,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._plan_vm_ha_apply_convergence",
        lambda _path: report,
    )
    monkeypatch.setattr("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", lambda: True)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--output-format",
            "json",
        ],
        input="y\n",
    )

    assert result.exit_code == 3
    assert json.loads(result.stdout)["outcome"] == "action-required"
    assert result.stdout.count("\n") == 1
    assert "Starting:" not in result.stdout
    assert "Proceed with this exact VM-HA plan?" not in result.stderr
    assert result.stderr.count("planning exact VM-HA convergence") == 1
    assert "✓ planning exact VM-HA convergence" in result.stderr


@pytest.mark.parametrize("extra_args", ((), ("--output-format", "json")))
def test_vm_ha_exact_apply_approval_replans_under_lock_and_never_prompts(
    monkeypatch,
    tmp_path: Path,
    extra_args: tuple[str, ...],
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    drift = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="repair-route-authority",
            reasons=("route-next-hop-not-exact",),
        )
    )
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none", digest="b" * 64))
    inspect = Mock(side_effect=(drift, healthy, healthy))
    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("reconcile-managed-routes-through-apply-owner",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    planner = Mock(side_effect=(report, report))
    effect = Mock()

    class Lock:
        def __init__(self, **kwargs) -> None:
            assert kwargs == {"project_id": "project-a", "gateway_name": "gateway"}

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr("nebius_vpngw.cli._plan_vm_ha_apply_convergence", planner)
    monkeypatch.setattr("nebius_vpngw.cli._execute_vm_ha_apply_convergence", effect)
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", Lock)
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", lambda: True)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--approve",
            "d" * 64,
            *extra_args,
        ],
        input="n\n",
    )

    assert result.exit_code == 0
    assert "Proceed with this exact VM-HA plan?" not in result.stderr
    if extra_args:
        payload = json.loads(result.stdout)
        assert payload["outcome"] == "healthy"
        assert payload["actions"] == ["apply-converged"]
    else:
        assert "VM-HA is healthy now." in result.stdout
    assert result.stderr.count("revalidating the exact approved plan") == 1
    assert "✓ revalidating the exact approved plan" in result.stderr
    assert planner.call_count == 2
    effect.assert_called_once()
    assert effect.call_args.args == (config_path, report)
    assert callable(effect.call_args.kwargs["progress_sink"])
    assert inspect.call_count == 3


def test_vm_ha_stale_apply_approval_fails_before_lock_or_effect(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    drift = _inspection(
        _snapshot(
            overall="BLOCKED",
            action="repair-route-authority",
            reasons=("route-next-hop-not-exact",),
        )
    )
    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("reconcile-managed-routes-through-apply-owner",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._resolve_vm_ha_effective_config",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path: drift,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._plan_vm_ha_apply_convergence",
        lambda _path: report,
    )
    lock = Mock(side_effect=AssertionError("stale approval acquired lock"))
    effect = Mock(side_effect=AssertionError("stale approval executed apply"))
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", lock)
    monkeypatch.setattr("nebius_vpngw.cli._execute_vm_ha_apply_convergence", effect)

    result = CliRunner().invoke(
        app,
        [
            "vm-ha",
            "--local-config-file",
            str(config_path),
            "--approve",
            "f" * 64,
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["reasons"] == ["approval-digest-stale-or-incorrect"]
    lock.assert_not_called()
    effect.assert_not_called()
