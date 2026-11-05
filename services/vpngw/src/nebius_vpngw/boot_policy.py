"""Persistent HA forwarding policy; executable before the package is installed.

This never changes the running kernel. The HA guard must fence an existing
member before installation; only the controller may later enable forwarding.
"""

from __future__ import annotations

import fnmatch
import os
import tempfile
from pathlib import Path

EXECUTABLE = Path("/usr/local/lib/nebius-vpngw/boot_policy.py")
OWNED = ("etc/sysctl.d/99-zzz-vpngw.conf", "etc/sysctl.d/99-vpn-gateway.conf")
KEY = "net.ipv4.ip_forward"
HA_MARKER = "# nebius-vpngw: HA boot forwarding is controller-owned"


class BootPolicyError(RuntimeError):
    """A bounded diagnostic without configuration contents or host paths."""


def _forward_setting(line: str) -> str | None:
    text = line.strip()
    if not text or text.startswith(("#", ";")) or "=" not in text:
        return None
    key, value = text.split("=", 1)
    key = key.strip().lstrip("-").replace("/", ".")
    if not (
        (key.startswith("net.ipv4.conf.") and key.endswith(".forwarding"))
        or any(fnmatch.fnmatchcase(target, key) for target in (KEY, "net.ipv4.conf.all.forwarding"))
    ):
        return None
    return value.split("#", 1)[0].split(";", 1)[0].strip()


def _inputs(root: Path) -> list[Path]:
    # systemd/procps select the highest-priority file for each basename. Inspect
    # every selected assignment, not just the final value: a preceding 1 is
    # already an unsafe transient during sysctl application.
    selected: dict[str, Path] = {}
    for directory in (
        "etc/sysctl.d",
        "run/sysctl.d",
        "usr/local/lib/sysctl.d",
        "usr/lib/sysctl.d",
        "lib/sysctl.d",
    ):
        for path in sorted((root / directory).glob("*.conf")):
            selected.setdefault(path.name, path)
    return [*selected.values(), root / "etc/sysctl.conf", root / "etc/ufw/sysctl.conf"]


def install_ha_policy(*, root: Path = Path("/")) -> None:
    """Reconcile only product files after fencing; reject unmanaged conflicts."""
    if (root / "proc/sys/net/ipv4/ip_forward").read_text().strip() != "0":
        raise BootPolicyError("ha-boot-policy-requires-forwarding-off")
    owned = {root / name for name in OWNED}
    for path in owned:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise BootPolicyError("ha-boot-policy-owned-file-invalid")
    for path in _inputs(root):
        if path in owned or not path.exists():
            continue
        if any(
            value not in (None, "0")
            for value in map(_forward_setting, path.read_text().splitlines())
        ):
            raise BootPolicyError("ha-boot-policy-unmanaged-forwarding-conflict")
    for name in OWNED:
        path = root / name
        # The second file is optional; do not manufacture an XFRM configuration.
        if name == OWNED[1] and not path.exists():
            continue
        lines = path.read_text().splitlines() if path.exists() else []
        content = "\n".join(
            line for line in lines if _forward_setting(line) is None and line != HA_MARKER
        )
        content = content.rstrip("\n") + "\n" + HA_MARKER + "\n" + KEY + " = 0\n"
        _publish(path, content)


def _publish(path: Path, content: str) -> None:
    if path.exists() and path.read_text() == content:
        metadata = path.stat()
        if (metadata.st_mode & 0o777, metadata.st_uid, metadata.st_gid) == (
            0o644,
            os.geteuid(),
            os.getegid(),
        ):
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".vpngw-boot-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fchown(stream.fileno(), os.geteuid(), os.getegid())
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def restore_ordinary_bootstrap_policy(*, root: Path = Path("/")) -> None:
    """Ordinary XFRM preparation removes only the product's previous HA policy.

    Called only on the existing persist=True path after HA deactivation. The
    ordinary agent still owns live routing; this does not change the kernel.
    """
    path = root / OWNED[0]
    if not path.exists():
        return
    lines = path.read_text().splitlines()
    if HA_MARKER not in lines:
        return
    if (root / "etc/nebius-vpngw/vm-ha-enabled").exists():
        raise BootPolicyError("ordinary-boot-policy-requires-ha-deactivation")
    if path.is_symlink():
        raise BootPolicyError("ha-boot-policy-owned-file-invalid")
    content = "\n".join(
        line for line in lines if _forward_setting(line) is None and line != HA_MARKER
    )
    _publish(path, content.rstrip("\n") + "\n" + KEY + " = 1\n")


if __name__ == "__main__":
    try:
        install_ha_policy()
    except (BootPolicyError, OSError, UnicodeError) as error:
        raise SystemExit(
            str(error) if isinstance(error, BootPolicyError) else "ha-boot-policy-io-failed"
        ) from None
