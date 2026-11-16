"""Actual recovery guards retain their known cause through public CLI output."""
import json
import shlex
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nebius_vpngw import cli
from nebius_vpngw.deploy.activation_recovery import Diagnosis, Recovery
from nebius_vpngw.deploy.recovery import Invocation, RegionalFailure, advice


def exhausted_owner(root, kind):
    owner = Recovery(root, execution="e" * 32, authority="a" * 64)
    owner.initialize()
    owner.initial("activate-member", state="started")
    for ordinal in range(3 if kind == "exhausted" else 1):
        attempt = owner.reserve("activate-member", Diagnosis("service", str(ordinal) * 64))
        attempt = owner.transition(attempt, "dispatched")
        attempt = owner.transition(attempt, "failed")
        owner.cleaned(attempt)
    return owner


@pytest.mark.parametrize("kind,code", [
    ("exhausted", "activation-recovery-exhausted"),
    ("unchanged", "unchanged-repair-refused"),
])
@pytest.mark.parametrize("command", ["apply"])
@pytest.mark.parametrize("output_format", ["text", "json"])
@pytest.mark.parametrize("cause", [None, RuntimeError, TimeoutError])
def test_guard_failure_preserves_cause_context_and_budget(
    tmp_path, monkeypatch, kind, code, command, output_format, cause
):
    owner = exhausted_owner(tmp_path / "state", kind)
    before = owner.path.read_bytes()
    config = tmp_path / "gateway's config.yaml"
    config.touch()

    def guard(*_args, **_kwargs):
        try:
            owner.reserve("activate-member", Diagnosis("service", ("f" if kind == "exhausted" else "0") * 64))
        except RuntimeError as error:
            if cause is not None:
                raise error from cause("private-underlying-failure")
            raise
        pytest.fail("A refused correction was admitted")

    monkeypatch.setattr(cli, "load_local_config" if command == "apply" else "_resolve_vm_ha_effective_config", guard)
    project_args = ["--project-id", "selected-project"] if command == "apply" else []
    result = CliRunner().invoke(cli.app, [command, "-c", str(config), *project_args,
                                          "--region", "selected-region", "--output-format", output_format])
    assert result.exit_code == 1
    assert owner.path.read_bytes() == before
    assert "private-underlying-failure" not in result.output
    assert "cause is unknown" not in result.output
    if output_format == "json":
        recovery = json.loads(result.stdout)["recovery"]
        assert recovery["reason_code"] == code
        repair = recovery["repair_command"]
        assert recovery["next_action"] and recovery["repair_prerequisite"]
        reason = recovery["reason"]
    else:
        assert result.stdout.count("Reason:") == 1
        assert result.stdout.count("Next action:") == 1
        assert result.stdout.count("Repair prerequisite:") == 1
        assert result.stdout.count("--repair") == 1
        assert "(" + code + ")" in result.stdout
        repair = next(line.strip() for line in result.stdout.splitlines()
                      if line.strip().startswith("nebius-vpngw apply "))
        reason = result.stdout
    assert ("three" if kind == "exhausted" else "same") in reason.lower()
    assert shlex.split(repair) == ["nebius-vpngw", "apply", "--local-config-file", str(config),
                                  *project_args, "--region", "selected-region", "--repair"]


@pytest.mark.parametrize("code", [
    "activation-recovery-exhausted", "unchanged-repair-refused", "ssh-bootstrap-conflict",
    "package-reservation-conflict",
    "managed-mtls-permissions",
])
def test_guard_codes_survive_report_and_pair_publication(code):
    child = advice(Invocation("apply", Path("generated HA.yaml")), codes=("predicate-" + code,))
    assert child.reason_code == code
    parent = advice(Invocation("pair apply", Path("pair config.json")),
                    RegionalFailure("b", {"recovery": child.mapping()}))
    assert parent.reason_code == code
    assert parent.reason.startswith("Region b:")
    assert "cause is unknown" not in parent.text()
    assert shlex.split(parent.repair_command) == ["nebius-vpngw", "pair", "apply", "pair config.json", "--repair"]


@pytest.mark.parametrize("command", ["apply"])
@pytest.mark.parametrize("output_format", ["text", "json"])
def test_mtls_permissions_failure_explains_reconstruction(tmp_path, monkeypatch, command, output_format):
    from nebius_vpngw.deploy.apply_diagnostics import ManagedMTLSActionFailure

    config = tmp_path / "gateway's HA config.yaml"
    config.touch()
    def fail(*_args, **_kwargs):
        raise ManagedMTLSActionFailure(b"managed mTLS action failed: managed mTLS state directory permissions are invalid\n")
    monkeypatch.setattr(cli, "load_local_config" if command == "apply" else "_resolve_vm_ha_effective_config", fail)
    result = CliRunner().invoke(cli.app, [command, "-c", str(config), "--region", "selected-region", "--output-format", output_format])
    assert result.exit_code == 1
    if output_format == "json":
        recovery = json.loads(result.stdout)["recovery"]
        assert recovery["reason_code"] == "managed-mtls-permissions"
        assert "private ownership or permissions" in recovery["reason"]
        assert "reconstruct" in recovery["next_action"]
        assert "foreign-owned" in recovery["repair_prerequisite"]
        repair = recovery["repair_command"]
    else:
        assert result.stdout.count("Reason:") == 1
        assert result.stdout.count("--repair") == 1
        assert "private ownership or permissions" in result.stdout
        repair = next(line.strip() for line in result.stdout.splitlines() if line.strip().startswith("nebius-vpngw apply "))
    assert shlex.split(repair) == ["nebius-vpngw", "apply", "--local-config-file", str(config), "--region", "selected-region", "--repair"]


@pytest.mark.parametrize("command", ["apply"])
@pytest.mark.parametrize("output_format", ["text", "json"])
def test_selected_ssh_conflict_explains_prerequisite_and_repair(
    tmp_path, monkeypatch, command, output_format
):
    from nebius_vpngw.deploy.provisioning_trust import TrustJournal

    config = tmp_path / "gateway's config.yaml"
    config.touch()
    desired = {"policy_sha256": "a" * 64, "members": ["gateway-1"],
               "targets": {"gateway-1": "gateway-1"}, "pins": {"gateway-1": "gateway-1"}}
    selected = {"receipt_sha256": "b" * 64, "projection_sha256": "c" * 64,
                "identities": {"gateway-1": "d" * 64}}
    with TrustJournal("project", "gateway", root=tmp_path / "trust") as owner:
        owner.begin(desired, approval="e" * 64, execution_id="f" * 32)
        owner.select(selected)
        before = owner.path.read_bytes()

        def guard(*_args, **_kwargs):
            owner.select({**selected, "receipt_sha256": "1" * 64})

        monkeypatch.setattr(cli, "load_local_config" if command == "apply" else "_resolve_vm_ha_effective_config", guard)
        result = CliRunner().invoke(cli.app, [command, "-c", str(config),
                                             "--region", "selected-region", "--output-format", output_format])
        assert result.exit_code == 1 and owner.path.read_bytes() == before
    assert "cause is unknown" not in result.output
    if output_format == "json":
        recovery = json.loads(result.stdout)["recovery"]
        assert recovery["reason_code"] == "ssh-bootstrap-conflict"
        assert "authenticated access" in recovery["repair_prerequisite"]
        assert recovery["next_action"]
        repair = recovery["repair_command"]
    else:
        assert result.stdout.count("Reason:") == result.stdout.count("Next action:") == 1
        assert result.stdout.count("Repair prerequisite:") == 1
        assert "ssh-bootstrap-conflict" in result.stdout
        repair = next(line.strip() for line in result.stdout.splitlines()
                      if line.strip().startswith("nebius-vpngw apply "))
    assert shlex.split(repair) == ["nebius-vpngw", "apply", "--local-config-file", str(config),
                                  "--region", "selected-region", "--repair"]


@pytest.mark.parametrize("command", ["apply"])
@pytest.mark.parametrize("output_format", ["text", "json"])
def test_package_reservation_conflict_explains_original_authority(
    tmp_path, monkeypatch, command, output_format
):
    from nebius_vpngw.deploy import package_reservation

    config = tmp_path / "gateway's config.yaml"
    config.touch()
    scope = (tmp_path / "command", "e" * 32, "a" * 64)
    request = {"action": "reserve", "approval": "b" * 64, "package": {"host": "member"}}
    token = package_reservation.SCOPE.set(scope)
    try:
        package_reservation.admit(request, "c" * 64)
        path = package_reservation.path_for(scope[0], scope[1], "member")
        before = path.read_bytes()

        def guard(*_args, **_kwargs):
            package_reservation.admit({**request, "approval": "d" * 64}, "c" * 64)

        monkeypatch.setattr(cli, "load_local_config" if command == "apply" else "_resolve_vm_ha_effective_config", guard)
        result = CliRunner().invoke(cli.app, [command, "-c", str(config),
                                             "--region", "selected-region", "--output-format", output_format])
        assert result.exit_code == 1 and path.read_bytes() == before
    finally:
        package_reservation.SCOPE.reset(token)
    assert "cause is unknown" not in result.output
    if output_format == "json":
        recovery = json.loads(result.stdout)["recovery"]
        assert recovery["reason_code"] == "package-reservation-conflict"
        assert "worker's outcome" in recovery["repair_prerequisite"]
        assert "original configuration" in recovery["next_action"]
        repair = recovery["repair_command"]
    else:
        assert result.stdout.count("Reason:") == result.stdout.count("Next action:") == 1
        assert result.stdout.count("Repair prerequisite:") == 1
        assert "package-reservation-conflict" in result.stdout
        repair = next(line.strip() for line in result.stdout.splitlines()
                      if line.strip().startswith("nebius-vpngw apply "))
    assert shlex.split(repair) == ["nebius-vpngw", "apply", "--local-config-file", str(config),
                                  "--region", "selected-region", "--repair"]
