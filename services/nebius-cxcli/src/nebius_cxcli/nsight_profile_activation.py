"""Self-contained owner of late shell activation, separate from package payload."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

HOOK = Path("/etc/profile.d/zz-nebius-nsight.sh")
CONTENT = (
    "# Managed by nebius-cxcli: activate pinned profilers after CUDA PATH setup.\n"
    "if [ -r /etc/profile.d/99-nsight.sh ]; then\n"
    "    . /etc/profile.d/99-nsight.sh\n"
    "fi\n"
)


def validate_parent():
    for path in HOOK.parents:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError("Nsight activation requires safe root-owned parent directories")


def validate_hook():
    info = HOOK.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o644
        or info.st_nlink != 1
        or HOOK.read_text() != CONTENT
    ):
        raise RuntimeError("Nsight activation hook has conflicting ownership or content")


def recover_publication():
    """Finish only our fully written hook's interrupted temporary hardlink cleanup."""
    info = HOOK.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o644
        or info.st_nlink != 2
        or HOOK.read_text() != CONTENT
    ):
        return
    matches = []
    for candidate in HOOK.parent.glob(".nsight-activation-*.tmp"):
        other = candidate.lstat()
        if stat.S_ISREG(other.st_mode) and (other.st_dev, other.st_ino) == (
            info.st_dev,
            info.st_ino,
        ):
            matches.append(candidate)
    if len(matches) != 1:
        raise RuntimeError("Nsight activation has an unowned extra hardlink")
    matches[0].unlink()
    sync_parent()


def sync_parent():
    descriptor = os.open(HOOK.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def activate(action):
    if action not in {"install", "verify"}:
        raise ValueError("Nsight activation requires install or verify")
    validate_parent()
    try:
        if action == "install":
            recover_publication()
        validate_hook()
        return
    except FileNotFoundError:
        if action == "verify":
            raise RuntimeError("Nsight activation hook is missing") from None
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=HOOK.parent, prefix=".nsight-activation-", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(CONTENT)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        with suppress(FileExistsError):
            os.link(temporary, HOOK)  # Atomic create, never replace an existing hook.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    validate_hook()
    sync_parent()


if __name__ == "__main__":
    activate(sys.argv[1])
