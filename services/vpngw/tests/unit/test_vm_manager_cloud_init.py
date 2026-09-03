from __future__ import annotations

import yaml

from nebius_vpngw.deploy.vm_manager import VMManager

KEY_A = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA alice"
KEY_B = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB bob"


def _render(ssh_key: str) -> str:
    return VMManager(project_id="project-test", zone="eu-north1-a")._build_cloud_init(
        ssh_key=ssh_key
    )


def _parsed(ssh_key: str) -> dict:
    return yaml.safe_load(_render(ssh_key))


def test_frr_daemons_is_not_chowned_before_frr_is_installed() -> None:
    parsed = _parsed(KEY_A)

    daemons = [f for f in parsed["write_files"] if f["path"] == "/etc/frr/daemons"]
    assert len(daemons) == 1
    assert "owner" not in daemons[0]

    runcmd = "\n".join(str(c) for c in parsed["runcmd"])
    assert runcmd.index("apt-get install -y frr") < runcmd.index("chown frr:frr /etc/frr/daemons")


def test_multiple_operator_keys_each_become_their_own_authorized_key() -> None:
    parsed = _parsed(f"{KEY_A}\n{KEY_B}\n")

    assert parsed["users"][0]["ssh_authorized_keys"] == [KEY_A, KEY_B]


def test_comments_and_blank_lines_in_the_key_file_are_ignored() -> None:
    parsed = _parsed(f"# operators\n\n{KEY_A}\n\n")

    assert parsed["users"][0]["ssh_authorized_keys"] == [KEY_A]


def test_single_key_is_unchanged() -> None:
    parsed = _parsed(KEY_A)

    assert parsed["users"][0]["ssh_authorized_keys"] == [KEY_A]
