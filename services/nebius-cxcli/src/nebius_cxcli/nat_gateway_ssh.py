"""Select one public-key identity before attempting NAT guest authentication."""

from __future__ import annotations

import base64
import os
import stat
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def public_key_identity(value: str) -> tuple[str, str]:
    parts = value.strip().split()
    if len(parts) < 2 or "\n" in value.strip():
        raise ValueError("NAT requires one OpenSSH public key")
    kind, encoded = parts[:2]
    try:
        blob = base64.b64decode(encoded, validate=True)
        size = int.from_bytes(blob[:4], "big")
        if not size or blob[4 : 4 + size].decode("ascii") != kind:
            raise ValueError
    except (ValueError, UnicodeError) as error:
        raise ValueError("NAT requires a valid OpenSSH public key") from error
    if kind not in {
        "ssh-ed25519",
        "ssh-rsa",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "sk-ssh-ed25519@openssh.com",
        "sk-ecdsa-sha2-nistp256@openssh.com",
    }:
        raise ValueError("Unsupported NAT SSH public key type")
    return kind, encoded


def _output(args: list[str]) -> str | None:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 and len(result.stdout) <= 65536 else None


def _private_identity(path: Path) -> tuple[str, str] | None:
    try:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
        ):
            return None
    except OSError:
        return None
    output = _output(["ssh-keygen", "-y", "-P", "", "-f", str(path)])
    try:
        return public_key_identity(output) if output else None
    except ValueError:
        return None


@contextmanager
def ssh_identity_options(public_key: str, private_key: Path | None = None) -> Iterator[list[str]]:
    """Never offer unrelated agent or default keys to a three-attempt SSH server."""
    expected = public_key_identity(public_key)
    selected: Path | None = None
    use_agent = False
    if private_key is not None:
        selected = private_key.expanduser().absolute()
        if _private_identity(selected) != expected:
            raise RuntimeError(
                "NAT --ssh-private-key must be an owner-only regular private key matching "
                "ssh_public_key. For an encrypted key, load it into ssh-agent and omit this option."
            )
    else:
        agent_keys = []
        for line in (_output(["ssh-add", "-L"]) or "").splitlines():
            try:
                if public_key_identity(line) == expected:
                    agent_keys.append(line)
            except ValueError:
                continue
        if len(agent_keys) > 1:
            raise RuntimeError("NAT SSH identity is ambiguous in ssh-agent")
        use_agent = len(agent_keys) == 1
        if not use_agent:
            matches = [
                path
                for name in ("id_ed25519", "id_ecdsa", "id_rsa")
                if _private_identity(path := Path.home() / ".ssh" / name) == expected
            ]
            if len(matches) != 1:
                raise RuntimeError(
                    "NAT needs one usable private key matching ssh_public_key. "
                    "Load it into ssh-agent or pass --ssh-private-key; no SSH connection was attempted."
                )
            selected = matches[0]
    with tempfile.TemporaryDirectory(prefix="cxcli-nat-ssh-") as temporary:
        if use_agent:
            selected = Path(temporary) / "identity.pub"
            selected.write_text(" ".join(expected) + "\n", encoding="ascii")
            selected.chmod(0o600)
        assert selected is not None
        yield [
            "-F",
            "/dev/null",
            "-i",
            str(selected),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "PreferredAuthentications=publickey",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-o",
            "NumberOfPasswordPrompts=0",
            "-o",
            "BatchMode=yes",
            *([] if use_agent else ["-o", "IdentityAgent=none"]),
        ]
