#!/usr/bin/env python3
"""Create missing Claude settings atomically without clobbering existing state."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import stat

from claude_config import SKILL, json_object, native_home, safe_path, safe_read


def render(profile: str) -> bytes:
    name = "settings.trusted-local.json.template" if profile == "trusted-local" else "settings.json.template"
    data = safe_read(SKILL / "assets" / name)
    value = json_object(data)
    expected = ({"permissions": {"defaultMode": "bypassPermissions"}, "sandbox": {"enabled": False}}
                if profile == "trusted-local" else {})
    if value != expected:
        raise ValueError("recovery baseline contains unreviewed settings")
    return data


def create_private_file(home: Path, content: bytes) -> bool:
    """Return a durability warning after publication; never retry that result."""
    safe_path(home)
    before = home.lstat()
    descriptor = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = f".settings.recovery-{secrets.token_hex(12)}"
    opened: int | None = None
    published = False
    warning = False
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o022
                or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)):
            raise ValueError("unsafe Claude home")
        opened = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=descriptor)
        os.fchmod(opened, 0o600)
        with os.fdopen(opened, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        current = home.lstat()
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("Claude home changed before publication")
        os.link(temporary, "settings.json", src_dir_fd=descriptor,
                dst_dir_fd=descriptor, follow_symlinks=False)
        published = True
        try:
            os.fsync(descriptor)
            current = home.lstat()
            warning = (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino)
        except OSError:
            warning = True
    finally:
        if opened is not None:
            try:
                os.close(opened)
            except OSError:
                if not published:
                    raise
                warning = True
        try:
            os.unlink(temporary, dir_fd=descriptor)
        except FileNotFoundError:
            pass
        except OSError:
            if not published:
                raise
            warning = True
        finally:
            os.close(descriptor)
    return warning


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--claude-home", type=Path, help="Existing native home; defaults to CLAUDE_CONFIG_DIR or ~/.claude.")
    parser.add_argument("--profile", choices=("minimal", "trusted-local"), default="minimal",
                        help="Use trusted-local only after explicit profile selection.")
    args = parser.parse_args(argv)
    try:
        warning = create_private_file(native_home(args.claude_home), render(args.profile))
    except FileExistsError:
        print(json.dumps({"status": "EXISTS", "action": "inspect and use patch-only reconciliation"}))
        return 1
    except (OSError, ValueError):
        print(json.dumps({"status": "FAILED", "reason": "unsafe target or recovery publication failed"}))
        return 1
    print(json.dumps({"status": "CREATED_WITH_WARNING" if warning else "CREATED",
                      "action": "inspect completed settings; do not retry creation"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
