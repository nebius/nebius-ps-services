import json
from dataclasses import replace

import pytest
from tests.unit.test_apply_result import plan

from nebius_vpngw import cli
from nebius_vpngw.deploy.apply_command import CommandJournal
from nebius_vpngw.deploy.apply_diagnostics import Failure, VerificationFailure
from nebius_vpngw.deploy.apply_result import ApplyReport


def test_first_predicate_survives_later_cloud_timeout(monkeypatch):
    clock = [0.0]
    values = iter([{"reasons": ["bgp-not-ready", "sensitive.example"]}, TimeoutError("private")])

    def fetch(**kwargs):
        value = next(values)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(cli, "_fetch_vm_ha_agent_status", fetch)
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    with pytest.raises(VerificationFailure) as caught:
        cli._wait_for_vm_ha_agent_status(
            predicate=lambda payload: False,
            failed_predicate=lambda payload: "cloud-route-receipt-unproven",
            phase="verify-owner",
            timeout_seconds=2,
            poll_seconds=1,
        )
    error = caught.value
    assert error.first == Failure(
        "verify-owner", "cloud-route-receipt-unproven", ("bgp-not-ready",)
    )
    assert error.latest.predicate == "status-observation-unavailable"
    assert "private" not in str(error)
    error.first = replace(error.first, lock_reinstated=False)
    reasons = cli._vm_ha_convergence_diagnostics(error, cli._VMHAProgressPhase.EXECUTE_APPLY)
    assert reasons[0] == "phase-verify-owner"
    assert "apply-lock-reinstatement-unverified" in reasons


def test_report_and_private_journal_retain_first_failure(tmp_path):
    journal = CommandJournal("project", "gateway", root=tmp_path / "journal")
    report = ApplyReport(journal=journal, started=True)
    with journal:
        journal.begin(plan())
    first = Failure("verify-owner", "required-bgp-prefixes-not-learned", lock_reinstated=True)
    report.failed(VerificationFailure(first, first))
    report.failed(VerificationFailure(Failure("verify-owner", "apply-lock-held"), first))
    assert report.mapping()["failure"] == first.mapping()
    stored = list((journal.root / "failures").glob("*.json"))
    assert len(stored) == 1
    assert json.loads(stored[0].read_text())["failure"]["predicate"] == first.predicate
    assert stored[0].stat().st_mode & 0o777 == 0o600
    assert journal.snapshot()["transaction"]["phase"] == "pending"


def test_resume_reports_original_failing_predicate_before_lock_consequence(tmp_path):
    journal = CommandJournal("project", "gateway", root=tmp_path / "journal")
    original = Failure("verify-owner", "required-bgp-prefixes-not-learned", lock_reinstated=True)
    with journal:
        journal.begin(plan())
    journal.record_failure(original.mapping())
    report = ApplyReport(started=True)
    report.bind(journal, resumed=True)
    report.failed(VerificationFailure(Failure("verify-owner", "apply-lock-held"), original))
    assert report.failure == original
    assert report.mapping()["failure"]["predicate"] == original.predicate
    unrelated = ApplyReport()
    unrelated.bind(journal, resumed=False)
    assert unrelated.failure is None


def test_text_diagnostics_keep_first_and_latest_activation_observations():
    first = Failure(
        "verify-owner", "forwarding-fenced", ("apply-lock-held",), lock_reinstated=True
    )
    latest = Failure(
        "verify-owner", "forwarding-fenced", ("origination-preparation-unready",)
    )
    error = VerificationFailure(first, latest)
    projected = cli._vm_ha_convergence_diagnostics(error, cli._VMHAProgressPhase.EXECUTE_APPLY)
    assert projected[:3] == (
        "phase-verify-owner", "predicate-forwarding-fenced", "apply-lock-held"
    )
    assert "apply-lock-reinstated" in projected
    assert "latest-phase-verify-owner" in projected
    assert "latest-predicate-forwarding-fenced" in projected
    assert "latest-origination-preparation-unready" in projected
    report = ApplyReport(started=True)
    report.failed(error)
    assert report.mapping()["failure"] == first.mapping()


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (cli._VMHAAgentStatusStale("sensitive"), "cause-status-not-converged"),
        (cli._VMHAAgentStatusPermanent("sensitive"), "cause-status-invalid-or-foreign"),
    ],
)
def test_well_formed_unready_status_is_distinct_from_malformed_evidence(error, reason):
    projected = cli._vm_ha_convergence_diagnostics(error, cli._VMHAProgressPhase.EXECUTE_APPLY)
    assert reason in projected
    assert "cause-invalid-data" not in projected
    assert "sensitive" not in str(projected)
