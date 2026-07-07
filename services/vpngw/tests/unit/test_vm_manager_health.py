from __future__ import annotations

import subprocess
from collections.abc import Sequence

import pytest

from nebius_vpngw.deploy.vm_manager import VMManager


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["ssh"], returncode=returncode, stdout=stdout, stderr="")


def _fake_ssh_run_for_esp4(
    esp4_result: subprocess.CompletedProcess[str],
) -> object:
    def fake_run(cmd: Sequence[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
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
        (_completed(stdout="reboot-pending\n", returncode=75), False, True, "waiting for gateway reboot"),
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
