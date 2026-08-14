from __future__ import annotations

from pathlib import Path

import paramiko
import pytest

from nebius_vpngw.deploy.ssh_policy import (
    KNOWN_HOSTS_ENV,
    require_explicit_known_hosts_file,
)


@pytest.mark.parametrize("content", [b"", b"not a known-hosts record\n"])
def test_required_vm_ha_trust_rejects_empty_or_malformed_file(
    tmp_path: Path, content: bytes
) -> None:
    path = tmp_path / "known_hosts"
    path.write_bytes(content)

    with pytest.raises(ValueError, match="non-empty readable|usable SSH host keys"):
        require_explicit_known_hosts_file({KNOWN_HOSTS_ENV: str(path)})


def test_required_vm_ha_trust_accepts_pinned_host_identity(tmp_path: Path) -> None:
    path = tmp_path / "known_hosts"
    keys = paramiko.HostKeys()
    keys.add("gateway-a", "ssh-rsa", paramiko.RSAKey.generate(1024))
    keys.save(str(path))

    assert require_explicit_known_hosts_file({KNOWN_HOSTS_ENV: str(path)}) == path


def test_required_vm_ha_trust_rejects_missing_enrollment() -> None:
    with pytest.raises(ValueError, match=f"{KNOWN_HOSTS_ENV} is required"):
        require_explicit_known_hosts_file({})
