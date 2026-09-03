from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nebius_vpngw.deploy.ssh_policy import VMHASSHTrustScope
from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStatus
from nebius_vpngw.deploy.vm_manager import VMManager
from nebius_vpngw.schema import VMHARole


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["ssh"], returncode=returncode, stdout=stdout, stderr=""
    )


def _fake_ssh_run_for_esp4(
    esp4_result: subprocess.CompletedProcess[str],
) -> object:
    def fake_run(
        cmd: Sequence[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        remote_cmd = str(cmd[-1])
        if remote_cmd == "echo connected":
            return _completed()
        if "cloud-init status" in remote_cmd:
            return _completed("status: done\n")
        if "python3 -m pip --version" in remote_cmd:
            return _completed("pip 24.0\n")
        if "esp4-reboot-pending" in remote_cmd:
            return esp4_result
        if "dpkg -l strongswan frr" in remote_cmd:
            return _completed("ii  strongswan  5.9\nii  frr  10.5\nactive\n")
        raise AssertionError(f"unexpected command: {remote_cmd}")

    return fake_run


@pytest.mark.parametrize(
    ("esp4_result", "esp4_ready", "esp4_reboot_pending", "message_fragment"),
    [
        (_completed(returncode=0), True, False, "ESP4 ready"),
        (
            _completed(stdout="reboot-pending\n", returncode=75),
            False,
            True,
            "waiting for gateway reboot",
        ),
        (_completed(stdout="blocked\n", returncode=1), False, False, "ESP4 is not ready"),
    ],
)
def test_check_vm_health_reports_esp4_states(
    monkeypatch: pytest.MonkeyPatch,
    esp4_result: subprocess.CompletedProcess[str],
    esp4_ready: bool,
    esp4_reboot_pending: bool,
    message_fragment: str,
) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr("subprocess.run", _fake_ssh_run_for_esp4(esp4_result))

    health = VMManager(project_id="project-test", region="eu-west1").check_vm_health(
        "nebius-vpn-gw-0",
        "203.0.113.10",
    )

    assert health["reachable"] is True
    assert health["cloud_init_complete"] is True
    assert health["esp4_ready"] is esp4_ready
    assert health["esp4_reboot_pending"] is esp4_reboot_pending
    assert message_fragment in health["message"]


def test_check_vm_health_fails_immediately_on_host_identity_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["ssh"],
            returncode=255,
            stdout=b"",
            stderr=b"Host key verification failed.\n",
        ),
    )

    with pytest.raises(RuntimeError, match="SSH host identity verification failed"):
        VMManager(project_id="project-test", region="eu-west1").check_vm_health(
            "nebius-vpn-gw-0",
            "203.0.113.10",
        )


def test_existing_member_identity_probe_uses_logical_host_alias(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot = tmp_path / "known_hosts"
    snapshot.write_text("fixture\n", encoding="utf-8")
    policy = SimpleNamespace(
        known_hosts_file=snapshot,
        assert_current=lambda: None,
        pin_target_for=lambda hostname: hostname,
    )
    observed: list[str] = []

    def run(command, **kwargs):
        observed.extend(str(item) for item in command)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr("subprocess.run", run)
    VMManager(
        project_id="project-test", region="eu-west1", ssh_policy=policy
    ).verify_vm_ha_existing_identities({"gateway-0": "203.0.113.10"})

    assert "HostKeyAlias=gateway-0" in observed
    assert "ubuntu@203.0.113.10" in observed


def test_management_key_reaches_existing_member_probe_without_prompts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key_path = tmp_path / "management-key"
    policy = SimpleNamespace(
        known_hosts_file=tmp_path / "known_hosts",
        assert_current=lambda: None,
        pin_target_for=lambda hostname: hostname,
    )
    observed: list[str] = []

    def run(command, **kwargs):
        observed.extend(str(item) for item in command)
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr("subprocess.run", run)
    VMManager(
        project_id="project-test",
        region="eu-west1",
        ssh_policy=policy,
        management_key_path=key_path,
    ).verify_vm_ha_existing_identities({"gateway-0": "203.0.113.10"})

    assert observed[observed.index("-i") + 1] == str(key_path)
    assert "BatchMode=yes" in observed
    assert "StrictHostKeyChecking=yes" in observed
    assert f"UserKnownHostsFile={policy.known_hosts_file}" in observed
    assert "HostKeyAlias=gateway-0" in observed


def test_management_key_reaches_vm_health_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    key_path = tmp_path / "management-key"
    policy = SimpleNamespace(
        known_hosts_file=tmp_path / "known_hosts",
        assert_current=lambda: None,
        pin_target_for=lambda hostname: hostname,
    )
    observed: list[list[str]] = []

    def run(command, **kwargs):
        observed.append([str(item) for item in command])
        return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"")

    monkeypatch.setattr("subprocess.run", run)
    VMManager(
        project_id="project-test",
        region="eu-west1",
        ssh_policy=policy,
        management_key_path=key_path,
    ).check_vm_health("gateway-0", "203.0.113.10", username="operator")

    assert observed[0][observed[0].index("-i") + 1] == str(key_path)
    assert "StrictHostKeyChecking=yes" in observed[0]
    assert f"UserKnownHostsFile={policy.known_hosts_file}" in observed[0]
    assert "HostKeyAlias=gateway-0" in observed[0]
    assert "operator@203.0.113.10" in observed[0]


def test_vm_health_uses_configured_username_for_every_ssh_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("time.sleep", lambda _: None)
    delegate = _fake_ssh_run_for_esp4(_completed())
    observed: list[list[str]] = []

    def run(command, *args, **kwargs):
        observed.append([str(item) for item in command])
        return delegate(command, *args, **kwargs)

    monkeypatch.setattr("subprocess.run", run)

    VMManager(project_id="project-test", region="eu-west1").check_vm_health(
        "gateway-0",
        "203.0.113.10",
        username="operator",
    )

    assert len(observed) == 5
    assert all("operator@203.0.113.10" in command for command in observed)


@pytest.mark.parametrize(
    ("recreate", "expected_enrollment"),
    [
        (False, {"gateway-1"}),
        (True, {"gateway-0", "gateway-1"}),
    ],
)
def test_ordinary_policy_prepins_fresh_members_and_verifies_retained_members(
    monkeypatch: pytest.MonkeyPatch,
    recreate: bool,
    expected_enrollment: set[str],
) -> None:
    manager = VMManager(
        project_id="project-test",
        region="eu-west1",
        management_key_path=Path("/tmp/management-key"),
    )
    spec = SimpleNamespace(name="gateway", region="eu-west1", instance_count=2, vm_ha=None)
    instances = (
        SimpleNamespace(hostname="gateway-0", external_ip="203.0.113.10"),
        SimpleNamespace(hostname="gateway-1", external_ip="203.0.113.11"),
    )
    policy = object()

    def binding() -> None:
        return None

    verified: list[tuple[dict[str, str], object, str]] = []

    monkeypatch.setattr(
        manager,
        "discover_ordinary_gateway_members",
        lambda _spec: {"gateway-0": "203.0.113.10"},
    )
    monkeypatch.setattr(
        manager,
        "ordinary_ssh_trust_bindings",
        lambda _spec, *, retained_hosts: {"gateway-0": binding},
    )
    monkeypatch.setattr(
        manager,
        "verify_ordinary_existing_identities",
        lambda existing, *, policy, username: verified.append((existing, policy, username)),
    )
    recovered = object()
    monkeypatch.setattr(
        manager,
        "recover_ordinary_ssh_host_keys",
        lambda hostnames, *, spec: recovered,
        raising=False,
    )
    observed: dict[str, object] = {}

    def require_policy(hosts, **kwargs):
        observed["hosts"] = tuple(hosts)
        observed.update(kwargs)
        return policy

    monkeypatch.setattr("nebius_vpngw.deploy.vm_manager.require_vm_ha_ssh_policy", require_policy)

    result = manager.prepare_ordinary_ssh_policy(
        spec,
        instances,
        trust_scope=VMHASSHTrustScope(
            tenant_id="tenant-test",
            project_id="project-test",
            region_id="eu-west1",
            gateway_name="gateway",
            cluster_id="ordinary-v1",
        ),
        recreate=recreate,
        management_public_key="ssh-ed25519 fixture",
        dry_run=False,
        username="operator",
    )

    assert result is policy
    assert observed["hosts"] == (
        ("gateway-0", "203.0.113.10"),
        ("gateway-1", "203.0.113.11"),
    )
    assert observed["enrollment_hosts"] == expected_enrollment
    assert observed["retained_hosts"] == {"gateway-0"}
    assert observed["default_known_hosts_bindings"] == {"gateway-0": binding}
    assert observed["host_identity_recovery"](frozenset({"gateway-0"})) is recovered
    assert observed["allow_default_known_hosts_import"] is True
    assert observed["persist_default_host_keys"] is True
    assert observed["require_management_key"] is False
    assert verified == [({"gateway-0": "203.0.113.10"}, policy, "operator")]


def test_ordinary_cloud_recovery_is_bound_to_the_discovered_compute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = VMManager(project_id="project-test", region="eu-west1")
    vm = SimpleNamespace(
        spec=SimpleNamespace(
            cloud_init_user_data=(
                "#cloud-config\n"
                "write_files:\n"
                "  - path: /etc/ssh/vpngw_host_key\n"
                "runcmd:\n"
                "  - HostKey /etc/ssh/vpngw_host_key\n"
            )
        )
    )
    checked: list[str] = []
    manager._ordinary_ssh_preflight_snapshot = {  # noqa: SLF001
        "gateway-0": (vm, "203.0.113.10")
    }
    manager._ordinary_ssh_binding_assertions = {  # noqa: SLF001
        "gateway-0": lambda: checked.append("gateway-0")
    }
    monkeypatch.setattr(
        "nebius_vpngw.deploy.vm_manager.recover_product_host_key",
        lambda instance, *, path: (
            b"private-host-key"
            if instance is vm and path == "/etc/ssh/vpngw_host_key"
            else pytest.fail("ordinary recovery used the wrong Compute or host-key path")
        ),
    )

    recovered = manager.recover_ordinary_ssh_host_keys(
        frozenset({"gateway-0"}),
        spec=SimpleNamespace(name="gateway", instance_count=1, vm_ha=None),
    )

    assert recovered["gateway-0"].private_key == b"private-host-key"
    assert (
        recovered["gateway-0"].assert_current
        is manager._ordinary_ssh_binding_assertions[  # noqa: SLF001
            "gateway-0"
        ]
    )
    assert checked == ["gateway-0"]


def test_vm_ha_migration_recovers_the_retained_ordinary_host_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = VMManager(project_id="project-test", region="eu-west1")
    retained = object()
    reread = object()
    signature = (
        "compute-0",
        "gateway-0",
        "project-test",
        "7",
        "203.0.113.10",
        "cloud-init-digest",
    )
    manager._vm_ha_ssh_preflight_snapshot = {  # noqa: SLF001
        "gateway-0": (retained, "203.0.113.10")
    }
    spec = SimpleNamespace(
        name="gateway",
        region="eu-west1",
        vm_ha=SimpleNamespace(
            members=(
                SimpleNamespace(instance_index=0, role=VMHARole.ACTIVE),
                SimpleNamespace(instance_index=1, role=VMHARole.PASSIVE),
            )
        ),
    )
    monkeypatch.setattr(manager, "_build_sdk_client", lambda _region: object())
    monkeypatch.setattr(
        manager,
        "_vm_ha_ssh_member_signature",
        lambda instance: (
            signature
            if instance in {retained, reread}
            else pytest.fail("unexpected Compute object")
        ),
    )
    monkeypatch.setattr(
        manager,
        "_get_vm_by_id_for_vm_ha_preflight",
        lambda _client, compute_id: (
            reread if compute_id == "compute-0" else pytest.fail("unexpected Compute identity")
        ),
    )
    monkeypatch.setattr(
        "nebius_vpngw.deploy.vm_manager.compute_provisioning_provenance",
        lambda _instance: pytest.fail(
            "ordinary-to-HA migration must not require pre-existing VM-HA provenance"
        ),
    )
    monkeypatch.setattr(
        "nebius_vpngw.deploy.vm_manager.recover_product_host_key",
        lambda instance, *, path: (
            b"private-host-key"
            if instance is retained and path == "/etc/ssh/vpngw_host_key"
            else pytest.fail("migration recovery used the wrong Compute or host-key path")
        ),
    )

    assertions = manager.vm_ha_ssh_trust_bindings(
        spec,
        retained_hosts={"gateway-0"},
        ordinary_migration_hosts={"gateway-0"},
    )
    assertions["gateway-0"]()
    recovered = manager.recover_vm_ha_ssh_host_keys(
        frozenset({"gateway-0"}),
        spec=spec,
        ordinary_migration_hosts={"gateway-0"},
    )

    assert recovered["gateway-0"].private_key == b"private-host-key"
    assert recovered["gateway-0"].assert_current is assertions["gateway-0"]


@pytest.mark.parametrize(
    "lifecycle_status",
    (
        VMHALifecycleStatus.PROVISIONING,
        VMHALifecycleStatus.ACTIVATING,
        VMHALifecycleStatus.ACTIVE,
    ),
)
def test_vm_ha_reproves_unmarked_retained_active_through_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    lifecycle_status: VMHALifecycleStatus,
) -> None:
    manager = VMManager(project_id="project-test", region="eu-west1")
    retained = object()
    reread = object()
    signature = (
        "compute-0",
        "gateway-0",
        "project-test",
        "7",
        "203.0.113.10",
        "cloud-init-digest",
    )
    manager._vm_ha_ssh_preflight_snapshot = {  # noqa: SLF001
        "gateway-0": (retained, "203.0.113.10")
    }
    spec = SimpleNamespace(
        name="gateway",
        region="eu-west1",
        vm_ha=SimpleNamespace(
            cluster_id="cluster-test",
            members=(SimpleNamespace(instance_index=0, node_id="node-a", role=VMHARole.ACTIVE),),
        ),
    )
    current_checks: list[str] = []
    lifecycle_state = SimpleNamespace(
        status=lifecycle_status,
        project_id="project-test",
        gateway_name="gateway",
        cluster_id="cluster-test",
        members=(
            SimpleNamespace(
                instance_name="gateway-0",
                compute_id="compute-0",
                public_ip="203.0.113.10",
                node_id="node-a",
                role="active",
            ),
        ),
    )
    lifecycle_snapshots = iter(
        (
            SimpleNamespace(
                state=lifecycle_state,
                assert_current=lambda: current_checks.append("stale"),
            ),
            SimpleNamespace(
                state=lifecycle_state,
                assert_current=lambda: current_checks.append("current"),
            ),
        )
    )
    monkeypatch.setattr(manager, "_build_sdk_client", lambda _region: object())
    monkeypatch.setattr(
        manager,
        "_vm_ha_ssh_member_signature",
        lambda instance: (
            signature
            if instance in {retained, reread}
            else pytest.fail("unexpected Compute object")
        ),
    )
    monkeypatch.setattr(
        manager,
        "_get_vm_by_id_for_vm_ha_preflight",
        lambda _client, compute_id: (
            reread if compute_id == "compute-0" else pytest.fail("unexpected Compute identity")
        ),
    )

    assertions = manager.vm_ha_ssh_trust_bindings(
        spec,
        retained_hosts={"gateway-0"},
        lifecycle_snapshot_loader=lambda: next(lifecycle_snapshots),
        ordinary_migration_hosts=set(),
    )
    assertions["gateway-0"]()

    assert current_checks == ["current"]


def test_invalid_enrollment_anchors_block_before_recreate_or_allocation() -> None:
    identity = SimpleNamespace(cloud_init_entries=lambda: "  - fixture\n")
    policy = SimpleNamespace(identity_for=lambda hostname: identity)
    manager = VMManager(project_id="project-test", region="eu-west1", ssh_policy=policy)
    spec = SimpleNamespace(
        name="gateway",
        instance_count=2,
        region="eu-west1",
        vm_ha=SimpleNamespace(
            cluster_id="cluster",
            members=(
                SimpleNamespace(node_id="node-a", instance_index=0, role=VMHARole.ACTIVE),
                SimpleNamespace(node_id="node-b", instance_index=1, role=VMHARole.PASSIVE),
            ),
        ),
        vm_spec={"ssh_public_key": None},
    )
    existing = SimpleNamespace()

    with (
        patch.object(manager, "_build_sdk_client", return_value=object()),
        patch.object(manager, "_resolve_client_apis", return_value=(None, None, None, None)),
        patch.object(
            manager,
            "_discover_vm_ha_members",
            return_value={"gateway-0": (existing, "203.0.113.10")},
        ),
        patch.object(manager, "verify_vm_ha_existing_identities"),
        patch.object(manager, "_build_cloud_init", return_value="#cloud-config\n"),
        patch.object(manager, "_delete_existing_instances_and_boot_disks") as delete,
        patch.object(manager, "_ensure_vm_ha_shared_allocation") as ensure_allocation,
        pytest.raises(RuntimeError, match="cloud-init must contain exactly one"),
    ):
        manager.ensure_group(spec, recreate=True)

    delete.assert_not_called()
    ensure_allocation.assert_not_called()


@pytest.mark.parametrize(
    ("initial", "current", "message"),
    [
        (
            {},
            {"gateway-0": (SimpleNamespace(id="compute-a"), "203.0.113.10")},
            "member set changed",
        ),
        (
            {"gateway-0": (SimpleNamespace(id="compute-a"), "203.0.113.10")},
            {},
            "member set changed",
        ),
        (
            {"gateway-0": (SimpleNamespace(id="compute-a"), "203.0.113.10")},
            {"gateway-0": (SimpleNamespace(id="compute-b"), "203.0.113.10")},
            "changed identity",
        ),
    ],
    ids=("appeared", "disappeared", "replaced"),
)
def test_vm_ha_member_snapshot_change_blocks_first_cloud_mutation(
    initial: dict[str, tuple[object, str]],
    current: dict[str, tuple[object, str]],
    message: str,
) -> None:
    manager = VMManager(
        project_id="project-test",
        region="eu-west1",
        ssh_policy=SimpleNamespace(),
    )
    spec = SimpleNamespace(
        name="gateway",
        instance_count=1,
        region="eu-west1",
        vm_ha=object(),
        vm_spec={},
    )

    with (
        patch.object(manager, "_build_sdk_client", return_value=object()),
        patch.object(manager, "_resolve_client_apis", return_value=(None, None, None, None)),
        patch.object(manager, "_discover_vm_ha_members", side_effect=(initial, current)),
        patch.object(manager, "verify_vm_ha_existing_identities"),
        patch.object(manager, "_prepare_gateway_ssh_enrollment_cloud_inits", return_value={}),
        patch.object(manager, "_delete_existing_instances_and_boot_disks") as delete,
        patch.object(manager, "_ensure_vm_ha_shared_allocation") as ensure_allocation,
        pytest.raises(RuntimeError, match=message),
    ):
        manager.ensure_group(spec)

    delete.assert_not_called()
    ensure_allocation.assert_not_called()


@pytest.mark.parametrize(
    ("operation", "args"),
    [
        ("get_ha_instance", ("instance-old",)),
        ("stop_ha_instance", ("instance-old",)),
        ("get_ha_allocation", ("private-1",)),
        ("set_ha_private_alias", ("instance-new", "eth0", "private-1", True)),
    ],
)
def test_vm_ha_cloud_operations_never_use_scaffold_mode(
    operation: str,
    args: tuple[str, ...],
) -> None:
    vm_mgr = VMManager(project_id="project-test", region="eu-west1")
    with (
        patch.object(vm_mgr, "_get_client", return_value=None),
        pytest.raises(RuntimeError, match="unavailable for VM-HA fencing"),
    ):
        getattr(vm_mgr, operation)(*args)
