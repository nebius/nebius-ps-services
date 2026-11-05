from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import typer

from nebius_vpngw import cli
from nebius_vpngw.agent import main as agent
from nebius_vpngw.deploy.ssh_push import SSHPush, VMHAStandbyReplacementNotReady
from nebius_vpngw.vm_ha_diagnostics import READINESS_REASONS, decode_not_ready, encode_not_ready


def test_readiness_transport_is_bounded_and_allowlisted():
    reasons = tuple(sorted(READINESS_REASONS))
    assert decode_not_ready(encode_not_ready(reasons).encode()) == reasons
    for invalid in (
        b"private raw traceback",
        b"x" * 2049,
        json.dumps(
            {"schema": "nebius-vpngw/vm-ha-standby-replacement-not-ready-v1", "reasons": ["secret"]}
        ).encode(),
        b"[]",
        b"null",
        b"\xff",
    ):
        assert decode_not_ready(invalid) == ()


def test_final_readiness_failure_preserves_latest_predicates():
    push = SSHPush()
    push._run_vm_ha_removal_agent_command = Mock(
        side_effect=[
            VMHAStandbyReplacementNotReady("private", reasons=("transfer-not-quiescent",)),
            VMHAStandbyReplacementNotReady("private", reasons=("rearm-operation-pending",)),
        ]
    )
    clock = iter((0.0, 61.0))
    with pytest.raises(VMHAStandbyReplacementNotReady) as raised:
        push.verify_vm_ha_standby_replacement_quiescent(
            "203.0.113.4",
            "test",
            {},
            inhibition={"operation_id": "d" * 64},
            monotonic=lambda: next(clock),
            sleeper=Mock(),
        )
    assert raised.value.reasons == ("rearm-operation-pending",)
    assert "private" not in str(raised.value)


@pytest.mark.parametrize("kind", ["apply-convergence", "replacement-artifact-recovery"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_recovery_keeps_phase_and_typed_cause_without_exception_text(monkeypatch, kind, wrapped):
    report = cli._VMHAApplyPlanReport(
        kind=kind,
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("typed-effect",),
        has_destructive_changes=False,
        managed_ssh_action=None,
    )

    def failure(*_args, **kwargs):
        sink = kwargs.get("progress_sink") or kwargs["vm_ha_progress_sink"]
        sink(
            cli._VMHAProgressEvent(
                cli._VMHAProgressPhase.VERIFY_OWNER, cli._VMHAProgressState.STARTED
            )
        )
        cause = TimeoutError("SECRET_TOKEN and private host")
        if wrapped:
            activation = cli._VMHAActivationFailed("PRIVATE_RAW_DIAGNOSTIC")
            activation.__cause__ = cause
            raise typer.Exit(code=1) from activation
        raise cause

    monkeypatch.setattr(cli, "_apply_impl", failure)
    monkeypatch.setattr(cli, "_execute_vm_ha_replacement_artifact_recovery", failure)
    with pytest.raises(cli._VMHAApplyConvergenceFailed) as raised:
        cli._execute_vm_ha_apply_convergence(Path("test.yaml"), report)
    assert raised.value.diagnostics == ("phase-verify-owner", "cause-timeout")
    assert "SECRET" not in str(raised.value) and "PRIVATE" not in str(raised.value)


def test_unknown_cause_and_cyclic_chain_are_safe():
    error = RuntimeError("secret")
    error.__cause__ = error
    assert cli._vm_ha_convergence_diagnostics(error, cli._VMHAProgressPhase.COMMIT_LIFECYCLE) == (
        "phase-commit-lifecycle",
        "cause-unclassified",
    )


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("controller_capabilities", [], "capability-unavailable"),
        ("node_id", "foreign", "identity-mismatch"),
        ("state", "unknown", "owner-state-not-ready"),
        ("data_plane_mode", "blocked", "data-plane-not-active"),
        ("promotion_ready", False, "promotion-not-ready"),
        ("observed_owner_node_id", "foreign", "ownership-unverified"),
        ("apply_locked", True, "apply-locked"),
        ("apply_operation_id", "foreign", "apply-operation-pending"),
        ("transfer_inhibition_operation_id", "foreign", "inhibition-not-acknowledged"),
        ("transfer_inhibition_quiescent", False, "transfer-not-quiescent"),
        ("pending_operation_id", "foreign", "operation-pending"),
        ("accepted-cloud-operation.json", "{}", "controller-operation-pending"),
        ("rearm-cloud-operation.json", "{}", "rearm-operation-pending"),
        ("inhibition", "foreign", "inhibition-mismatch"),
    ],
)
def test_each_readiness_predicate_remains_fail_closed(monkeypatch, tmp_path, field, value, reason):
    status = dict(
        cluster_id="cluster",
        node_id="owner",
        generation_id="generation",
        controller_capabilities=[
            agent.STANDBY_REPLACEMENT_INHIBITION_CAPABILITY,
            agent.LIVE_PEER_REPLACEMENT_CAPABILITY,
        ],
        state="active",
        reasons=[],
        data_plane_mode="active",
        promotion_ready=True,
        observed_owner_node_id="owner",
        apply_locked=False,
        apply_operation_id=None,
        transfer_inhibition_operation_id="operation",
        transfer_inhibition_quiescent=True,
        pending_operation_id=None,
    )
    monkeypatch.setattr(
        agent,
        "_vm_ha_standby_replacement_identity",
        lambda **kw: ("cluster", "owner", "generation"),
    )
    monkeypatch.setattr(
        agent,
        "standby_replacement_inhibition_operation_id",
        lambda *a, **kw: value if field == "inhibition" else "operation",
    )
    monkeypatch.setattr(agent, "vm_ha_status", lambda **kw: status)
    if field.endswith(".json"):
        (tmp_path / field).write_text(value)
    elif field != "inhibition":
        status[field] = value
    with pytest.raises(agent.StandbyReplacementNotReady) as raised:
        agent.verify_vm_ha_standby_replacement_quiescent("operation", state_dir=tmp_path)
    assert raised.value.reasons == (reason,)
    assert "foreign" not in encode_not_ready(raised.value.reasons)
