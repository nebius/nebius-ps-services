"""Migration trust publication uses the exact existing apply approval path."""

from pathlib import Path

import pytest

from nebius_vpngw import cli
from nebius_vpngw.vm_ha_command import (
    VMHACommandClassification,
    VMHACommandHealth,
    VMHACommandOutcome,
    VMHACommandResult,
)


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    ("kind", "action", "destructive", "admitted"),
    [
        ("migration", "migrate", False, True),
        ("migration", "migrate", True, False),
        ("migration", "repair", False, False),
        ("migration", "rotate", False, False),
        ("apply-convergence", "migrate", False, False),
        ("recovery", "migrate", False, False),
        ("provisioning", "create", False, False),
    ],
)
def test_migration_trust_stays_within_exact_approval(kind, action, destructive, admitted, dry_run):
    path = Path("gateway.vm-ha.config.yaml")
    prior = VMHACommandResult(
        outcome=VMHACommandOutcome.ACTION_REQUIRED,
        classification=VMHACommandClassification.CANDIDATE_READY,
        health=VMHACommandHealth.NOT_CONFIGURED,
        effective_config_file=path,
    )
    report = cli._VMHAApplyPlanReport(
        kind=kind,
        digest="d" * 64,
        engine_digest="e" * 64,
        effects=("retain-active-identities", "publish-managed-ssh-trust"),
        has_destructive_changes=destructive,
        managed_ssh_action=action,
        impact=cli._vm_ha_apply_plan_impact(kind, has_destructive_changes=destructive),
    )
    result = cli._vm_ha_apply_plan_result(
        config_path=path, prior=prior, report=report, dry_run=dry_run
    )
    if admitted:
        assert result.approval is not None
        assert result.approval.kind == "migration"
        assert result.approval.digest == report.digest
        assert result.approval.effects == report.effects
        assert result.impact.approval_required
        assert result.outcome == (
            VMHACommandOutcome.PLANNED if dry_run else VMHACommandOutcome.ACTION_REQUIRED
        )
    else:
        assert result.approval is None
        assert result.classification == VMHACommandClassification.EXTERNAL_PREREQUISITE
