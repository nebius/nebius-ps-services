from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import subprocess
import sys
import zipfile
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import paramiko
import pytest
import typer
import yaml
from click import unstyle
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
    _VMHAAgentPackagePreparationFailed,
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


@pytest.fixture(autouse=True)
def isolated_package_recovery_root(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "nebius_vpngw.deploy.package_recovery.lifecycle_root",
        lambda _state: tmp_path / "package-command",
    )


@pytest.fixture
def verified_ordinary_conversion(monkeypatch):
    """These HA UI cases begin after the ordinary owner verified its phase."""
    monkeypatch.setattr(
        "nebius_vpngw.deploy.conversion_prerequisite.run", lambda *args, **kwargs: False
    )


@pytest.fixture
def ha_apply_admission(monkeypatch):
    """Isolate existing HA orchestration oracles from schema/cloud preflight.

    These tests provide typed inspection and effect-owner doubles. The public
    apply integration suite separately exercises actual YAML admission and locks.
    """
    monkeypatch.setattr(
        cli_module,
        "load_local_config",
        lambda *a, **kw: {
            "project_id": "project-a",
            "region_id": "eu-west1",
            "gateway_group": {"name": "gateway", "vm_ha": {"enabled": True}},
        },
    )
    monkeypatch.setattr(
        cli_module,
        "merge_with_peer_configs",
        lambda *a, **kw: SimpleNamespace(
            vm_ha=object(),
            gateway_group=SimpleNamespace(name="gateway", region="eu-west1"),
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "_plan_vm_ha_apply_convergence",
        lambda *a, **kw: _VMHAApplyPlanReport(
            kind="unchanged",
            digest="f" * 64,
            engine_digest="f" * 64,
            effects=(),
            has_destructive_changes=False,
            managed_ssh_action=None,
            impact=VMHACommandImpact("Verified unchanged", False, False, False),
        ),
    )


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
            b'LIVE_PEER_REPLACEMENT_CAPABILITY = "vm-ha-live-peer-replacement-v5"\n'
        ),
        f"{dist_info}/METADATA": b"Metadata-Version: 2.4\nName: nebius-vpngw\nVersion: 1.2.3\n",
        f"{dist_info}/WHEEL": (b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"),
    }
    for asset_name in (
        "nebius-vpngw-agent.service",
        "nebius-vpngw-agent-ordering.conf",
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
    source_root = Path(__file__).parents[2] / "src/nebius_vpngw"
    for source in source_root.rglob("*"):
        if source.is_file() and "__pycache__" not in source.parts:
            members["nebius_vpngw/" + source.relative_to(source_root).as_posix()] = (
                source.read_bytes()
            )
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
        "schema": "nebius-vpngw/vm-ha-auto-healing-policy-status-v3",
        "evidence_reason": None,
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
            "schema": "nebius-vpngw/vm-ha-auto-healing-policy-status-v3",
            "evidence_reason": None,
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


@pytest.mark.parametrize(
    ("owner_refresh_required", "package_fails"), ((False, False), (True, False), (True, True))
)
def test_missing_standby_orchestration_inhibits_owner_before_create(
    monkeypatch: pytest.MonkeyPatch,
    owner_refresh_required: bool,
    package_fails: bool,
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
        instance_index=0,
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
            if package_fails:
                raise paramiko.AuthenticationException("TOP_SECRET")

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

    def run():
        return _create_missing_vm_ha_standby_under_owner_inhibition(
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

    if package_fails:
        with pytest.raises(_VMHAAgentPackagePreparationFailed) as failed:
            run()
        assert failed.value.reason == "package-preparation-failed-member-1"
        assert "authentication failed" in failed.value.next_action
        assert "TOP_SECRET" not in str(failed.value)
        assert trace[-1] == "package-owner"
        assert not {"refresh-owner", "inhibit-owner", "create-missing-target"}.intersection(trace)
        return
    observed = run()

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
        ("authorization-failed", "runtime-credential-authorization-failed"),
        ("provider-timeout", "runtime-credential-provider-timeout"),
        ("provider-unavailable", "runtime-credential-provider-unavailable"),
        ("dns-unavailable", "runtime-credential-dns-unavailable"),
        ("identity-unverified", "runtime-credential-identity-unverified"),
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
    assert callable(observed["vm_ha_progress_sink"])
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
            "evidence_reason": None,
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
            "evidence_reason": None,
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


@pytest.mark.parametrize(
    "failed_target",
    (
        None,
        "owner-target",
        "standby-target",
        "wire-break",
        "standby-wire-break",
        "owner-refresh-disconnect",
    ),
)
def test_artifact_recovery_upgrades_owner_before_rearm_and_reuses_artifact(
    monkeypatch,
    tmp_path: Path,
    failed_target: str | None,
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
        instance_index=0,
        vm_ha_node=SimpleNamespace(node_id="node-0"),
    )
    standby = SimpleNamespace(
        hostname="gateway-1",
        instance_index=1,
        vm_ha_node=SimpleNamespace(node_id="node-1"),
    )
    context = _VMHAArtifactRecoveryContext(
        local_config={"gateway_group": {"vm_spec": {}}},
        plan=SimpleNamespace(),  # type: ignore[arg-type]
        lifecycle=SimpleNamespace(record_sha256="f" * 64),  # type: ignore[arg-type]
        owner_instance=owner,
        standby_instance=standby,
        owner_role="active",
        standby_role="passive",
        owner_target="owner-target",
        standby_target="standby-target",
        ssh_policy=object(),  # type: ignore[arg-type]
        owner_record={
            "controller_capabilities": []
            if failed_target == "wire-break"
            else ["independent-heartbeat-v6"]
        },
    )
    object.__setattr__(
        context,
        "recovery_record",
        {
            "schema": "nebius-vpngw.artifact-standby-recovery.v1",
            "binding": {"lifecycle": "f" * 64},
            "report": {"digest": report.digest},
            "phase": "owner",
        },
    )
    monkeypatch.setattr(
        "nebius_vpngw.deploy.package_recovery.lifecycle_root", lambda _s: tmp_path / "command"
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
            if target == failed_target:
                raise EOFError("TOP_SECRET_REMOTE_DETAIL")

        def require_vm_ha_heartbeat_v6(self, target, *_args):
            assert target == "standby-target"
            if failed_target == "standby-wire-break":
                raise RuntimeError("heartbeat wire upgrade requires coordinated two-member apply")

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
            "evidence_reason": None,
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
        if failed_target == "owner-refresh-disconnect":
            raise RuntimeError("observer disconnected after owner refresh")
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

    if failed_target == "owner-refresh-disconnect":
        from nebius_vpngw.deploy import artifact_standby_recovery as receipts

        with pytest.raises(RuntimeError, match="observer disconnected"):
            _execute_vm_ha_artifact_standby_recovery(
                config_path, report, region=None, progress_sink=None
            )
        saved = receipts.read(tmp_path / "command")
        assert saved["phase"] == "owner-ready"
        object.__setattr__(context, "recovery_record", saved)
        context.owner_record["controller_capabilities"].append("vm-ha-standby-restoration-v2")
        failed_target = None

    if failed_target == "standby-wire-break":
        with pytest.raises(RuntimeError, match="coordinated two-member apply"):
            _execute_vm_ha_artifact_standby_recovery(
                config_path, report, region=None, progress_sink=None
            )
        assert "install:standby-target" not in order
        assert "refresh:standby-target" not in order
        assert "canonical-apply" not in order
        return
    if failed_target == "wire-break":
        with pytest.raises(RuntimeError, match="coordinated two-member apply"):
            _execute_vm_ha_artifact_standby_recovery(
                config_path, report, region=None, progress_sink=None
            )
        assert not order
        return
    if failed_target is not None:
        with pytest.raises(_VMHAAgentPackagePreparationFailed) as failed:
            _execute_vm_ha_artifact_standby_recovery(
                config_path, report, region=None, progress_sink=None
            )
        member = 1 if failed_target == "owner-target" else 2
        assert failed.value.reason == f"package-preparation-failed-member-{member}"
        assert "connection closed unexpectedly" in failed.value.next_action
        assert "TOP_SECRET" not in str(failed.value)
        assert order[-1] == f"install:{failed_target}"
        assert f"refresh:{failed_target}" not in order
        assert "canonical-apply" not in order
        return

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

    with pytest.raises(RuntimeError, match="VM-HA activation interrupted"):
        _execute_vm_ha_apply_convergence(tmp_path / "gateway.config.yaml", report)


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("replay", [False, True])
def test_vm_ha_apply_executor_preserves_artifact_failure_from_apply_exit(
    monkeypatch,
    tmp_path: Path,
    wrapped: bool,
    replay: bool,
) -> None:
    from nebius_vpngw.replay_window import ReplayWindowCapabilityError

    report = _VMHAApplyPlanReport(
        kind="apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("typed-effect",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    artifact_error = (
        ReplayWindowCapabilityError("PRIVATE_ARTIFACT_DETAIL")
        if replay
        else VMHAAgentArtifactError(
            VMHAAgentArtifactProblem.CHANGED,
            "PRIVATE_ARTIFACT_DETAIL",
        )
    )

    def fail_package(**_kwargs) -> None:
        if wrapped:
            raise typer.Exit(code=1) from artifact_error
        raise artifact_error

    monkeypatch.setattr("nebius_vpngw.cli._apply_impl", fail_package)

    with pytest.raises(type(artifact_error)) as raised:
        _execute_vm_ha_apply_convergence(tmp_path / "gateway.config.yaml", report)
    assert raised.value is artifact_error


@pytest.mark.parametrize(
    "origin",
    ("apply-exit", "owner-refresh", "artifact-recovery", "recovery-exit", "recovery-error"),
)
def test_vm_ha_executor_preserves_member_bound_package_failure(monkeypatch, tmp_path, origin):
    report = _VMHAApplyPlanReport(
        kind="artifact-standby-recovery" if origin == "artifact-recovery" else "apply-convergence",
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("typed-effect",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )
    error = _VMHAAgentPackagePreparationFailed(2, "VM-HA exact agent package installation failed")

    def fail(*_args, **_kwargs):
        if origin.startswith("recovery-"):
            try:
                raise RuntimeError("PRIVATE_RECOVERY_DETAIL") from error
            except RuntimeError as wrapped:
                if origin == "recovery-exit":
                    raise typer.Exit(code=1) from wrapped
                raise
        if origin == "apply-exit":
            raise typer.Exit(code=1) from error
        raise error

    monkeypatch.setattr(
        cli_module,
        "_execute_vm_ha_artifact_standby_recovery"
        if origin == "artifact-recovery"
        else "_apply_impl",
        fail,
    )
    with pytest.raises(_VMHAAgentPackagePreparationFailed) as raised:
        _execute_vm_ha_apply_convergence(tmp_path / "gateway.config.yaml", report)
    assert raised.value is error
    assert raised.value.reason == "package-preparation-failed-member-2"
    assert "exact agent package installation failed" in raised.value.next_action
    assert "PRIVATE_RECOVERY_DETAIL" not in str(raised.value)


def test_apply_projects_owner_refresh_package_failure_without_traceback(monkeypatch, tmp_path):
    path = tmp_path / "gateway.yaml"
    path.write_text("version: 1\n")
    error = _VMHAAgentPackagePreparationFailed(1, "SSH package preparation authentication failed")
    monkeypatch.setattr(cli_module, "_apply_impl", Mock(side_effect=error))
    monkeypatch.setattr(cli_module, "load_local_config", lambda *_a, **_k: {})
    monkeypatch.setattr(cli_module, "merge_with_peer_configs", lambda *_a: SimpleNamespace())
    result = CliRunner().invoke(app, ["apply", "--local-config-file", str(path)])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Package preparation failed on member 1" in result.output
    assert "SSH package preparation authentication failed" in result.output
    assert result.output.count("Reason:") == 1


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
    verified_ordinary_conversion,
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

    monkeypatch.setattr(
        cli_module, "_ordinary_vm_ha_conversion_trust_prerequisite", lambda *a, **kw: None
    )

    result = CliRunner().invoke(app, ["apply", "--convert-to-ha", "-c", str(source)])

    assert result.exit_code == 0, result.output
    assert source.read_text(encoding="utf-8") == original
    assert not destination.exists()
    reserve.assert_not_called()
    assert [event[0] for event in status_events] == ["start", "stop"]


def test_vm_ha_interactive_conversion_interrupt_has_no_dangling_progress_or_effects(
    monkeypatch,
    sample_config: dict,
    tmp_path: Path,
    verified_ordinary_conversion,
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

    result = CliRunner().invoke(app, ["apply", "--convert-to-ha", "-c", str(source)])

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
    verified_ordinary_conversion,
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
            "apply",
            "--convert-to-ha",
            "--local-config-file",
            str(source),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ha"]["classification"] == "conversion-required"
    assert payload["ha"]["reasons"] == ["conversion-input-required"]
    assert payload["ha"]["actions"] == ["ordinary-convergence-verified"]
    assert payload["ha"]["failover_tested"] is False
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
            "apply",
            "--local-config-file",
            str(config_path),
            "--standby-auto-healing",
            "sometimes",
        ],
    )

    assert result.exit_code == 2
    assert "invalid-arguments" in unstyle(result.output)
    authenticate.assert_not_called()


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: healthy,
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
            "apply",
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
    assert payload["ha"]["outcome"] == "planned"
    assert payload["ha"]["classification"] == "maintenance-policy"
    assert payload["ha"]["impact"] == {
        "summary": "No VPN traffic interruption or destructive changes are expected",
        "destructive": False,
        "vpn_traffic_interruption": False,
        "resource_creation": False,
        "approval_required": False,
    }
    assert payload["ha"]["approval"] == {
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


@pytest.mark.usefixtures("ha_apply_admission")
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
        "schema": "nebius-vpngw/vm-ha-auto-healing-policy-status-v3",
        "evidence_reason": None,
    }
    lock = Mock(side_effect=AssertionError("dry-run acquired the writer lock"))
    effect = Mock(side_effect=AssertionError("dry-run changed the policy"))

    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: blocked,
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
            "apply",
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
    assert payload["ha"]["outcome"] == "planned"
    assert payload["ha"]["classification"] == "maintenance-policy"
    assert payload["ha"]["approval"]["effects"] == [
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
@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: blocked,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_action",
        policy_action,
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", lock)

    result = CliRunner().invoke(
        app,
        [
            "apply",
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
    assert payload["ha"]["outcome"] == "planned"
    assert payload["ha"]["classification"] == "maintenance-policy"
    assert payload["ha"]["approval"]["kind"] == "standby-auto-healing-policy"
    assert payload["ha"]["approval"]["digest"] != "d" * 64
    assert payload["ha"]["approval"]["effects"] == [
        *start_effects,
        "wait-for-restored-standby-readiness",
        "initialize-restored-peer-policy",
        "wait-for-accepted-start-quiescence",
        "verify-two-member-policy-agreement",
    ]
    assert policy_action.call_count == 2
    lock.assert_not_called()


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
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
            "apply",
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
    assert payload["ha"]["outcome"] == "healthy"
    assert payload["ha"]["actions"] == ["standby-auto-healing-enabled"]
    assert prepare.call_count == 1
    before_rearm_request = prepare.call_args.kwargs["before_rearm_request"]
    before_rearm_request("node-0", "node-1", "revision-b")
    arm.assert_called_once()
    execute.assert_called_once()
    assert inspect.call_count == 6


@pytest.mark.usefixtures("ha_apply_admission")
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
    lock = Mock(return_value=_ContextManagedFake())
    effect = Mock(side_effect=AssertionError("bare status attempted recovery"))

    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: blocked,
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", lock)
    monkeypatch.setattr("nebius_vpngw.cli._prepare_vm_ha_planned_target", effect)

    payloads = []
    for _ in range(2):
        result = CliRunner().invoke(
            app,
            [
                "apply",
                "--local-config-file",
                str(config_path),
                "--output-format",
                "json",
            ],
        )
        assert result.exit_code == 1
        payloads.append(json.loads(result.stdout))

    assert payloads[0] == payloads[1]
    assert payloads[0]["ha"]["classification"] == "maintenance-policy"
    assert payloads[0]["ha"]["reasons"] == ["standby-auto-healing-policy-invalid"]
    assert payloads[0]["ha"]["next_action"] == (
        f"run nebius-vpngw apply --local-config-file {config_path} --standby-auto-healing enabled"
    )
    assert lock.call_count == 2
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

    monkeypatch.setattr("nebius_vpngw.cli._read_vm_ha_replacement_policy", lambda *_args: owner)

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

    monkeypatch.setattr("nebius_vpngw.cli._read_vm_ha_replacement_policy", lambda *_args: owner)

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


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
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
            "apply",
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
    assert payload["ha"]["outcome"] == "maintenance"
    assert payload["ha"]["actions"] == ["standby-auto-healing-disabled"]
    assert payload["ha"]["reasons"] == ["maintenance-ready"]
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
@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
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
            "apply",
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
    assert payload["ha"]["reasons"] == ["convergence-failed-safely"]
    execute.assert_called_once()
    assert inspect.call_count == 4


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: healthy,
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
            "apply",
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
    assert payload["ha"]["outcome"] == "healthy"
    assert payload["ha"]["actions"] == [
        "standby-auto-healing-recovery-cleared",
        "standby-auto-healing-already-enabled",
    ]
    cleanup.assert_called_once_with(
        config_path=config_path,
        statuses=statuses,
    )


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: healthy,
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
            "apply",
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
    assert payload["ha"]["outcome"] == "healthy"
    assert payload["ha"]["actions"] == [
        "standby-auto-healing-recovery-cleared",
        "standby-auto-healing-already-enabled",
    ]
    planner.assert_not_called()


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: healthy,
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
            "apply",
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
    assert payload["ha"]["classification"] == "maintenance-policy"
    assert payload["ha"]["health"] == "blocked"
    assert payload["ha"]["reasons"] == ["standby-auto-healing-recovery-cleanup-required"]
    assert payload["ha"]["next_action"] == (
        "inspect VM-HA agent journals for the recovery owner, then rerun the same "
        "requested apply command"
    )
    assert "private cleanup detail" not in result.stdout
    planner.assert_not_called()


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: healthy,
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
            "apply",
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
    assert payload["ha"]["classification"] == "maintenance-policy"
    assert payload["ha"]["reasons"] == ["standby-auto-healing-recovery-cleanup-required"]
    cleanup.assert_not_called()
    planner.assert_not_called()


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: healthy,
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
            "apply",
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
    assert payload["ha"]["outcome"] == "planned"
    assert payload["ha"]["actions"] == ["standby-auto-healing-already-enabled"]
    assert payload["ha"]["approval"]["effects"] == [
        "clear-completed-recovery",
        "verify-two-member-policy-agreement",
        "confirm-standby-auto-healing-already-enabled",
    ]
    cleanup.assert_not_called()


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: healthy,
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
            "apply",
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
    assert payload["ha"]["outcome"] == "planned"
    assert payload["ha"]["actions"] == ["standby-auto-healing-disable"]
    assert payload["ha"]["approval"]["effects"][0] == "clear-completed-recovery"
    assert payload["ha"]["approval"]["effects"][1].startswith("prepare-disable-on-coordinator-")
    cleanup.assert_not_called()


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: healthy,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_statuses",
        lambda **_kwargs: current_statuses[0],
    )

    clean_plan = CliRunner().invoke(
        app,
        [
            "apply",
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
            "apply",
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
    clean_digest = json.loads(clean_plan.stdout)["ha"]["approval"]["digest"]
    cleanup_payload = json.loads(cleanup_plan.stdout)
    assert cleanup_payload["ha"]["approval"]["digest"] != clean_digest
    assert cleanup_payload["ha"]["approval"]["effects"][0] == "clear-completed-recovery"


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: healthy,
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
            "apply",
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
    clean_digest = json.loads(clean_plan.stdout)["ha"]["approval"]["digest"]
    current_statuses[0] = recovery_statuses

    result = CliRunner().invoke(
        app,
        [
            "apply",
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
    assert payload["ha"]["reasons"] == ["approval-digest-stale-or-incorrect"]
    cleanup.assert_not_called()


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
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
            "apply",
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
    assert payload["ha"]["outcome"] == "maintenance"
    assert payload["ha"]["actions"] == [
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


@pytest.mark.usefixtures("ha_apply_admission")
def test_vm_ha_healthy_requires_two_agreeing_fresh_samples(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none"))

    def inspect_with_retry(_path: Path, **_kwargs) -> _VMHACommandInspection:
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ("candidate-reused",)),
    )
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)

    result = CliRunner().invoke(
        app,
        [
            "apply",
            "--local-config-file",
            str(config_path),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ha"]["outcome"] == "healthy"
    assert payload["ha"]["actions"] == ["candidate-reused"]
    assert inspect.call_count == 2
    assert "request attempt" not in result.stderr
    assert "TRANSIENT_PROVIDER_DETAIL" not in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.usefixtures("ha_apply_admission")
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
        **_kwargs,
    ) -> _VMHACommandInspection:
        observed_regions.append(region)
        return healthy

    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)

    result = CliRunner().invoke(
        app,
        [
            "apply",
            "--local-config-file",
            str(config_path),
            "--region",
            "eu-north1",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["ha"]["outcome"] == "healthy"
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
        gateway_group=SimpleNamespace(security_group_ids=(), region="eu-north1"),
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


@pytest.mark.usefixtures("ha_apply_admission")
def test_vm_ha_dry_run_plans_rearm_without_lock_or_effect(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    degraded = _inspection(
        _snapshot(
            overall="DEGRADED",
            action="nebius-vpngw apply --local-config-file fixture",
            reasons=("standby-not-ready",),
            member_compute_states=(("node-0", "running"), ("node-1", "stopped")),
        )
    )
    lock = Mock(side_effect=AssertionError("dry-run acquired a lock"))
    effect = Mock(side_effect=AssertionError("dry-run invoked rearm"))
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: degraded,
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", lock)
    monkeypatch.setattr("nebius_vpngw.cli._prepare_vm_ha_planned_target", effect)

    result = CliRunner().invoke(
        app,
        [
            "apply",
            "--local-config-file",
            str(config_path),
            "--dry-run",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ha"]["outcome"] == "planned"
    assert payload["ha"]["classification"] == "standby-rearm"
    assert payload["ha"]["actions"] == ["rearm-exact-standby"]
    lock.assert_not_called()
    effect.assert_not_called()


@pytest.mark.usefixtures("ha_apply_admission")
def test_vm_ha_rearm_reclassifies_under_lock_and_verifies_two_samples(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    degraded = _inspection(
        _snapshot(
            overall="DEGRADED",
            action="nebius-vpngw apply --local-config-file fixture",
            reasons=("standby-not-ready",),
            member_compute_states=(("node-0", "running"), ("node-1", "stopped")),
        )
    )
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none", digest="b" * 64))
    inspect = Mock(side_effect=(degraded, degraded, healthy, healthy, healthy, healthy))
    effect = Mock()

    class Lock:
        def __init__(self, **kwargs) -> None:
            assert kwargs == {"project_id": "project-a", "gateway_name": "gateway"}

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", Lock)
    monkeypatch.setattr("nebius_vpngw.cli._prepare_vm_ha_planned_target", effect)
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)

    result = CliRunner().invoke(
        app,
        [
            "apply",
            "--local-config-file",
            str(config_path),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ha"]["outcome"] == "healthy"
    assert payload["ha"]["actions"] == ["standby-rearmed"]
    assert result.stderr.count("acquiring the VM-HA writer lock") == 1
    assert "✓ acquiring the VM-HA writer lock" in result.stderr
    assert result.stderr.count("verifying two agreeing fresh health samples") == 2
    assert "✓ verifying two agreeing fresh health samples" in result.stderr
    effect.assert_called_once()
    assert effect.call_args.kwargs["local_config_file"] == config_path
    assert effect.call_args.kwargs["target_role"] is None
    assert effect.call_args.kwargs["command"] == "apply"
    assert effect.call_args.kwargs["show_auth_progress"] is False
    assert callable(effect.call_args.kwargs["progress_sink"])
    assert inspect.call_count == 6


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
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
            "apply",
            "--local-config-file",
            str(config_path),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ha"]["classification"] == "controller-transition"
    assert payload["ha"]["actions"] == ["controller-observed"]
    assert payload["ha"]["reasons"] == [
        "controller-operation-pending",
        "controller-no-progress",
    ]
    assert payload["ha"]["next_action"] == (
        "inspect VM-HA controller service journals on both members, then rerun apply"
    )
    assert result.stderr.count("observing controller-owned recovery") == 1
    assert "✗ observing controller-owned recovery" in result.stderr
    assert inspect.call_count == 121


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)

    result = CliRunner().invoke(
        app,
        [
            "apply",
            "--local-config-file",
            str(config_path),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ha"]["outcome"] == "healthy"
    assert payload["ha"]["actions"] == ["controller-observed"]
    assert payload["ha"]["approval"] is None
    assert "Proceed with this exact VM-HA plan?" not in result.stderr
    assert inspect.call_count == 3


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
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
            "apply",
            "--local-config-file",
            str(config_path),
            "--dry-run",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ha"]["classification"] == "vm-ha-required"
    assert payload["ha"]["approval"]["kind"] == "resume-transaction"
    assert payload["ha"]["reasons"] == ["apply-transaction-resume-required"]
    assert inspect.call_count == 1
    sleep.assert_not_called()


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: removed,
    )
    monkeypatch.setattr("nebius_vpngw.cli._plan_vm_ha_apply_convergence", planner)

    result = CliRunner().invoke(
        app,
        [
            "apply",
            "--local-config-file",
            str(config_path),
            "--dry-run",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ha"]["classification"] == "vm-ha-required"
    assert payload["ha"]["approval"]["kind"] == "provisioning"
    assert payload["ha"]["reasons"] == ["apply-provisioning-required"]
    planner.assert_called_once_with(config_path)


@pytest.mark.usefixtures("ha_apply_admission")
def test_vm_ha_healthy_rerun_rejects_surplus_approval_without_effects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none"))
    inspect = Mock(side_effect=(healthy, healthy))
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr("nebius_vpngw.cli._inspect_vm_ha_command_status", inspect)
    monkeypatch.setattr("nebius_vpngw.cli.time.sleep", lambda _seconds: None)
    execute = Mock()
    monkeypatch.setattr("nebius_vpngw.cli._execute_vm_ha_apply_convergence", execute)

    result = CliRunner().invoke(
        app,
        [
            "apply",
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
    assert payload["ha"]["reasons"] == ["approval-digest-stale-or-incorrect"]
    assert inspect.call_count == 2
    execute.assert_not_called()


def test_vm_ha_rejects_approval_when_conversion_still_needs_input(
    sample_config: dict,
    tmp_path: Path,
    verified_ordinary_conversion,
) -> None:
    source = tmp_path / "gateway.config.yaml"
    source.write_text(yaml.safe_dump(sample_config, sort_keys=False), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "apply",
            "--convert-to-ha",
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
    assert payload["ha"]["reasons"] == ["approval-not-applicable"]


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: _inspection(snapshot),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._plan_vm_ha_apply_convergence",
        lambda _path: report,
    )

    result = CliRunner().invoke(
        app,
        [
            "apply",
            "--local-config-file",
            str(config_path),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ha"]["classification"] == "vm-ha-required"
    assert payload["ha"]["reasons"] == ["active-standby-replacement-required"]
    assert payload["ha"]["approval"]["kind"] == "active-standby-replacement"
    assert payload["ha"]["impact"]["resource_creation"] is True
    assert payload["ha"]["impact"]["destructive"] is False
    assert payload["ha"]["impact"]["vpn_traffic_interruption"] is False
    assert payload["ha"]["next_action"] == (
        "run nebius-vpngw apply --local-config-file "
        f"{config_path} --approve {'d' * 64} to create the missing non-owner VM"
    )

    text_result = CliRunner().invoke(
        app,
        ["apply", "--local-config-file", str(config_path)],
    )

    assert text_result.exit_code == 1
    assert (
        "Next action: run nebius-vpngw apply --local-config-file "
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


@pytest.mark.usefixtures("ha_apply_admission")
def test_vm_ha_failure_is_redacted_and_reports_effective_candidate(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "gateway.config.yaml"
    candidate = tmp_path / "gateway.vm-ha.config.yaml"
    source.write_text("version: 1\n", encoding="utf-8")
    candidate.write_text("version: 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
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
            "apply",
            "--local-config-file",
            str(source),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ha"]["effective_config_file"] == str(candidate)
    assert payload["ha"]["reasons"] == ["convergence-failed-safely"]
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
        (
            None,
            "agent-artifact-incompatible",
            "rebuild the agent wheel from the current source",
        ),
    ),
)
@pytest.mark.usefixtures("ha_apply_admission")
def test_vm_ha_agent_artifact_prerequisite_is_actionable_and_zero_effect(
    monkeypatch,
    tmp_path: Path,
    problem: VMHAAgentArtifactProblem | None,
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
    from nebius_vpngw.replay_window import require_replay_window_capability

    def reject_replay_artifact(*_args, **_kwargs):
        require_replay_window_capability(
            {"connections": [{"tunnels": [{"replay_window": 1024}]}]}, ()
        )

    planner = Mock(
        side_effect=(
            VMHAAgentArtifactError(problem, "PRIVATE_ARTIFACT_DETAIL")
            if problem is not None
            else reject_replay_artifact
        )
    )
    lock = Mock(return_value=_ContextManagedFake())
    effect = Mock(side_effect=AssertionError("artifact prerequisite executed apply"))
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: drift,
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
            "apply",
            "--local-config-file",
            str(config_path),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ha"]["outcome"] == "action-required"
    assert payload["ha"]["classification"] == "external-prerequisite"
    assert payload["ha"]["reasons"] == [reason]
    assert action_fragment in payload["ha"]["next_action"]
    assert "PRIVATE_ARTIFACT_DETAIL" not in result.output
    assert "✗ planning exact VM-HA convergence." in result.stderr
    lock.assert_called_once()
    effect.assert_not_called()


@pytest.mark.usefixtures("ha_apply_admission")
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
            "matching its exact pin, then rerun apply"
        ),
    )
    lock = Mock(return_value=_ContextManagedFake())
    effect = Mock(side_effect=AssertionError("SSH prerequisite executed apply"))
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: drift,
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
            "apply",
            "--local-config-file",
            str(config_path),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ha"]["outcome"] == "action-required"
    assert payload["ha"]["classification"] == "external-prerequisite"
    assert payload["ha"]["health"] == "blocked"
    assert payload["ha"]["reasons"] == ["replacement-ssh-identity-unavailable"]
    assert payload["ha"]["next_action"] == (
        "restore the missing non-owner's original private SSH host key "
        "matching its exact pin, then rerun apply"
    )
    assert "authentication" not in payload["ha"]["next_action"]
    lock.assert_called_once()
    effect.assert_not_called()

    text_result = CliRunner().invoke(
        app,
        ["apply", "--local-config-file", str(config_path)],
    )

    assert text_result.exit_code == 1
    assert "Reason:" in text_result.stdout
    assert "(replacement-ssh-identity-unavailable)" in text_result.stdout
    assert (
        "Next action: restore the missing non-owner's original private SSH host key "
        "matching its exact pin, then rerun apply" in text_result.stdout
    )
    assert "authentication-or-provider-unavailable" not in text_result.stdout


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.usefixtures("ha_apply_admission")
def test_vm_ha_artifact_change_after_execution_starts_reports_partial_resume(
    monkeypatch,
    tmp_path: Path,
    replay: bool,
) -> None:
    from nebius_vpngw.replay_window import ReplayWindowCapabilityError

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
        side_effect=(
            ReplayWindowCapabilityError("PRIVATE_ARTIFACT_DETAIL")
            if replay
            else VMHAAgentArtifactError(
                VMHAAgentArtifactProblem.CHANGED,
                "PRIVATE_ARTIFACT_DETAIL",
            )
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: drift,
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
            "apply",
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
    assert payload["ha"]["classification"] == "external-prerequisite"
    assert payload["ha"]["health"] == "blocked"
    assert payload["ha"]["actions"] == ["convergence-effects-may-have-started"]
    problem = "incompatible" if replay else "changed"
    assert payload["ha"]["reasons"] == [f"agent-artifact-{problem}-during-convergence"]
    assert "durable checkpoints and resume idempotently" in payload["ha"]["next_action"]
    assert "gateway changes may already have started" in payload["ha"]["next_action"]
    assert "PRIVATE_ARTIFACT_DETAIL" not in result.output
    assert planner.call_count == 2
    effect.assert_called_once()


@pytest.mark.parametrize(
    ("failure", "expected_reason", "expected_next_fragment"),
    (
        (
            _VMHAAgentPackagePreparationFailed(2, "VM-HA exact agent package installation failed"),
            "package-preparation-failed-member-2",
            "VM-HA exact agent package installation failed",
        ),
        (
            _VMHAApplyConvergenceFailed("PRIVATE_APPLY_DETAIL"),
            "apply-convergence-interrupted",
            "resume idempotently",
        ),
        (
            _VMHAApplyConvergenceFailed("PRIVATE_APPLY_DETAIL", pending=True),
            "waiting-for-authority",
            "resume the retained operation",
        ),
        (
            _VMHAApplyConvergenceFailed(
                "PRIVATE_APPLY_DETAIL",
                pending=True,
                diagnostics=("controller-observation-unavailable",),
            ),
            "waiting-for-authority",
            "resume the retained operation",
        ),
        (
            _VMHAApplyConvergenceFailed(
                "PRIVATE_APPLY_DETAIL",
                pending=True,
                diagnostics=("controller-observation-budget-exhausted",),
            ),
            "waiting-for-authority",
            "resume the retained operation",
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
@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: drift,
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
            "apply",
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
    assert payload["ha"]["health"] == ("unknown" if failure.pending else "blocked")
    assert payload["ha"]["outcome"] == ("pending" if failure.pending else "failed")
    assert payload["ha"]["actions"] == ["convergence-effects-may-have-started"]
    assert payload["ha"]["reasons"] == [expected_reason, *failure.diagnostics]
    assert expected_next_fragment in payload["ha"]["next_action"]
    assert "authentication" not in payload["ha"]["next_action"]
    assert "PRIVATE_APPLY_DETAIL" not in result.output
    if failure.diagnostics:
        recovery = payload["recovery"]
        assert recovery["reason_code"] == failure.diagnostics[0]
        assert "--repair" in recovery["repair_command"]
        assert str(config_path) in recovery["repair_command"]
        assert recovery["repair_prerequisite"]
        assert "vm-ha" in recovery["resume_command"]
        assert recovery["destroy_command"] is None


@pytest.mark.usefixtures("ha_apply_admission")
def test_vm_ha_authentication_exit_keeps_authentication_guidance(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "gateway.config.yaml"
    source.write_text("version: 1\n", encoding="utf-8")

    def fail_authentication(**_kwargs) -> None:
        raise typer.Exit(code=1) from NebiusCLIAuthenticationError("redacted")

    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion", fail_authentication
    )

    result = CliRunner().invoke(
        app,
        [
            "apply",
            "--local-config-file",
            str(source),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ha"]["reasons"] == ["authentication-or-provider-unavailable"]
    assert payload["ha"]["next_action"] == "restore authentication and rerun apply"


@pytest.mark.usefixtures("ha_apply_admission")
def test_vm_ha_bare_exit_is_not_reported_as_authentication(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "gateway.config.yaml"
    source.write_text("version: 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        Mock(side_effect=typer.Exit(code=1)),
    )

    result = CliRunner().invoke(
        app,
        [
            "apply",
            "--local-config-file",
            str(source),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ha"]["reasons"] == ["command-preflight-failed"]
    assert "authentication" not in payload["ha"]["next_action"]


@pytest.mark.usefixtures("ha_apply_admission")
def test_vm_ha_interruption_returns_130_with_stable_json(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "gateway.config.yaml"
    source.write_text("version: 1\n", encoding="utf-8")
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        Mock(side_effect=KeyboardInterrupt),
    )

    result = CliRunner().invoke(
        app,
        [
            "apply",
            "--local-config-file",
            str(source),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 130
    payload = json.loads(result.stdout)
    assert payload["schema"] == "nebius-vpngw.apply-result.v1"
    assert payload["ha"]["reasons"] == ["interrupted"]


@pytest.mark.usefixtures("ha_apply_admission")
@pytest.mark.parametrize("exit_wrapper", (False, True))
@pytest.mark.parametrize("pending", (False, True))
@pytest.mark.parametrize("output_format", ("text", "json"))
def test_ha_apply_preserves_package_reason_through_recovery_wrappers(
    monkeypatch, tmp_path: Path, exit_wrapper: bool, pending: bool, output_format: str
) -> None:
    from nebius_vpngw.deploy.apply_diagnostics import ObservationUnavailable

    config = tmp_path / "gateway.vm-ha.config.yaml"
    config.write_text("version: 1\n", encoding="utf-8")
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
    failure = _VMHAAgentPackagePreparationFailed(2, "HA handoff package plan changed")
    if pending:
        failure.__cause__ = ObservationUnavailable("package-observation", "read-timeout")

    def fail(**_kwargs):
        try:
            raise RuntimeError("PRIVATE_RECOVERY_DETAIL") from failure
        except RuntimeError as wrapped:
            if exit_wrapper:
                raise typer.Exit(1) from wrapped
            raise

    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config, ()),
    )
    monkeypatch.setattr(cli_module, "_inspect_vm_ha_command_status", lambda _path, **_kwargs: drift)
    monkeypatch.setattr(cli_module, "_plan_vm_ha_apply_convergence", lambda *_a, **_k: report)
    monkeypatch.setattr(cli_module, "_apply_impl", fail)

    result = CliRunner().invoke(
        app,
        ["apply", "-c", str(config), "--approve", "d" * 64, "--output-format", output_format],
    )

    assert result.exit_code == 1
    assert "PRIVATE_RECOVERY_DETAIL" not in result.output
    if output_format == "json":
        payload = json.loads(result.stdout)
        assert payload["ha"]["outcome"] == ("pending" if pending else "failed")
        assert payload["ha"]["reasons"][0] == (
            "waiting-for-authority" if pending else "package-preparation-failed-member-2"
        )
        assert "--repair" in payload["recovery"]["repair_command"]
        assert str(config) in payload["recovery"]["repair_command"]
        if not pending:
            assert "member 2" in payload["recovery"]["reason"]
            assert "HA handoff package plan changed" in payload["recovery"]["reason"]
    else:
        assert "Reason:" in result.output and "Next action:" in result.output
        assert result.output.count("--repair") == 1
        if not pending:
            assert "HA handoff package plan changed" in result.output


@pytest.mark.usefixtures("ha_apply_admission")
@pytest.mark.parametrize("exit_code", (130, 1))
@pytest.mark.parametrize("output_format", ("text", "json"))
def test_ha_apply_preserves_nested_engine_interrupt(
    monkeypatch, tmp_path: Path, exit_code: int, output_format: str
) -> None:
    config = tmp_path / "gateway.vm-ha.config.yaml"
    config.write_text("version: 1\n", encoding="utf-8")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **kwargs: _VMHAEffectiveConfig(config, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status", lambda path, **_kwargs: drift
    )
    planner = Mock(side_effect=(report, report))
    monkeypatch.setattr("nebius_vpngw.cli._plan_vm_ha_apply_convergence", planner)
    effect = Mock(side_effect=typer.Exit(exit_code))
    monkeypatch.setattr("nebius_vpngw.cli._apply_impl", effect)

    result = CliRunner().invoke(
        app,
        ["apply", "-c", str(config), "--approve", "d" * 64, "--output-format", output_format],
    )

    effect.assert_called_once()
    assert planner.call_count == 2
    assert result.exit_code == exit_code
    if output_format == "json":
        payload = json.loads(result.stdout)
        assert payload["schema"] == "nebius-vpngw.apply-result.v1"
        assert "--repair" in payload["recovery"]["repair_command"]
        if exit_code == 130:
            assert payload["ha"]["reasons"] == ["interrupted"]
    else:
        assert "Reason:" in result.output and "Next action:" in result.output
        assert result.output.count("--repair") == 1


@pytest.mark.parametrize("extra_args", ((), ("--output-format", "json")))
@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: drift,
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
            "apply",
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
        assert payload["ha"]["outcome"] == "planned"
        assert payload["ha"]["classification"] == "vm-ha-required"
        assert payload["ha"]["approval"] == {
            "kind": "apply-convergence",
            "digest": "d" * 64,
            "effects": ["reconcile-managed-routes-through-apply-owner"],
        }
    else:
        assert "Apply plan." in result.stdout
    effect.assert_not_called()


@pytest.mark.parametrize("extra_args", ((), ("--output-format", "json")))
@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
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
        ["apply", "--local-config-file", str(config_path), *extra_args],
        input="n\n",
    )

    assert result.exit_code == 0
    assert "Proceed with this exact VM-HA plan?" not in result.stderr
    if extra_args:
        payload = json.loads(result.stdout)
        assert payload["ha"]["outcome"] == "healthy"
        assert result.stdout.count("\n") == 1
    else:
        assert "Apply completed." in result.stdout and "HA health: healthy" in result.stdout
    assert planner.call_count == 2
    effect.assert_called_once()
    assert effect.call_args.args == (config_path, report)
    assert inspect.call_count == 3


@pytest.mark.parametrize(
    "recovery_kind", ("artifact-standby-recovery", "replacement-artifact-recovery")
)
@pytest.mark.usefixtures("ha_apply_admission")
def test_vm_ha_disruptive_artifact_recovery_warns_and_requires_confirmation(
    monkeypatch,
    tmp_path: Path,
    recovery_kind: str,
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
        kind=recovery_kind,
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=(
            "install-approved-artifact-on-serving-owner",
            "verify-owner-routes-forwarding-and-standby",
        ),
        has_destructive_changes=False,
        managed_ssh_action=None,
        impact=_vm_ha_apply_plan_impact(
            recovery_kind,
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
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
        ["apply", "--local-config-file", str(config_path)],
        input="y\n",
    )

    assert result.exit_code == 0
    assert "Apply completed." in result.stdout
    assert f"Approval kind: {recovery_kind}" in result.stderr
    assert f"Approval digest: {'d' * 64}" in result.stderr
    assert f"Impact: {report.impact.summary}." in result.stderr
    assert "install approved artifact on serving owner" in result.stderr
    assert "Proceed with this exact VM-HA plan? [y/N]" in result.stderr
    assert result.stderr.index("Planned effects:") < result.stderr.index("Proceed with")
    assert planner.call_count == 2
    effect.assert_called_once()
    assert effect.call_args.args == (config_path, report)
    assert callable(effect.call_args.kwargs["progress_sink"])
    assert inspect.call_count == 4


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
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
        ["apply", "--local-config-file", str(config_path)],
        input="y\n",
    )

    assert result.exit_code == 0
    assert "Create the missing non-owner VM now? [y/N]" in result.stderr
    assert "Approval digest:" not in result.stderr
    assert "Artifact SHA-256:" not in result.stderr
    assert "serving owner is not restarted" in result.stderr
    effect.assert_called_once()
    assert effect.call_args.args == (config_path, report)


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: blocked,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._plan_vm_ha_apply_convergence",
        lambda _path: report,
    )
    monkeypatch.setattr("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", lambda: True)

    result = CliRunner().invoke(
        app,
        ["apply", "--local-config-file", str(config_path)],
        input="n\n",
    )

    assert result.exit_code == 0
    assert "Create the missing non-owner VM now? [y/N]" in result.stderr
    assert "Approval digest:" not in result.output
    assert "--approve" not in result.output
    assert "rerun apply and answer y" in result.stdout


@pytest.mark.parametrize("answer", ("\n", "n\n"))
@pytest.mark.usefixtures("ha_apply_admission")
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
    lock = Mock(return_value=_ContextManagedFake())
    effect = Mock(side_effect=AssertionError("declined approval executed apply"))
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: drift,
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
        ["apply", "--local-config-file", str(config_path)],
        input=answer,
    )

    assert result.exit_code == 0
    assert "Apply cancelled." in result.stdout
    assert "Proceed with this exact VM-HA plan? [y/N]" in result.stderr
    lock.assert_called_once()
    effect.assert_not_called()


@pytest.mark.usefixtures("ha_apply_admission")
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
    lock = Mock(return_value=_ContextManagedFake())
    effect = Mock(side_effect=AssertionError("destructive plan executed apply"))
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: drift,
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
            "apply",
            "--local-config-file",
            str(config_path),
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ha"]["reasons"] == ["destructive-gateway-recreation-required"]
    assert payload["ha"]["approval"] is None
    assert payload["ha"]["impact"] == {
        "summary": "Deletes and recreates gateway VM resources and may interrupt VPN traffic",
        "destructive": True,
        "vpn_traffic_interruption": True,
        "resource_creation": True,
        "approval_required": True,
    }
    lock.assert_called_once()
    effect.assert_not_called()


@pytest.mark.usefixtures("ha_apply_admission")
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
    lock = Mock(return_value=_ContextManagedFake())
    effect = Mock(side_effect=AssertionError("interrupted approval executed apply"))
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: drift,
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
        ["apply", "--local-config-file", str(config_path)],
        input="",
    )

    assert result.exit_code == 130
    assert "(interrupted)" in result.stdout
    assert "Proceed with this exact VM-HA plan? [y/N]" in result.stderr
    lock.assert_called_once()
    effect.assert_not_called()


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: drift,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._plan_vm_ha_apply_convergence",
        lambda _path: report,
    )
    monkeypatch.setattr("nebius_vpngw.cli._vm_ha_wizard_streams_interactive", lambda: True)

    result = CliRunner().invoke(
        app,
        [
            "apply",
            "--local-config-file",
            str(config_path),
            "--output-format",
            "json",
        ],
        input="y\n",
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["ha"]["outcome"] == "action-required"
    assert result.stdout.count("\n") == 1
    assert "Starting:" not in result.stdout
    assert "Proceed with this exact VM-HA plan?" not in result.stderr
    assert result.stderr.count("planning exact VM-HA convergence") == 1
    assert "✓ planning exact VM-HA convergence" in result.stderr


@pytest.mark.parametrize("extra_args", ((), ("--output-format", "json")))
@pytest.mark.parametrize(
    ("plan_kind", "ssh_action"), (("apply-convergence", None), ("migration", "migrate"))
)
@pytest.mark.usefixtures("ha_apply_admission")
def test_vm_ha_exact_apply_approval_replans_under_lock_and_never_prompts(
    monkeypatch,
    tmp_path: Path,
    extra_args: tuple[str, ...],
    plan_kind: str,
    ssh_action: str | None,
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
        kind=plan_kind,
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("reconcile-managed-routes-through-apply-owner",),
        has_destructive_changes=False,
        managed_ssh_action=ssh_action,
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
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
            "apply",
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
        assert payload["ha"]["outcome"] == "healthy"
        assert payload["ha"]["actions"] == ["apply-converged"]
    else:
        assert "Apply completed." in result.stdout
    assert result.stderr.count("revalidating the exact approved plan") == 1
    assert "✓ revalidating the exact approved plan" in result.stderr
    assert planner.call_count == 2
    effect.assert_called_once()
    assert effect.call_args.args == (config_path, report)
    assert callable(effect.call_args.kwargs["progress_sink"])
    assert inspect.call_count == 3


@pytest.mark.usefixtures("ha_apply_admission")
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
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status",
        lambda _path, **_kwargs: drift,
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._plan_vm_ha_apply_convergence",
        lambda _path: report,
    )
    lock = Mock(return_value=_ContextManagedFake())
    effect = Mock(side_effect=AssertionError("stale approval executed apply"))
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", lock)
    monkeypatch.setattr("nebius_vpngw.cli._execute_vm_ha_apply_convergence", effect)

    result = CliRunner().invoke(
        app,
        [
            "apply",
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
    assert payload["ha"]["reasons"] == ["approval-digest-stale-or-incorrect"]
    lock.assert_called_once()
    effect.assert_not_called()


@pytest.mark.parametrize("dry_run", (False, True))
@pytest.mark.parametrize("commit_boundary", ("before-prepare", "owner-prepared", "peer-committed"))
@pytest.mark.usefixtures("ha_apply_admission")
def test_vm_ha_resumes_policy_after_completed_start_with_both_members_reachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dry_run: bool, commit_boundary: str
) -> None:
    config_path = tmp_path / "gateway.vm-ha.config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none"))
    statuses = list(_terminal_enabled_recovery_statuses(peer_agrees=True))
    decision = policy_decision_digest(
        cluster_id="cluster-a",
        member_node_ids=("node-0", "node-1"),
        generation_id="a" * 64,
        desired=StandbyAutoHealing.DISABLED,
        operation_id="c" * 64,
        coordinator_node_id="node-0",
        predecessor_digest="b" * 64,
    )
    for status in statuses:
        record = AutoHealingPolicyRecord.from_mapping(
            {
                **status["record"],
                "desired": "disabled",
                "decision_digest": decision,
                "peer_ack_digest": decision,
            }
        )
        status.update(record=record.to_dict(), desired="disabled", decision_digest=decision)
    transaction = _vm_ha_auto_healing_transaction_for_statuses(
        desired=StandbyAutoHealing.ENABLED,
        inspection=healthy,
        statuses=statuses,
    )
    statuses[0]["recovery"].update(
        operation_id=transaction.operation_id, policy_digest=decision, predecessor_digest=decision
    )
    terminal = list(
        _terminal_enabled_recovery_statuses(
            peer_agrees=True,
            operation_id=transaction.operation_id,
            predecessor_digest=decision,
        )
    )
    if commit_boundary != "before-prepare":
        prepared = AutoHealingPolicyRecord.from_mapping(
            {**terminal[0]["record"], "phase": "prepared", "peer_ack_digest": None}
        )
        statuses[0] = {
            **terminal[0],
            "record": prepared.to_dict(),
            "phase": "prepared",
            "peer_agrees": False,
            "recovery": statuses[0]["recovery"],
        }
        if commit_boundary == "peer-committed":
            statuses[1] = terminal[1]
    effects = []

    def execute(**kwargs):
        assert kwargs["transaction"] == transaction
        assert kwargs["initial_statuses"] == statuses
        effects.append("commit-enabled")
        return terminal

    def cleanup(**kwargs):
        assert kwargs["statuses"] == terminal
        effects.append("clear-completed")
        return True

    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._inspect_vm_ha_command_status", lambda _path, **_kwargs: healthy
    )
    monkeypatch.setattr(
        "nebius_vpngw.cli._run_vm_ha_auto_healing_statuses", lambda **_kwargs: statuses
    )
    monkeypatch.setattr("nebius_vpngw.cli._execute_vm_ha_auto_healing_policy", execute)
    monkeypatch.setattr("nebius_vpngw.cli._clear_completed_vm_ha_auto_healing_recovery", cleanup)
    monkeypatch.setattr(
        "nebius_vpngw.cli._prepare_vm_ha_planned_target",
        Mock(side_effect=AssertionError("completed start was repeated")),
    )
    monkeypatch.setattr("nebius_vpngw.cli.VMHAApplyLock", Mock(return_value=nullcontext()))
    result = CliRunner().invoke(
        app,
        [
            "apply",
            "-c",
            str(config_path),
            "--standby-auto-healing",
            "enabled",
            "--output-format",
            "json",
            *(["--dry-run"] if dry_run else []),
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ha"]["outcome"] == ("planned" if dry_run else "healthy")
    assert effects == ([] if dry_run else ["commit-enabled", "clear-completed"])
    if dry_run:
        assert "clear-completed-recovery" not in payload["ha"]["approval"]["effects"]


def _disabled_recovery_observation_statuses(*, consumed: bool = False):
    """An acknowledged disabled pair with one original accepted recovery."""
    from dataclasses import replace

    owner, peer = _terminal_enabled_recovery_statuses(peer_agrees=True)
    disabled_digest = policy_decision_digest(
        cluster_id="cluster-a",
        member_node_ids=("node-0", "node-1"),
        generation_id="a" * 64,
        desired=StandbyAutoHealing.DISABLED,
        operation_id="c" * 64,
        coordinator_node_id="node-0",
        predecessor_digest="b" * 64,
    )
    for status in (owner, peer):
        record = replace(
            AutoHealingPolicyRecord.from_mapping(status["record"]),
            desired=StandbyAutoHealing.DISABLED,
            decision_digest=disabled_digest,
            peer_ack_digest=disabled_digest,
        )
        status.update(record=record.to_dict(), desired="disabled", decision_digest=disabled_digest)
    transaction = cli_module._vm_ha_auto_healing_transaction(
        desired=StandbyAutoHealing.ENABLED, statuses=[owner, peer]
    )
    recovery = replace(
        AutoHealingRecoveryRecord.from_mapping(owner["recovery"]),
        operation_id=transaction.operation_id,
        policy_digest=disabled_digest,
        predecessor_digest=disabled_digest,
        phase=AutoHealingRecoveryPhase.CONSUMED if consumed else AutoHealingRecoveryPhase.COMPLETED,
    )
    owner.update(
        recovery=recovery.to_dict(),
        recovery_phase=recovery.phase.value,
        accepted_start=consumed,
        recovery_authority={
            "allocation_id": recovery.allocation_id,
            "ownership_epoch": recovery.ownership_epoch,
            "promotion_receipt_id": recovery.promotion_receipt_id,
        },
    )
    return owner, peer, transaction


@pytest.fixture
def recovery_observation_clock(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(cli_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        cli_module.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay)
    )
    return clock


@pytest.mark.parametrize("counterfactual", [False, True])
@pytest.mark.parametrize("initial_pair", [False, True])
@pytest.mark.usefixtures("ha_apply_admission")
def test_vm_ha_observes_original_start_before_policy_approval(
    monkeypatch, tmp_path, recovery_observation_clock, counterfactual, initial_pair
):
    config = tmp_path / "gateway.config.yaml"
    config.write_text("version: 1\n")
    owner, peer, transaction = _disabled_recovery_observation_statuses()
    first = _inspection(
        _snapshot(
            overall="TRANSITIONING",
            action="wait",
            digest="a" * 64,
            member_compute_states=(("node-0", "running"), ("node-1", "stopped")),
        )
    )
    running = _inspection(_snapshot(overall="MAINTENANCE", action="enable", digest="b" * 64))
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none", digest="b" * 64))
    calls = {"inspect": 0, "status": 0, "execute": 0, "lock": 0}

    def inspect(*_args, **_kwargs):
        calls["inspect"] += 1
        return healthy if calls["execute"] else first if calls["inspect"] == 1 else running

    def statuses(**_kwargs):
        calls["status"] += 1
        return [owner] if calls["status"] <= 2 and not initial_pair else [owner, peer]

    def execute(**kwargs):
        assert calls["status"] == (4 if initial_pair else 5)
        assert recovery_observation_clock[0] == (1 if initial_pair else 2)
        assert kwargs["initial_statuses"] == [owner, peer]
        assert kwargs["transaction"] == transaction
        kwargs["authority_guard"]()
        calls["execute"] += 1
        return list(
            _terminal_enabled_recovery_statuses(
                peer_agrees=True,
                operation_id=transaction.operation_id,
                predecessor_digest=transaction.predecessor_digest,
            )
        )

    class Lock(_ContextManagedFake):
        def __init__(self, **_kwargs):
            calls["lock"] += 1
            calls["status_at_lock"] = calls["status"]

    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kw: _VMHAEffectiveConfig(config, ()),
    )
    monkeypatch.setattr(cli_module, "_inspect_vm_ha_status_with_region", inspect)
    monkeypatch.setattr(cli_module, "_run_vm_ha_auto_healing_statuses", statuses)
    monkeypatch.setattr(cli_module, "_execute_vm_ha_auto_healing_policy", execute)
    monkeypatch.setattr(
        cli_module, "_clear_completed_vm_ha_auto_healing_recovery", lambda **_kw: True
    )
    monkeypatch.setattr(cli_module, "VMHAApplyLock", Lock)
    no_start = Mock(side_effect=AssertionError("must not repeat the original Start"))
    monkeypatch.setattr(cli_module, "_prepare_vm_ha_planned_target", no_start)
    if counterfactual:
        monkeypatch.setattr(
            cli_module,
            "_observe_vm_ha_auto_healing_recovery",
            lambda _p, current, observed, **_kw: (current, observed),
        )
    result = CliRunner().invoke(
        app,
        [
            "apply",
            "--local-config-file",
            str(config),
            "--standby-auto-healing",
            "enabled",
            "--output-format",
            "json",
        ],
    )
    payload = json.loads(result.stdout)
    if counterfactual:
        assert result.exit_code == 1
        assert payload["ha"]["reasons"] == ["approval-state-changed"]
        assert payload["recovery"]["reason_code"] == "approval-state-changed"
        assert "--repair" in payload["recovery"]["repair_command"]
        assert calls["execute"] == 0
    else:
        assert result.exit_code == 0, result.stdout
        assert payload["ha"]["outcome"] == "healthy"
        assert calls["execute"] == 1
        assert recovery_observation_clock[0] == (2 if initial_pair else 3)
    assert calls["lock"] == 1
    assert calls["status_at_lock"] == 0
    no_start.assert_not_called()


@pytest.mark.parametrize(
    "drift",
    ["owner", "lifecycle", "project", "operation", "allocation", "record", "phase", "malformed"],
)
def test_recovery_observer_rejects_changed_identity_without_effects(
    monkeypatch, recovery_observation_clock, drift
):
    from dataclasses import replace

    owner, peer, _ = _disabled_recovery_observation_statuses()
    first = _inspection(_snapshot(overall="TRANSITIONING", action="wait"))
    current = _inspection(_snapshot(overall="MAINTENANCE", action="enable"))
    if drift == "owner":
        current = replace(
            current,
            snapshot=replace(
                current.snapshot,
                authority=replace(current.snapshot.authority, owner_node_id="node-1"),
            ),
        )
    elif drift == "lifecycle":
        current = replace(
            current,
            snapshot=replace(current.snapshot, lifecycle_state=SimpleNamespace(status="foreign")),
        )
    elif drift == "project":
        current = replace(current, project_id="foreign")
    changed = dict(owner)
    if drift in {"operation", "allocation", "phase"}:
        recovery = AutoHealingRecoveryRecord.from_mapping(owner["recovery"])
        changes = (
            {"operation_id": "e" * 64}
            if drift == "operation"
            else {"allocation_id": "foreign"}
            if drift == "allocation"
            else {"phase": AutoHealingRecoveryPhase.CONSUMED}
        )
        recovery = replace(recovery, **changes)
        changed.update(recovery=recovery.to_dict(), recovery_phase=recovery.phase.value)
    elif drift == "record":
        record = AutoHealingPolicyRecord.from_mapping(owner["record"])
        changed["record"] = replace(record, updated_at=99).to_dict()
    elif drift == "malformed":
        changed["recovery"] = {"invalid": "PRIVATE_DETAIL"}
    monkeypatch.setattr(cli_module, "_inspect_vm_ha_status_with_region", lambda *_a, **_kw: current)
    monkeypatch.setattr(
        cli_module, "_run_vm_ha_auto_healing_statuses", lambda **_kw: [changed, peer]
    )
    with pytest.raises(_VMHAApplyConvergenceFailed) as failure:
        cli_module._observe_vm_ha_auto_healing_recovery(
            Path("config.yaml"), first, [owner], desired=StandbyAutoHealing.ENABLED
        )
    assert failure.value.reason == "controller-observation-contradictory"
    assert not failure.value.pending


@pytest.mark.parametrize("failure", ["stall", "deadline", "unavailable"])
def test_recovery_observer_keeps_deadline_and_stall_separate(
    monkeypatch, recovery_observation_clock, failure
):
    from nebius_vpngw.deploy.vm_ha_cloud import vm_ha_observation_budget

    owner, _peer, _ = _disabled_recovery_observation_statuses()
    first = _inspection(_snapshot(overall="TRANSITIONING", action="wait"))

    def inspect(*_a, **_kw):
        if failure == "unavailable":
            raise TimeoutError("observation unavailable")
        return first

    def statuses(**_kw):
        # A new heartbeat/recovery timestamp is not semantic progress.
        return [
            {
                **owner,
                "recovery": {**owner["recovery"], "updated_at": recovery_observation_clock[0]},
            }
        ]

    monkeypatch.setattr(cli_module, "_inspect_vm_ha_status_with_region", inspect)
    monkeypatch.setattr(cli_module, "_run_vm_ha_auto_healing_statuses", statuses)
    with (
        pytest.raises(_VMHAApplyConvergenceFailed) as error,
        vm_ha_observation_budget(
            seconds=2 if failure == "deadline" else 30,
            clock=lambda: recovery_observation_clock[0],
        ),
    ):
        cli_module._observe_vm_ha_auto_healing_recovery(
            Path("config.yaml"),
            first,
            [owner],
            desired=StandbyAutoHealing.ENABLED,
            stall_timeout_seconds=3,
        )
    expected = {
        "stall": "controller-no-progress",
        "deadline": "controller-observation-budget-exhausted",
        "unavailable": "controller-observation-unavailable",
    }[failure]
    assert error.value.pending
    assert error.value.diagnostics == (expected,)
    assert recovery_observation_clock[0] == {"stall": 3, "deadline": 2, "unavailable": 0}[failure]


@pytest.mark.parametrize("case", ["fresh", "unsubmitted", "settled-stopped", "terminal-pair"])
def test_recovery_observer_leaves_other_planning_paths_unchanged(monkeypatch, case):
    owner, peer, _ = _disabled_recovery_observation_statuses(consumed=case == "unsubmitted")
    if case == "unsubmitted":
        owner["accepted_start"] = False
    elif case == "fresh":
        owner.update(recovery=None, recovery_phase=None)
    stopped = _inspection(
        _snapshot(
            overall="MAINTENANCE",
            action="enable",
            member_compute_states=(
                ("node-0", "running"),
                ("node-1", "running" if case == "terminal-pair" else "stopped"),
            ),
        )
    )
    statuses = [owner, peer] if case == "terminal-pair" else [owner]
    forbidden = Mock(side_effect=AssertionError("no extra observation required"))
    monkeypatch.setattr(cli_module, "_inspect_vm_ha_status_with_region", forbidden)
    assert cli_module._observe_vm_ha_auto_healing_recovery(
        Path("config.yaml"), stopped, statuses, desired=StandbyAutoHealing.ENABLED
    ) == (stopped, statuses)
    forbidden.assert_not_called()


def test_recovery_observer_tracks_progress_below_unchanged_overall(
    monkeypatch, recovery_observation_clock
):
    consumed, peer, _ = _disabled_recovery_observation_statuses(consumed=True)
    completed, _, _ = _disabled_recovery_observation_statuses()
    first = _inspection(
        _snapshot(
            overall="TRANSITIONING",
            action="wait",
            member_compute_states=(("node-0", "running"), ("node-1", "stopped")),
        )
    )
    moving = _inspection(
        _snapshot(
            overall="TRANSITIONING",
            action="wait",
            member_compute_states=(("node-0", "running"), ("node-1", "transitional")),
        )
    )
    running = _inspection(_snapshot(overall="TRANSITIONING", action="wait"))
    observations = iter([first, moving, moving, running, running, running])
    policies = iter(
        [[consumed], [consumed], [completed], [completed], [completed, peer], [completed, peer]]
    )
    monkeypatch.setattr(
        cli_module, "_inspect_vm_ha_status_with_region", lambda *_a, **_kw: next(observations)
    )
    monkeypatch.setattr(
        cli_module, "_run_vm_ha_auto_healing_statuses", lambda **_kw: next(policies)
    )
    assert cli_module._observe_vm_ha_auto_healing_recovery(
        Path("config.yaml"),
        first,
        [consumed],
        desired=StandbyAutoHealing.ENABLED,
        stall_timeout_seconds=2,
    ) == (running, [completed, peer])
    assert recovery_observation_clock[0] == 5


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.usefixtures("ha_apply_admission")
def test_recovery_observation_pending_has_resume_and_repair_without_writes(
    monkeypatch, tmp_path, recovery_observation_clock, dry_run
):
    config = tmp_path / "config with spaces.yaml"
    config.write_text("version: 1\n")
    owner, _peer, _ = _disabled_recovery_observation_statuses()
    first = _inspection(_snapshot(overall="TRANSITIONING", action="wait"))
    calls = [0]

    def inspect(*_a, **_kw):
        calls[0] += 1
        if calls[0] > 1:
            raise TimeoutError("PRIVATE_DETAIL")
        return first

    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kw: _VMHAEffectiveConfig(config, ()),
    )
    monkeypatch.setattr(cli_module, "_inspect_vm_ha_status_with_region", inspect)
    monkeypatch.setattr(cli_module, "_run_vm_ha_auto_healing_statuses", lambda **_kw: [owner])
    forbidden = Mock(side_effect=AssertionError("pending observation must not mutate"))
    lock = Mock(return_value=_ContextManagedFake())
    monkeypatch.setattr(cli_module, "VMHAApplyLock", lock)
    monkeypatch.setattr(cli_module, "_prepare_vm_ha_planned_target", forbidden)
    monkeypatch.setattr(cli_module, "_execute_vm_ha_auto_healing_policy", forbidden)
    args = [
        "apply",
        "--local-config-file",
        str(config),
        "--standby-auto-healing",
        "enabled",
        "--output-format",
        "json",
    ]
    if dry_run:
        args.append("--dry-run")
    result = CliRunner().invoke(app, args)
    payload = json.loads(result.stdout)
    if dry_run:
        assert result.exit_code == 0, result.stdout
        assert payload["ha"]["outcome"] == "planned"
        assert calls[0] == 1
    else:
        assert payload["ha"]["outcome"] == "pending", result.stdout
        assert payload["recovery"]["reason_code"] == "controller-observation-unavailable"
        assert "--repair" in payload["recovery"]["repair_command"]
        assert str(config) in payload["recovery"]["repair_command"]
        assert "--standby-auto-healing enabled" in payload["recovery"]["resume_command"]
        assert "PRIVATE_DETAIL" not in result.stdout + result.stderr
    forbidden.assert_not_called()

    assert lock.call_count == (0 if dry_run else 1)


@pytest.mark.parametrize("output_format", ["json", "text"])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.usefixtures("ha_apply_admission")
def test_vm_ha_final_health_failure_explains_verification_and_repair(
    monkeypatch, tmp_path: Path, output_format: str, dry_run: bool
) -> None:
    config_path = tmp_path / "gateway's HA config.yaml"
    config_path.write_text("version: 1\n", encoding="utf-8")
    initial = _inspection(_snapshot(overall="TRANSITIONING", action="wait"))
    healthy = _inspection(_snapshot(overall="HEALTHY", action="none"))
    degraded = _inspection(_snapshot(overall="DEGRADED", action="inspect"))
    inspections = iter((initial, healthy))
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ha_convergence.resolve_conversion",
        lambda **_kwargs: _VMHAEffectiveConfig(config_path, ()),
    )
    monkeypatch.setattr(
        cli_module, "_inspect_vm_ha_command_status", lambda *_a, **_kw: next(inspections, degraded)
    )
    monkeypatch.setattr(cli_module.time, "sleep", lambda _seconds: None)
    result = CliRunner().invoke(
        app,
        [
            "apply",
            "--local-config-file",
            str(config_path),
            "--output-format",
            output_format,
            *(["--dry-run"] if dry_run else []),
        ],
    )
    assert result.exit_code == 1
    if output_format == "json":
        payload = json.loads(result.stdout)
        assert payload["ha"]["reasons"] == ["health-observations-did-not-agree"]
        assert payload["recovery"]["reason_code"] == "health-observations-did-not-agree"
        assert "healthy HA" in payload["recovery"]["reason"]
        assert "--repair" in payload["recovery"]["repair_command"]
        assert payload["ha"]["actions"] == (
            [] if dry_run else ["convergence-effects-may-have-started"]
        )
    else:
        assert "healthy HA" in result.stdout
        assert "Reason:" in result.stdout
        assert result.stdout.count("--repair") == 1


def test_apply_health_confirmation_retains_shared_verification_diagnostic(tmp_path: Path) -> None:
    from nebius_vpngw.deploy.recovery import Invocation, advice

    config = tmp_path / "gateway.yaml"
    observed = _inspection(_snapshot(overall="DEGRADED", action="inspect"))
    with pytest.raises(_VMHAApplyConvergenceFailed) as caught:
        cli_module._confirm_vm_ha_healthy(config, observed, attempts=1)
    recovery = advice(Invocation("apply", str(config)), caught.value)
    assert recovery.reason_code == "health-observations-did-not-agree"
    assert "healthy HA" in recovery.reason
    assert "--repair" in recovery.repair_command
