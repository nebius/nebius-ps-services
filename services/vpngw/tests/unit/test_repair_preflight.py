"""Repair admission reports actionable causes without granting repair authority."""

import json
from pathlib import Path
from types import SimpleNamespace

import grpc
import paramiko
import pytest
from nebius.aio.service_error import RequestError
from typer.testing import CliRunner

from nebius_vpngw import cli, vm_ha_managed_credentials
from nebius_vpngw.deploy import release_policy, repair_apply
from nebius_vpngw.deploy.recovery import (
    Invocation,
    RegionalFailure,
    RepairInspectionFailure,
    advice,
    repair_inspection,
)
from nebius_vpngw.nebius_auth import NebiusCLIAuthenticationError
from nebius_vpngw.vm_ha_credentials import VMHACredentialIdentityError


def provider_error(code):
    return RequestError(SimpleNamespace(code=code, message="private-provider-sentinel"))


@pytest.mark.parametrize("ha", [False, True])
@pytest.mark.parametrize(
    "error,code",
    [
        (provider_error(grpc.StatusCode.UNAUTHENTICATED), "provider-authentication-failed"),
        (provider_error(grpc.StatusCode.PERMISSION_DENIED), "provider-permission-denied"),
        (provider_error(grpc.StatusCode.UNAVAILABLE), "provider-unavailable"),
        (NebiusCLIAuthenticationError("private-token-sentinel"), "operator-authentication-failed"),
        (RuntimeError("private-provider-sentinel permission denied"), "unknown-failure"),
    ],
)
def test_repair_discovery_failure_identifies_check_and_typed_cause(ha, error, code):
    def fail(_):
        raise error

    manager = SimpleNamespace(
        discover_vm_ha_members=fail,
        discover_ordinary_gateway_members=fail,
    )
    with pytest.raises(Exception) as caught:
        repair_apply.inspect(
            manager=manager,
            plan=SimpleNamespace(vm_ha=object() if ha else None, gateway_group=object()),
            local={},
            path=Path("config.yaml"),
            instances=(),
        )
    recovery = advice(Invocation("apply", repair=True), caught.value)
    assert recovery.reason_code == code
    assert "retained Compute members" in recovery.reason
    assert "private-" not in json.dumps(recovery.mapping()) + recovery.text()
    assert recovery.destroy_command is None


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_public_repair_failure_keeps_one_retry_and_no_order_gate(
    monkeypatch,
    tmp_path,
    output_format,
):
    path = tmp_path / "config.yaml"
    path.touch()

    def fail(**_):
        def discover(_):
            raise provider_error(grpc.StatusCode.PERMISSION_DENIED)

        repair_apply.inspect(
            manager=SimpleNamespace(discover_vm_ha_members=discover),
            plan=SimpleNamespace(vm_ha=object(), gateway_group=object()),
            local={},
            path=path,
            instances=(),
        )

    monkeypatch.setattr(cli, "load_local_config", lambda _: {})
    monkeypatch.setattr(cli, "merge_with_peer_configs", lambda *_: SimpleNamespace())
    monkeypatch.setattr(cli, "_apply_impl", fail)
    result = CliRunner().invoke(
        cli.app,
        ["apply", "-c", str(path), "--repair", "--output-format", output_format],
        input="y\n",
    )
    assert result.exit_code == 1
    assert "Continue with software repair?" not in result.output
    assert "private-" not in result.output
    if output_format == "json":
        recovery = json.loads(result.stdout)["recovery"]
        assert recovery["reason_code"] == "provider-permission-denied"
        assert "retained Compute members" in recovery["reason"]
        assert recovery["resume_command"] == recovery["repair_command"]
    else:
        assert result.stdout.count("nebius-vpngw apply") == 1
        assert "normal apply is not a prerequisite" in result.stdout
        assert "Step 2" not in result.stdout


def test_confirmation_remains_default_no_and_describes_direct_repair(monkeypatch, capsys):
    prompts = []
    monkeypatch.setattr(
        repair_apply.typer,
        "confirm",
        lambda text, *, default: prompts.append((text, default)) or False,
    )
    assert repair_apply.confirm(SimpleNamespace(intent={"targets": []})) is False
    assert prompts == [("Continue with software repair?", False)]
    assert "Normal apply is recommended for routine convergence." in capsys.readouterr().out


@pytest.mark.parametrize(
    "error,code",
    [
        (
            paramiko.AuthenticationException("private-ssh-sentinel"),
            "ssh-client-authentication-failed",
        ),
        (
            VMHACredentialIdentityError("authentication-failed"),
            "runtime-credential-authentication-failed",
        ),
        (release_policy.ReleaseBlocked("newer"), "gateway-release-newer"),
        (TimeoutError("private-timeout-sentinel"), "operation-timeout"),
    ],
)
def test_repair_inspection_preserves_specific_causes_through_wrappers(error, code):
    with pytest.raises(RepairInspectionFailure) as caught, repair_inspection("guest-release"):
        raise RuntimeError("private-wrapper-sentinel") from error
    recovery = advice(Invocation("apply", repair=True), caught.value)
    assert recovery.reason_code == code
    assert "gateway release over authenticated SSH" in recovery.reason
    assert "private-" not in recovery.text() + json.dumps(recovery.mapping())
    parent = advice(
        Invocation("pair apply", repair=True),
        RegionalFailure("b", {"recovery": recovery.mapping()}),
    )
    assert parent.reason_code == code
    assert "private-" not in parent.text()


@pytest.mark.parametrize("check", list(RepairInspectionFailure.CHECKS))
def test_each_real_ha_inspection_boundary_reports_its_own_failure(monkeypatch, tmp_path, check):
    names = ("gateway-0", "gateway-1")
    targets = {name: f"192.0.2.{i + 1}" for i, name in enumerate(names)}
    manager = SimpleNamespace(
        project_id="project-a",
        _management_key_path=None,
        discover_vm_ha_members=lambda _: targets,
        _vm_ha_ssh_preflight_snapshot={
            name: (SimpleNamespace(state="RUNNING", name=name), address)
            for name, address in targets.items()
        },
    )
    monkeypatch.setattr(
        repair_apply,
        "retained_binding",
        lambda vm: {
            "project": "project-a",
            "compute": "vm-" + vm.name,
            "disk": "disk-" + vm.name,
        },
    )
    monkeypatch.setattr(repair_apply.repair_ssh, "ha_bindings", lambda *_: {})
    monkeypatch.setattr(repair_apply.repair_ssh, "bindings", lambda *_: {})
    monkeypatch.setattr(cli, "_vm_ha_ssh_trust_scope", lambda *a, **kw: object())
    monkeypatch.setattr(repair_apply, "require_vm_ha_ssh_policy", lambda *a, **kw: object())
    wheel = tmp_path / "release.whl"
    wheel.write_bytes(b"test-artifact")
    ssh = SimpleNamespace(resolve_vm_ha_agent_artifact=lambda: SimpleNamespace(path=wheel))
    monkeypatch.setattr(repair_apply, "SSHPush", lambda **_: ssh)
    monkeypatch.setattr(
        vm_ha_managed_credentials, "inspect_managed_vm_ha_credentials", lambda **_: None
    )
    monkeypatch.setattr(release_policy, "require_targets", lambda *a, **kw: None)
    monkeypatch.setattr(repair_apply, "require_package_prerequisites", lambda _: None)
    boundaries = {
        "members": (manager, "discover_vm_ha_members"),
        "ssh-authority": (repair_apply.repair_ssh, "ha_bindings"),
        "ssh-policy": (repair_apply, "require_vm_ha_ssh_policy"),
        "artifact": (ssh, "resolve_vm_ha_agent_artifact"),
        "runtime-credentials": (vm_ha_managed_credentials, "inspect_managed_vm_ha_credentials"),
        "guest-release": (release_policy, "require_targets"),
        "dependencies": (repair_apply, "require_package_prerequisites"),
    }
    original = RuntimeError("private-boundary-sentinel")

    def fail(*_a, **_kw):
        raise original

    owner, method = boundaries[check]
    monkeypatch.setattr(owner, method, fail)
    with pytest.raises(RepairInspectionFailure) as caught:
        repair_apply.inspect(
            manager=manager,
            plan=SimpleNamespace(
                vm_ha=object(), gateway_group=SimpleNamespace(name="gateway", region="region-a")
            ),
            local={"gateway_group": {}},
            path=tmp_path / "config.yaml",
            instances=tuple(
                SimpleNamespace(
                    hostname=name,
                    config_yaml="selected-config",
                    vm_ha_node=SimpleNamespace(node_id=name),
                )
                for name in names
            ),
        )
    assert caught.value.__cause__ is original
    assert caught.value.check == check
    recovery = advice(Invocation("apply", repair=True), caught.value)
    assert recovery.reason_code == "unknown-failure"
    assert RepairInspectionFailure.CHECKS[check] in recovery.reason
    assert "private-" not in recovery.text()
