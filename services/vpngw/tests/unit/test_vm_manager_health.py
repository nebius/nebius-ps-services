from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nebius_vpngw.deploy.vm_manager import VMManager


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

    health = VMManager(project_id="project-test", zone="eu-west1").check_vm_health(
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
        VMManager(project_id="project-test", zone="eu-west1").check_vm_health(
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
        project_id="project-test", zone="eu-west1", ssh_policy=policy
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
        zone="eu-west1",
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
        zone="eu-west1",
        ssh_policy=policy,
        management_key_path=key_path,
    ).check_vm_health("gateway-0", "203.0.113.10")

    assert observed[0][observed[0].index("-i") + 1] == str(key_path)
    assert "StrictHostKeyChecking=yes" in observed[0]
    assert f"UserKnownHostsFile={policy.known_hosts_file}" in observed[0]
    assert "HostKeyAlias=gateway-0" in observed[0]


def test_invalid_enrollment_anchors_block_before_recreate_or_allocation() -> None:
    identity = SimpleNamespace(cloud_init_entries=lambda: "  - fixture\n")
    policy = SimpleNamespace(identity_for=lambda hostname: identity)
    manager = VMManager(project_id="project-test", zone="eu-west1", ssh_policy=policy)
    spec = SimpleNamespace(
        name="gateway",
        instance_count=1,
        region="eu-west1",
        vm_ha=object(),
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
        zone="eu-west1",
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
        patch.object(manager, "_prepare_vm_ha_enrollment_cloud_inits", return_value={}),
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
        ("set_ha_private_allocation", ("instance-new", "eth0", "private-1")),
    ],
)
def test_vm_ha_cloud_operations_never_use_scaffold_mode(
    operation: str,
    args: tuple[str, ...],
) -> None:
    vm_mgr = VMManager(project_id="project-test", zone="eu-west1")
    with (
        patch.object(vm_mgr, "_get_client", return_value=None),
        pytest.raises(RuntimeError, match="unavailable for VM-HA fencing"),
    ):
        getattr(vm_mgr, operation)(*args)
