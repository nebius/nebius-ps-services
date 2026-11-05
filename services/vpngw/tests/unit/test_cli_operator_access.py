from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from nebius_vpngw import cli
from nebius_vpngw.deploy import operator_access


@pytest.fixture
def configured(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("fixture")
    local = {"project_id": "project-test", "gateway_group": {"name": "test", "vm_spec": {}}}
    group = SimpleNamespace(name="test", region="region-test", vm_spec={})
    plan = SimpleNamespace(
        gateway_group=group,
        vm_ha=None,
        iter_instance_configs=lambda: [SimpleNamespace(hostname="test-0")],
    )
    state = {"value": "RUNNING"}
    instance = SimpleNamespace(status=SimpleNamespace(state="RUNNING"))

    class Manager:
        def __init__(self, **kwargs):
            self._ordinary_ssh_preflight_snapshot = {"test-0": (instance, "192.0.2.1")}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def close(self):
            pass

        def discover_ordinary_gateway_members(self, group):
            instance.status.state = state["value"]
            return {"test-0": "192.0.2.1"}

        def _get_client(self):
            return object()

        def _vm_ha_ssh_member_signature(self, item):
            return ("compute-test", "test-0", "project-test", "1", "192.0.2.1", "cloud-digest")

        def _get_vm_by_id_for_ordinary_ssh_preflight(self, client, identifier):
            return instance

    monkeypatch.setattr(cli, "load_local_config", lambda _: local)
    monkeypatch.setattr(cli, "merge_with_peer_configs", lambda *_: plan)
    monkeypatch.setattr(cli, "_gateway_ssh_client_auth", lambda _: object())
    monkeypatch.setattr(cli, "_apply_operator_auth_token", lambda: None)
    monkeypatch.setattr(cli, "_existing_gateway_ssh_policy", lambda *_: None)
    monkeypatch.setattr(cli, "VMHAApplyLock", lambda **_: nullcontext())
    monkeypatch.setattr(cli, "VMManager", Manager)
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: "test passphrase")
    return path, state


def test_cli_partial_grant_reports_exact_key_locations_and_nonzero(
    configured, tmp_path, monkeypatch
):
    config, state = configured
    private = tmp_path / "operator" / "id_ed25519"

    def run(self, name, key_factory, *, on_key):
        for target in self.targets:
            target.assert_current()
        key = key_factory()
        on_key(key)
        return key, (operator_access.EnrollmentStatus("test-0", installed=True),)

    monkeypatch.setattr(operator_access.OperatorEnrollment, "run", run)
    result = CliRunner().invoke(
        cli.app,
        [
            "access",
            "add-operator",
            "alice",
            "-c",
            str(config),
            "--generate-ssh-key",
            "--key-output",
            str(private),
        ],
    )
    assert result.exit_code == 1, result.output
    assert f"Private key file: {private}" in result.output
    assert f"Public key file: {private}.pub" in result.output
    assert "installed=yes" in result.output
    assert "Successful grants remain" in result.output
    assert "PRIVATE KEY" not in result.output and "test passphrase" not in result.output
    assert private.exists()


def test_cli_stopped_member_blocks_before_local_key_generation(configured, tmp_path):
    config, state = configured
    state["value"] = "STOPPED"
    private = tmp_path / "operator" / "id_ed25519"
    result = CliRunner().invoke(
        cli.app,
        [
            "access",
            "add-operator",
            "alice",
            "-c",
            str(config),
            "--generate-ssh-key",
            "--key-output",
            str(private),
        ],
    )
    assert result.exit_code == 1
    assert "does not start stopped members" in result.output
    assert not private.exists()
