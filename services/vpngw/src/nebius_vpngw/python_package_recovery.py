"""Prospective, per-wheel repair checkpoints owned by the package transaction.

This is not a general repair tool. Missing or ambiguous predecessor ownership
cannot be reconstructed from a newer wheel. Only recorded predecessor/target
bytes may be restored while the canonical owner holds package exclusion.
"""

from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import importlib.util
import marshal
import os
import stat
import struct
import sys
import sysconfig
import types
import zipfile
from collections.abc import Callable
from email.parser import BytesParser
from pathlib import Path

try:
    import _vpngw_package_worker as storage  # type: ignore[import-not-found]
except ModuleNotFoundError:
    from nebius_vpngw import package_worker as storage

SCHEMA = "nebius-vpngw.python-package-checkpoint.v1"


def schemes() -> tuple[Path, ...]:
    return tuple(
        set(
            Path(sysconfig.get_path(key)).resolve()
            for key in ("purelib", "platlib", "scripts", "data")
        )
    )


def safe(path: Path, roots: tuple[Path, ...]) -> Path:
    absolute = Path(os.path.abspath(path))
    if not any(absolute.is_relative_to(root) and absolute != root for root in roots):
        raise RuntimeError("Python package path escapes the installation scheme")
    for component in (absolute, *absolute.parents):
        if component.is_symlink():
            raise RuntimeError("Python package path contains a symlink")
    return absolute


def content(path: Path) -> dict | None:
    if not path.exists():
        if path.is_symlink():
            raise RuntimeError("Python package file is a symlink")
        return None
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o022
        ):
            raise RuntimeError("Python package file ownership is unsafe")
        raw = stream.read()
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "mode": stat.S_IMODE(info.st_mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
    }


def wheel_identity(wheel: Path) -> tuple[str, str]:
    with zipfile.ZipFile(wheel) as archive:
        names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(names) != 1:
            raise RuntimeError("Python wheel metadata is ambiguous")
        value = BytesParser().parsebytes(archive.read(names[0]))
        return str(value["Name"]), str(value["Version"])


def atomic(path: Path, raw: bytes, mode: int) -> None:
    created = []
    parent = path.parent
    while not parent.exists():
        created.append(parent)
        parent = parent.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + os.urandom(12).hex())
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
        for parent in created:
            sync_directory(parent.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def metadata_roots(inventory: dict) -> set[Path]:
    roots = schemes()
    return {
        parent
        for name in inventory
        for parent in safe(Path(name), roots).parents
        if parent.name.endswith(".dist-info") and parent.parent in roots
    }


def obsolete_metadata(before: dict, target: dict) -> set[Path]:
    return metadata_roots(before) - metadata_roots(target)


def metadata_cleanup(before: dict, target: dict) -> set[Path]:
    """Admit only obsolete metadata directories derived from recorded files."""
    directories: set[Path] = set()
    for root in obsolete_metadata(before, target):
        files = {Path(name) for name in before if Path(name).is_relative_to(root)}
        owned_directories = {
            parent for path in files for parent in path.parents if parent.is_relative_to(root)
        }
        for path in (root, *root.rglob("*")):
            safe(path, schemes())
            if not path.exists():
                continue
            info = path.stat()
            if path.is_dir():
                if (
                    path not in owned_directories
                    or info.st_uid != os.geteuid()
                    or info.st_mode & 0o022
                ):
                    raise RuntimeError("Python metadata directory ownership is unverified")
            elif path not in files:
                raise RuntimeError("Python metadata contains an unowned entry")
        directories.update(owned_directories)
    return directories


def prune_metadata(directories: set[Path]) -> None:
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        safe(path, schemes())
        if path.exists():
            info = path.stat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o022
            ):
                raise RuntimeError("Python metadata directory ownership changed")
            path.rmdir()  # Never remove unknown contents or shared import directories.
            sync_directory(path.parent)


def sync_metadata(before: dict, target: dict) -> None:
    roots = metadata_roots(before) | metadata_roots(target)
    directories = {root.parent for root in roots}
    directories.update(
        parent
        for name in target
        for parent in Path(name).parents
        if any(parent.is_relative_to(root) for root in roots)
    )
    # A retry can find entries created/removed before the prior writer fsynced
    # their parents. Persist those entries even when this invocation made none.
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        sync_directory(path)


def load(directory: Path, wheel: Path) -> dict:
    value = storage.read(directory / "checkpoint.json")
    if (
        set(value)
        != {"schema", "wheel", "interpreter", "name", "version", "before", "target", "state"}
        or value["schema"] != SCHEMA
        or value["wheel"] != hashlib.sha256(wheel.read_bytes()).hexdigest()
        or value["interpreter"] != sys.executable
        or value["state"] not in {"prepared", "installing", "restoring", "restored", "complete"}
        or (value["name"], value["version"]) != wheel_identity(wheel)
    ):
        raise RuntimeError("Python repair checkpoint changed")
    for inventory in (value["before"], value["target"]):
        if not isinstance(inventory, dict):
            raise RuntimeError("Python repair inventory is invalid")
        for name, item in inventory.items():
            safe(Path(name), schemes())
            if (
                not isinstance(item, dict)
                or set(item) != {"sha256", "mode", "uid", "gid"}
                or not isinstance(item["sha256"], str)
                or len(item["sha256"]) != 64
                or item["uid"] != os.geteuid()
                or item["mode"] & 0o022
            ):
                raise RuntimeError("Python repair file identity is invalid")
    return value


def prepare(directory: Path, wheel: Path, command: Callable) -> None:
    storage.directory(directory)
    if (directory / "checkpoint.json").exists():
        load(directory, wheel)
        return
    roots = schemes()
    name, version = wheel_identity(wheel)
    try:
        installed = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        installed = None
    owned: set[Path] = set()
    if installed is not None:
        base = Path(str(installed.locate_file(""))).resolve()
        destinations = {Path(sysconfig.get_path(key)).resolve() for key in ("purelib", "platlib")}
        if base in destinations:
            if installed.read_text("RECORD") is None or installed.files is None:
                raise RuntimeError("Python predecessor uninstall inventory is missing")
            owned = {
                safe(Path(str(installed.locate_file(item))), roots) for item in installed.files
            }
            # Runtime imports can create bytecode outside RECORD even when pip
            # used --no-compile. Admit only canonical caches whose code is exactly
            # derived from a RECORD-owned source using this interpreter.
            derived: set[Path] = set()
            for path in tuple(owned):
                if path.suffix != ".py":
                    continue
                for optimization in ("", "1", "2"):
                    cache = safe(
                        Path(
                            importlib.util.cache_from_source(str(path), optimization=optimization)
                        ),
                        roots,
                    )
                    if not cache.exists() or cache in owned:
                        continue
                    content(cache)
                    raw = cache.read_bytes()
                    try:
                        code = marshal.loads(raw[16:])
                        exact = compile(
                            path.read_bytes(),
                            str(path),
                            "exec",
                            dont_inherit=True,
                            optimize=int(optimization or "0"),
                        )
                        valid = (
                            raw[:4] == importlib.util.MAGIC_NUMBER
                            and isinstance(code, types.CodeType)
                            and code.co_filename == str(path)
                            and code == exact
                        )
                    except (EOFError, ValueError, TypeError, SyntaxError):
                        valid = False
                    if not valid:
                        raise RuntimeError("Python generated bytecode ownership is unverified")
                    owned.add(cache)
                derived.update((path.with_suffix(".pyc"), path.with_suffix(".pyo")))
            for entry in installed.entry_points:
                if entry.group in {"console_scripts", "gui_scripts"}:
                    derived.add(Path(sysconfig.get_path("scripts")) / entry.name)
            if any(safe(path, roots).exists() and path not in owned for path in derived):
                raise RuntimeError("Python predecessor has uninstall effects outside its inventory")
    # Namespaces may share directories, but no file may have two owners.
    claimed: set[Path] = set()
    for other in metadata.distributions():
        if (other.metadata["Name"] or "").lower().replace("_", "-") == name.lower().replace(
            "_", "-"
        ):
            continue
        for entry_path in other.files or ():
            path = Path(os.path.abspath(str(other.locate_file(entry_path))))
            claimed.add(path)
            if path in owned:
                raise RuntimeError("Python predecessor file has shared ownership")
    projection = directory / "projection"
    storage.directory(projection)
    command(
        [
            sys.executable,
            "-B",
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--no-compile",
            "--ignore-installed",
            "--break-system-packages",
            "--root",
            str(projection),
            str(wheel),
        ],
        limit=120,
    )
    target = {}
    for projected in projection.rglob("*"):
        if projected.is_symlink():
            raise RuntimeError("Python target contains a symlink")
        if projected.is_file():
            path = safe(Path("/") / projected.relative_to(projection), roots)
            if path in claimed:
                raise RuntimeError("Python target file has shared ownership")
            if path.exists() and path not in owned:
                raise RuntimeError("Python target would overwrite an unowned file")
            target[str(path)] = content(projected)
    if not target:
        raise RuntimeError("Python target projection is empty")
    paths = sorted(owned | {Path(path) for path in target})
    # Independently reject OS-owned files, including files under /usr/local.
    for offset in range(0, len(paths), 128):
        code, output = command(
            ["dpkg-query", "--search", *(str(p) for p in paths[offset : offset + 128])],
            limit=30,
            required=False,
            capture_stderr=True,
        )
        if code not in {0, 1} or not output.strip():
            raise RuntimeError("Python OS package ownership is unverified")
        if code == 0 or any(
            not line.startswith("dpkg-query: no path found matching pattern ")
            for line in output.splitlines()
        ):
            raise RuntimeError("Python package path is owned by the OS package manager")
    before = {}
    storage.directory(directory / "blobs")
    for path in owned:
        item = content(path)
        if item is None:
            raise RuntimeError("Python predecessor inventory is incomplete")
        before[str(path)] = item
        blob = directory / "blobs" / item["sha256"]
        if not blob.exists():
            atomic(blob, path.read_bytes(), 0o600)
    storage.publish(
        directory / "checkpoint.json",
        dict(
            schema=SCHEMA,
            wheel=hashlib.sha256(wheel.read_bytes()).hexdigest(),
            interpreter=sys.executable,
            name=name,
            version=version,
            before=before,
            target=target,
            state="prepared",
        ),
    )


def matches(inventory: dict) -> bool:
    return all(content(safe(Path(path), schemes())) == item for path, item in inventory.items())


def exact_bytes(path: Path, expected: dict) -> tuple[bytes, os.stat_result]:
    """Read the same owned file whose identity and metadata were admitted."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or before.st_mode & 0o022
        ):
            raise RuntimeError("Python package file ownership is unsafe")
        raw = stream.read()
        after = os.fstat(stream.fileno())

    def signature(info):
        return (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_uid,
            info.st_gid,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    if signature(before) != signature(after) or signature(after) != signature(path.lstat()):
        raise RuntimeError("Python package file changed during observation")
    if expected != {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "mode": stat.S_IMODE(after.st_mode),
        "uid": after.st_uid,
        "gid": after.st_gid,
    }:
        raise RuntimeError("Python package file identity changed")
    return raw, after


def matches_before(directory: Path, value: dict, name: str, current: dict | None) -> bool:
    """Allow only regenerated timestamps in an already owned canonical cache.

    Python may refresh a cache between non-disruptive staging and writer fencing.
    Its immutable source, compiled code and permissions must still be exact;
    neither the checkpoint nor any arbitrary metadata is refreshed here.
    """
    expected = value["before"].get(name)
    if expected is None:
        return False
    if current == expected:
        return True
    if current is None or not name.endswith(".pyc"):
        return False
    try:
        path = safe(Path(name), schemes())
        source = safe(Path(importlib.util.source_from_cache(name)), schemes())
        optimization = path.stem.rpartition(".opt-")[2] if ".opt-" in path.stem else ""
        if (
            optimization not in {"", "1", "2"}
            or Path(importlib.util.cache_from_source(str(source), optimization=optimization))
            != path
        ):
            return False
        if any(current[key] != expected[key] for key in ("mode", "uid", "gid")):
            return False
        source_raw, source_info = exact_bytes(source, value["before"][str(source)])
        raw, _info = exact_bytes(path, current)
        blob = safe(directory / "blobs" / expected["sha256"], (directory,))
        blob_identity = content(blob)
        if blob_identity is None or blob_identity["sha256"] != expected["sha256"]:
            return False
        original, _ = exact_bytes(blob, blob_identity)
        if (
            len(raw) < 16
            or raw[:8] != importlib.util.MAGIC_NUMBER + b"\0" * 4
            or raw[:8] != original[:8]
            or raw[12:] != original[12:]
            or struct.unpack("<II", raw[8:16])
            != (int(source_info.st_mtime) & 0xFFFFFFFF, len(source_raw) & 0xFFFFFFFF)
        ):
            return False
        code = marshal.loads(raw[16:])
        return (
            isinstance(code, types.CodeType)
            and code.co_filename == str(source)
            and code
            == compile(
                source_raw,
                str(source),
                "exec",
                dont_inherit=True,
                optimize=int(optimization or "0"),
            )
        )
    except (OSError, ValueError, KeyError, TypeError, EOFError, RuntimeError, SyntaxError):
        return False


def completed_cache(directory: Path, value: dict, name: str, current: dict | None) -> bool:
    """Retain an already owned cache only when its installed source is unchanged."""
    if current is None or not name.endswith(".pyc"):
        return False
    try:
        source = safe(Path(importlib.util.source_from_cache(name)), schemes())
        path = safe(Path(name), schemes())
        optimization = path.stem.rpartition(".opt-")[2] if ".opt-" in path.stem else ""
        if (
            str(source) not in value["before"]
            or value["before"][str(source)] != value["target"].get(str(source))
            or optimization not in {"", "1", "2"}
            or Path(importlib.util.cache_from_source(str(source), optimization=optimization))
            != path
        ):
            return False
        source_raw, info = exact_bytes(source, value["target"][str(source)])
        raw, _ = exact_bytes(path, current)
        if (
            len(raw) < 16
            or raw[:8] != importlib.util.MAGIC_NUMBER + b"\0" * 4
            or struct.unpack("<II", raw[8:16])
            != (int(info.st_mtime) & 0xFFFFFFFF, len(source_raw) & 0xFFFFFFFF)
        ):
            return False
        code = marshal.loads(raw[16:])
        # RECORD ownership proves bytes, not that Python will execute the
        # approved source. Validate even exact-before caches on completion.
        return (
            isinstance(code, types.CodeType)
            and code.co_filename == str(source)
            and code
            == compile(
                source_raw,
                str(source),
                "exec",
                dont_inherit=True,
                optimize=int(optimization or "0"),
            )
            and matches_before(directory, value, name, current)
        )
    except (OSError, ValueError, KeyError, TypeError, EOFError, RuntimeError, SyntaxError):
        return False


def complete(directory: Path, value: dict) -> bool:
    return (
        matches(value["target"])
        and all(
            (current := content(safe(Path(path), schemes()))) is None
            or completed_cache(directory, value, path, current)
            for path in value["before"].keys() - value["target"].keys()
        )
        and not any(path.exists() for path in obsolete_metadata(value["before"], value["target"]))
    )


def restore(directory: Path, value: dict) -> None:
    # Preflight the entire write set before restoring even one file. Interrupted
    # restoration accepts either the original before-image or the exact target.
    for name in set(value["before"]) | set(value["target"]):
        current = content(safe(Path(name), schemes()))
        if current not in (None, value["before"].get(name), value["target"].get(name)) and not (
            matches_before(directory, value, name, current)
        ):
            raise RuntimeError("Python repair encountered foreign or unproven file contents")
    for item in value["before"].values():
        blob = directory / "blobs" / item["sha256"]
        if blob.is_symlink() or hashlib.sha256(blob.read_bytes()).hexdigest() != item["sha256"]:
            raise RuntimeError("Python repair before-image changed")
    directories = metadata_cleanup(value["target"], value["before"])
    value["state"] = "restoring"
    storage.publish(directory / "checkpoint.json", value)

    for name in sorted(set(value["before"]) | set(value["target"])):
        path = safe(Path(name), schemes())
        item = value["before"].get(name)
        if item is None:
            if path.exists():
                path.unlink()  # Only an exact transaction-created file, never a directory.
        else:
            if content(path) == item:
                continue
            blob = directory / "blobs" / item["sha256"]
            raw = blob.read_bytes()
            if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise RuntimeError("Python repair before-image changed")
            atomic(path, raw, item["mode"])
            os.chown(path, item["uid"], item["gid"])
    prune_metadata(directories)
    if not matches(value["before"]) or any(
        path.exists() for path in obsolete_metadata(value["target"], value["before"])
    ):
        raise RuntimeError("Python predecessor restoration is unverified")
    sync_metadata(value["target"], value["before"])
    value["state"] = "restored"
    storage.publish(directory / "checkpoint.json", value)


def checkpoint_root(manifest: dict, payload: Path) -> Path:
    # Before-images belong to one admitted operation, not just its artifact.
    # Another operation may legitimately install the same wheel from a different
    # path, changing pip's direct_url.json and RECORD. Its successor needs a new
    # before-image; the original admission must retain its immutable checkpoint.
    return payload / "python-checkpoints" / storage.digest(manifest)


def prepare_payload(manifest: dict, payload: Path, command: Callable) -> None:
    if "ordinary_assets" not in manifest:
        return
    root = checkpoint_root(manifest, payload)
    claimed: set[str] = set()
    for name, digest in {
        **manifest["dependency_wheels"],
        manifest["wheel_name"]: manifest["wheel_sha256"],
    }.items():
        prepare(root / digest, payload / name, command)
        checkpoint = load(root / digest, payload / name)
        paths = set(checkpoint["before"]) | set(checkpoint["target"])
        if claimed & paths:
            raise RuntimeError("Selected Python wheels share file ownership")
        claimed.update(paths)


def install_payload(manifest: dict, payload: Path, command: Callable, check: Callable) -> None:
    root = checkpoint_root(manifest, payload)
    for name, digest in {
        **manifest["dependency_wheels"],
        manifest["wheel_name"]: manifest["wheel_sha256"],
    }.items():
        install(root / digest, payload / name, command, check)


def install(directory: Path, wheel: Path, command: Callable, check: Callable) -> None:
    check()
    value = load(directory, wheel)
    if value["state"] == "complete":
        if not complete(directory, value):
            raise RuntimeError("Completed Python package changed")
        return
    if value["state"] in {"installing", "restoring"}:
        if complete(directory, value):
            sync_metadata(value["before"], value["target"])
            value["state"] = "complete"
            storage.publish(directory / "checkpoint.json", value)
            return
        restore(directory, value)
    if not all(
        matches_before(directory, value, name, content(safe(Path(name), schemes())))
        for name in value["before"]
    ) or any(
        content(safe(Path(path), schemes())) is not None
        for path in value["target"].keys() - value["before"].keys()
    ):
        raise RuntimeError("Python predecessor changed before installation")
    directories = metadata_cleanup(value["before"], value["target"])
    value["state"] = "installing"
    storage.publish(directory / "checkpoint.json", value)
    # Delete only checkpoint-owned obsolete files. Letting pip uninstall the
    # predecessor would expand effects beyond RECORD (scripts/bytecode). Exact
    # target paths were independently admitted; pip must not resolve or uninstall.
    for name in value["before"].keys() - value["target"].keys():
        path = safe(Path(name), schemes())
        path.unlink()
        sync_directory(path.parent)
    prune_metadata(directories)
    args = [
        sys.executable,
        "-B",
        "-m",
        "pip",
        "install",
        "--no-index",
        "--no-deps",
        "--no-compile",
        "--break-system-packages",
    ]
    args.append("--ignore-installed")
    command([*args, str(wheel)], limit=120)
    check()
    if not complete(directory, value):
        raise RuntimeError("Python installed inventory differs from staged projection")
    sync_metadata(value["before"], value["target"])
    value["state"] = "complete"
    storage.publish(directory / "checkpoint.json", value)
