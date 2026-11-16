"""Forced native package replacement under the full-repair guest owner.

Archives are resolved and staged before inhibition. Installed-file proof comes
from those archives, not a possibly damaged dpkg checksum database.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path

try:
    import _vpngw_repair_state as storage  # type: ignore[import-not-found]
    import _vpngw_runtime_migration as migration  # type: ignore[import-not-found]
except ModuleNotFoundError:
    from . import repair_state as storage
    from . import runtime_migration as migration

CONF_OPTIONS = ("--force-confask", "--force-confnew", "--force-confmiss")
DOCUMENTATION_ROOTS = ("/usr/share/doc/", "/usr/share/man/", "/usr/share/locale/")


def verify_archives(approved: dict, directory: Path) -> None:
    for filename, archive in approved["archives"].items():
        path = directory / filename
        if path.is_symlink() or path.stat().st_size != archive["size"]:
            raise RuntimeError("Repair native archive identity changed")
        checksum = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                checksum.update(block)
        if checksum.hexdigest() != archive["sha256"]:
            raise RuntimeError("Repair native archive identity changed")


def file_identity(path: Path) -> dict:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return {"kind": "symlink", "target": os.readlink(path)}
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError("Runtime archive contains an unsupported file type")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return {"kind": "file", "sha256": digest.hexdigest(), "mode": stat.S_IMODE(info.st_mode)}


def archive_inventory(approved: dict, directory: Path, command: Callable) -> dict:
    """Project only hash-verified archives, retaining native conffile identities."""
    migration.validate_plan(approved)
    verify_archives(approved, directory)
    files: dict[str, dict] = {}
    conffiles: set[str] = set()
    postinstall_modes: dict[str, list[int]] = {}
    for filename in sorted(approved["archives"]):
        path = directory / filename
        with tempfile.TemporaryDirectory(prefix=".repair-native-", dir=directory) as temporary:
            root = Path(temporary)
            data, control = root / "data", root / "control"
            command(["dpkg-deb", "--extract", str(path), str(data)], limit=120)
            command(["dpkg-deb", "--control", str(path), str(control)], limit=30)
            conf = control / "conffiles"
            if conf.is_file():
                for line in conf.read_text().splitlines():
                    name = line.split()[0] if line.split() else ""
                    if not name.startswith("/") or ".." in Path(name).parts:
                        raise RuntimeError("Runtime conffile identity is invalid")
                    conffiles.add(name)
            for source in data.rglob("*"):
                if source.is_dir() and not source.is_symlink():
                    continue
                name = "/" + source.relative_to(data).as_posix()
                # Ubuntu minimized images intentionally exclude documentation
                # and translations through dpkg policy. They are not runtime
                # payload; do not weaken executable/library/configuration proof.
                if name.startswith(DOCUMENTATION_ROOTS):
                    continue
                value = file_identity(source)
                # Ubuntu FRR's postinst removes other-user permissions from
                # root-owned /etc/frr files. Reinstallation can retain an
                # already FRR-owned file, leaving the archive mode unchanged.
                # Both are package-defined outcomes; content remains exact.
                if (
                    approved["archives"][filename]["package"].split(":")[0] == "frr"
                    and name.startswith("/etc/frr/")
                    and value["kind"] == "file"
                ):
                    postinstall_modes[name] = sorted({value["mode"], value["mode"] & ~0o007})
                if (
                    approved["archives"][filename]["package"].split(":")[0] == "cron"
                    and name == "/usr/bin/crontab"
                    and value["kind"] == "file"
                ):
                    # Ubuntu cron.postinst installs this exact statoverride.
                    postinstall_modes[name] = [0o2755]
                if name in files and files[name] != value:
                    raise RuntimeError("Runtime archives disagree on an installed path")
                files[name] = value
    if not files:
        raise RuntimeError("Repair native archive projection is empty")
    return {
        "archives": storage.digest(approved["archives"]),
        "files": files,
        "conffiles": sorted(conffiles),
        "postinstall_modes": postinstall_modes,
    }


def verify_inventory(
    inventory: dict, *, root: Path = Path("/"), rendered: set[str] | None = None
) -> None:
    """Rendered conffiles require separate exact generated-content verification."""
    excluded = rendered or set()
    if not excluded.issubset(set(inventory["conffiles"])):
        raise RuntimeError("Only declared rendered conffiles may have separate verification")
    for name, expected in inventory["files"].items():
        path = Path(name)
        if not path.is_absolute() or ".." in path.parts:
            raise RuntimeError("Repair native inventory path is invalid")
        if name in excluded:
            continue
        actual = root / path.relative_to("/")
        try:
            observed = file_identity(actual)
        except (OSError, RuntimeError) as error:
            raise RuntimeError("Repair native installed payload is incomplete") from error
        modes = inventory.get("postinstall_modes", {}).get(name)
        if modes is not None:
            allowed = (
                name.startswith("/etc/frr/") and set(modes) == {expected["mode"], expected["mode"] & ~0o007}
                or name == "/usr/bin/crontab" and modes == [0o2755]
            )
            if expected["kind"] != "file" or not allowed:
                raise RuntimeError("Repair native postinstall mode policy is invalid")
            if observed.get("mode") in modes:
                observed = {**observed, "mode": expected["mode"]}
        if observed != expected:
            fields = sorted(
                key
                for key in set(observed) | set(expected)
                if observed.get(key) != expected.get(key)
            )
            raise RuntimeError(
                "Repair native installed payload differs from its archive: "
                + name
                + " ("
                + ", ".join(fields)
                + ")"
            )


def install(
    approved: dict,
    directory: Path,
    inventory: dict,
    *,
    command: Callable,
    check: Callable,
) -> None:
    """The durable repair worker owns this entire call and every subprocess."""
    migration.validate_plan(approved)
    if not set(migration.REPAIR_REQUIRED).issubset(approved["install"]):
        raise RuntimeError("Repair native closure is incomplete")
    if storage.digest(approved["archives"]) != inventory["archives"]:
        raise RuntimeError("Repair native projection belongs to another archive closure")
    verify_archives(approved, directory)
    check()
    current = migration.packages()
    remove = [
        name
        for name in approved["remove"]
        if current.get(name, [None, None])[1] not in {None, "not-installed", "config-files"}
    ]
    if remove:
        command(["dpkg", "--remove", *remove], limit=120)
    check()
    # APT orders Pre-Depends configuration before dependent unpack. The worker
    # retains the frontend lock across this call; only its nested APT lock is
    # disabled. dpkg still owns its database lock. No resolver downloads/removals
    # or package effects outside the frozen closure are admitted.
    arguments = [
        "apt-get",
        "-o",
        "Debug::NoLocking=1",
        "-o",
        "Dir::Cache::archives=" + str(directory),
        "--no-download",
        "--no-remove",
        "--no-install-recommends",
        "--reinstall",
        *(option for value in CONF_OPTIONS for option in ("-o", "Dpkg::Options::=" + value)),
        "install",
        *(str(directory / name) for name in sorted(approved["archives"])),
    ]
    _code, selection = command([*arguments[:1], "--simulate", *arguments[1:]], limit=120)
    selected = {}
    for line in selection.splitlines():
        if line.startswith("Remv "):
            raise RuntimeError("Repair native installer proposed an unapproved removal")
        if line.startswith("Inst "):
            match = re.match(r"Inst ([A-Za-z0-9:+.-]+)(?: \[[^]]+\])? \(([^ ]+)", line)
            if match is None:
                raise RuntimeError("Repair native installation selection is unreadable")
            selected[match[1]] = match[2]
        if line.startswith("Conf ") and line.split()[1] not in approved["desired"]:
            raise RuntimeError("Repair native installer proposed unrelated configuration")
    if selected != approved["install"]:
        raise RuntimeError("Repair native installer changed the frozen package closure")
    verify_archives(approved, directory)
    command([*arguments[:1], "--assume-yes", *arguments[1:]], limit=300)
    check()
    migration.verify_packages(approved)
    verify_inventory(inventory)
