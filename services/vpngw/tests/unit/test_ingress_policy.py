from __future__ import annotations

import io
import json
import subprocess
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

from nebius_vpngw import cli
from nebius_vpngw import ingress_policy as policy
from nebius_vpngw.config_loader import load_local_config, merge_with_peer_configs
from nebius_vpngw.schema import GatewayConfig


@pytest.mark.parametrize(
    "asset",
    ["nebius-vpngw-ingress.py", "nebius-vpngw-vm-ha.service"],
)
def test_replacement_artifact_requires_boot_and_reconnect_assets(asset):
    root = Path(policy.__file__).parent
    with zipfile.ZipFile(io.BytesIO(), "w") as archive:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".sh", ".conf", ".service"}:
                archive.writestr(
                    "nebius_vpngw/" + str(path.relative_to(root)),
                    b"outdated" if path.name == asset else path.read_bytes(),
                )
        with pytest.raises(policy.IngressCapabilityError, match="matching wheel"):
            policy.require_wheel(archive)


@pytest.mark.parametrize("ha", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("value", [None, [], ["${INGRESS_UNRESOLVED_TEST}"], ["bad/24"]])
def test_cli_rejects_sources_before_auth_locks_or_effects(
    monkeypatch, tmp_path, sample_config, ha, dry_run, value
):
    sample_config["gateway_group"]["vm_ha"] = {"enabled": ha}
    sample_config["gateway"]["ssh_allowed_cidrs"] = value
    monkeypatch.delenv("INGRESS_UNRESOLVED_TEST", raising=False)
    path = tmp_path / "gateway.config.yaml"
    path.write_text(yaml.safe_dump(sample_config))

    def unexpected(*args, **kwargs):
        pytest.fail("invalid ingress reached an operational boundary")

    for name in ("VMHAApplyLock", "_apply_impl", "_ensure_authentication"):
        monkeypatch.setattr(cli, name, unexpected)
    result = CliRunner().invoke(
        cli.app, ["apply", "-c", str(path), *(["--dry-run"] if dry_run else [])]
    )
    assert result.exit_code == 1
    assert "ssh_allowed_cidrs" in result.output
    assert "Traceback" not in result.output


def test_partial_network_preparation_loader_cannot_bypass_source_validation(
    tmp_path, sample_config
):
    sample_config["gateway"]["ssh_allowed_cidrs"] = []
    path = tmp_path / "gateway.yaml"
    path.write_text(yaml.safe_dump(sample_config))
    with pytest.raises(ValueError, match="ssh_allowed_cidrs"):
        load_local_config(path, validate_schema=False, allow_missing_placeholders=True)


def test_missing_table_install_preserves_ordinary_command_budget(tmp_path, monkeypatch):
    from nebius_vpngw.agent import local_commands

    monkeypatch.setattr(policy, "LOCK_FILE", tmp_path / "lock")
    monkeypatch.setattr(policy, "POLICY_FILE", tmp_path / "policy")
    desired = {"peer_ips": []}
    installed = False
    calls = []

    def bounded_run(argv, **kwargs):
        nonlocal installed
        calls.append(argv)
        if "tables" in argv:
            data = [policy.objects(desired)[0]] if installed else []
        elif "list" in argv:
            if not installed:
                return subprocess.CompletedProcess(argv, 1, "", "No such file or directory")
            data = policy.objects(desired)
        else:
            if "--check" not in argv:
                installed = True
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 0, json.dumps({"nftables": data}), "")

    monkeypatch.setattr(local_commands, "_bounded_run", bounded_run)
    budget = local_commands.CommandBudget(time.monotonic() + 30)
    token = local_commands.CURRENT.set(budget)
    try:
        policy.install(desired, run=local_commands.run)
        budget.require_success()
    finally:
        local_commands.CURRENT.reset(token)
    assert installed and budget.failures == []
    assert calls[0] == ["nft", "-j", "list", "tables", "inet"]


def test_bootstrap_policies_are_derived_per_instance(sample_config):
    plan = merge_with_peer_configs(sample_config, [])
    assert plan.gateway_group.ingress_policies[0] == policy.from_config(
        yaml.safe_load(plan.per_instance[0].config_yaml)
    )


def test_kernel_verification_rejects_added_accept_or_missing_hook():
    desired = {"peer_ips": []}
    for mutate in (
        lambda objects: objects[1]["chain"].update(prio=10),
        lambda objects: objects.pop(),
        lambda objects: objects.append(
            {
                "rule": {
                    "family": "inet",
                    "table": policy.TABLE,
                    "chain": "ingress",
                    "expr": [{"accept": None}],
                }
            }
        ),
    ):
        objects = policy.objects(desired)
        mutate(objects)

        def run(argv, objects=objects, **kwargs):
            data = objects[:1] if "tables" in argv else objects
            return SimpleNamespace(returncode=0, stdout=json.dumps({"nftables": data}))

        with pytest.raises(policy.IngressPolicyDrift, match="differs"):
            policy.verify(desired, run=run)


def test_failed_native_validation_never_applies_or_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, "LOCK_FILE", tmp_path / "lock")
    monkeypatch.setattr(policy, "POLICY_FILE", tmp_path / "policy")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if "tables" in argv:
            return SimpleNamespace(returncode=0, stdout='{"nftables":[]}')
        return SimpleNamespace(returncode=1, stdout="")

    with pytest.raises(RuntimeError, match="validation failed"):
        policy.install({"peer_ips": []}, run=run)
    assert len(calls) == 2 and "--check" in calls[-1]
    assert not policy.POLICY_FILE.exists()


def test_interrupted_activation_keeps_validated_boot_intent(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, "LOCK_FILE", tmp_path / "lock")
    monkeypatch.setattr(policy, "POLICY_FILE", tmp_path / "policy")
    desired = {"peer_ips": []}
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if "--check" in argv:
            assert not policy.POLICY_FILE.exists()
            return SimpleNamespace(returncode=0, stdout="")
        if "-f" in argv:
            assert policy.POLICY_FILE.read_text() == policy.serialized(desired)
            raise TimeoutError("interrupted native activation")
        return SimpleNamespace(returncode=0, stdout='{"nftables":[]}')

    with pytest.raises(TimeoutError):
        policy.install(desired, run=run)
    assert len(calls) == 3
    assert policy.validate(json.loads(policy.POLICY_FILE.read_text())) == desired


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(returncode=1, stdout=""),
        SimpleNamespace(returncode=0, stdout="not-json"),
        SimpleNamespace(returncode=0, stdout='{"nftables":{}}'),
    ],
)
def test_unreadable_inventory_never_becomes_repair_authority(tmp_path, monkeypatch, result):
    monkeypatch.setattr(policy, "LOCK_FILE", tmp_path / "lock")
    monkeypatch.setattr(policy, "POLICY_FILE", tmp_path / "policy")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return result

    with pytest.raises(RuntimeError) as error:
        policy.install({"peer_ips": []}, run=run)
    assert not isinstance(error.value, policy.IngressPolicyDrift)
    assert calls == [["nft", "-j", "list", "tables", "inet"]]
    assert not policy.POLICY_FILE.exists()


@pytest.mark.parametrize("value", [None, [], ["0.0.0.0/0"], ["203.0.113.4/32"]])
def test_removed_ssh_field_is_rejected(value):
    with pytest.raises(ValueError, match="ssh_allowed_cidrs"):
        GatewayConfig(local_asn=65000, local_prefixes=["10.0.0.0/8"], ssh_allowed_cidrs=value)
    with pytest.raises(ValueError):
        policy.validate({"ssh_allowed_cidrs": value, "peer_ips": []})


def test_peer_policy_contains_no_tcp_or_source_cidr_rules():
    rules = json.dumps(policy.objects({"peer_ips": ["198.51.100.10", "2001:db8::1"]}))
    assert '"tcp"' not in rules
    assert '"prefix"' not in rules
    assert '"esp"' in rules and "4500" in rules and "500" in rules
    assert set(policy.from_config({"connections": []})) == {"peer_ips"}
