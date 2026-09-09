"""Reservation identity includes complete, deterministic Slurm timestamps."""

import subprocess

import pytest

from nebius_cxcli import cli
from test_soperator_checks_execution import Cluster, execution
from test_soperator_checks_execution import policy as policy


def test_checks_pin_timestamp_display_for_reservation_identity(tmp_path, policy):
    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    observed = []

    def slurm(command):
        observed.append(command)
        return cluster.slurm(command.removeprefix("env SLURM_TIME_FORMAT=standard TZ=UTC "))

    runner.slurm = slurm
    runner._reservation("reserve")
    assert observed[0] == (
        "env SLURM_TIME_FORMAT=standard TZ=UTC scontrol show reservation reserve -o"
    )


def test_checks_fingerprint_preserves_complete_fields_with_spaces(tmp_path, policy):
    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    # Exercise the canonical field parser independently of the display override.
    runner.slurm = lambda command: cluster.slurm(
        command.removeprefix("env SLURM_TIME_FORMAT=standard TZ=UTC ")
    )
    cluster.reservation_fields["Comment"] = "first observation at 01:00"
    first = runner._reservation("reserve")
    runner.state["reservationFingerprint"] = first["fingerprint"]
    cluster.reservation_fields["Comment"] = "first observation at 02:00"
    with pytest.raises(RuntimeError, match="contract changed"):
        runner._reservation("reserve")


@pytest.mark.parametrize("field", ["StartTime", "EndTime"])
@pytest.mark.parametrize(
    "value", ["", "2026-01-01", "Unknown", "2026-02-30T00:00:00", "2026-01-01 01:00:00"]
)
def test_checks_reject_incomplete_or_invalid_reservation_times(tmp_path, policy, field, value):
    cluster = Cluster(policy)
    cluster.reservation_fields[field] = value
    runner = execution(tmp_path, policy, cluster)
    runner.slurm = lambda command: cluster.slurm(
        command.removeprefix("env SLURM_TIME_FORMAT=standard TZ=UTC ")
    )
    with pytest.raises(RuntimeError, match="reservation timestamps"):
        runner._reservation("reserve")
    assert not cluster.writes
    assert not runner.path.exists()


def test_upgrade_preimages_pin_timestamp_display(monkeypatch):
    observed = []

    def run(namespace, command, **kwargs):
        observed.append((namespace, command, kwargs))
        return subprocess.CompletedProcess(
            [],
            0,
            "ReservationName=customer StartTime=2026-01-01T01:00:00 "
            "EndTime=2026-01-02T01:00:00 Users=alice\n",
            "",
        )

    monkeypatch.setattr(cli, "_run_soperator_upgrade_login_command", run)
    preimages = cli._soperator_upgrade_reservation_preimages(namespace="soperator", extra_env=None)
    assert observed[0][1] == "env SLURM_TIME_FORMAT=standard TZ=UTC scontrol show reservation -o"
    assert "StartTime=2026-01-01T01:00:00" in preimages[0].record
    assert "EndTime=2026-01-02T01:00:00" in preimages[0].record
