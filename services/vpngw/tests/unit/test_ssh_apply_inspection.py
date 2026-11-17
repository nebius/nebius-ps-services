"""A completed trust transition must not consume later status probe budgets."""

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from nebius_vpngw import cli
from nebius_vpngw.deploy import ssh_policy
from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStatus


@pytest.mark.parametrize("persist,stopped", [(True, False), (False, False), (True, True)])
def test_inspection_uses_canonical_trust_only_after_publication(monkeypatch, persist, stopped):
    hosts = {f"gateway-{n}": f"192.0.2.{n + 1}" for n in range(2)}
    members = [
        NS(instance_name=name, hostname=name, public_ip=address, compute_id=name, disk_id=name)
        for name, address in hosts.items()
    ]
    local = {"project_id": "project", "gateway_group": {"vm_spec": {}}}
    plan = NS(
        gateway_group=NS(name="gateway", region="region"),
        iter_instance_configs=lambda: iter(members),
    )
    state = NS(
        status=VMHALifecycleStatus.ACTIVE,
        transaction=NS(accepted_cloud_operation_id=None),
        members=members,
    )
    snapshot = NS(state=state)
    scope = NS(digest="a" * 64)
    published = []
    candidate = NS(managed_action="convert", pins=("exact-0", "exact-1"))
    canonical = NS(pins=candidate.pins)
    context = cli._StatusSSHContext(
        "ubuntu", None, None, False, {name: canonical for name in hosts}, frozenset(), True
    )

    class Manager:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_vm_public_ip(self, name):
            return hosts[name]

        def discover_vm_ha_members(self, _):
            return hosts

        def vm_ha_ssh_trust_bindings(self, *args, **kwargs):
            return {}

        def get_ha_instance(self, name):
            return "stopped" if stopped and name == "gateway-1" else "running"

    def collect(**kwargs):
        selected = kwargs["ssh_context"].policies
        expected = canonical if persist and not stopped else candidate
        assert all(value is expected for value in selected.values())
        assert all(value.pins == candidate.pins for value in selected.values())
        return NS(view=NS(overall="BLOCKED"))

    monkeypatch.setattr(cli, "_load_config_with_region_override", lambda *a, **k: local)
    monkeypatch.setattr(cli, "merge_with_peer_configs", lambda *a: plan)
    monkeypatch.setattr(cli, "_enforce_command_applicability", lambda *a: None)
    monkeypatch.setattr(cli, "_ensure_authentication", lambda **k: None)
    monkeypatch.setattr(cli, "VMManager", Manager)
    monkeypatch.setattr(
        cli, "VMHALifecycleStore", lambda *a: NS(read_hardened=lambda **k: snapshot)
    )
    monkeypatch.setattr(cli, "_vm_ha_ssh_trust_scope", lambda *a, **k: scope)
    monkeypatch.setattr(cli, "_build_status_ssh_context", lambda *a, **k: context)
    monkeypatch.setattr(cli, "_collect_vm_ha_status_snapshot", collect)
    monkeypatch.setattr(cli, "has_unresolved_tunnel_psk_placeholders", lambda _: False)
    monkeypatch.setattr(cli, "publish_vm_ha_ssh_trust", published.append)
    monkeypatch.setattr(ssh_policy, "prepare_retained_vm_ha_ssh_policy", lambda *a, **k: candidate)
    monkeypatch.setattr("nebius_vpngw.deploy.vm_ha_cloud.instance_cloud_state", lambda value: value)
    token = ssh_policy.APPLY_POLICIES.set({scope.digest: candidate})
    try:
        cli._inspect_vm_ha_command_status(
            Path("config.yaml"), prepare_ssh=True, persist_ssh=persist
        )
        assert published == ([candidate] if persist and not stopped else [])
        assert (scope.digest in ssh_policy.APPLY_POLICIES.get()) == (not persist or stopped)
    finally:
        ssh_policy.APPLY_POLICIES.reset(token)
