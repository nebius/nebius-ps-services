"""Self-contained, omission-only repair program executed inside the shared jail.

The immutable package program and activation owner are injected as namespaces.
Existing files are compared, never overwritten. Only exact cached archives are
read; no package manager transaction or network acquisition is performed here.
"""

import ctypes
import errno
import hashlib
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def safe_parent(path):
    for parent in path.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError("Nsight repair requires existing safe root-owned parent directories")


def regular_state(path, checksum, mode):
    safe_parent(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"path": str(path), "sha256": checksum, "mode": mode}
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != mode
        or hashlib.sha256(path.read_bytes()).hexdigest() != checksum
    ):
        raise RuntimeError("Nsight installation contains changed or foreign files")
    return None


def archive_entries(path):
    process = subprocess.Popen(
        ["dpkg-deb", "--fsys-tarfile", str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for entry in archive:
                yield archive, entry
        if process.wait(timeout=60):
            raise RuntimeError("Nsight admitted archive cannot be inspected")
    finally:
        if process.stdout:
            process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()


def inventory(package, activation, admission, verification):
    # The original cache-only reader calls mkdir(exist_ok=True). Require the
    # directory first so observation cannot create it when ownership is missing.
    safe_parent(package["CACHE"] / "archive.deb")
    package["check_package_preconditions"](admission)
    if (
        package["file_state"](package["RECEIPT"]).get("missing")
        or json.loads(package["RECEIPT"].read_text()) != verification
    ):
        raise RuntimeError("Nsight repair requires its intact accepted local receipt")
    missing, seen = [], set()
    for tool, (name, version, _expected) in package["PACKAGES"].items():
        matches = [
            a for a in admission["artifacts"] if a["package"] == name and a["version"] == version
        ]
        if len(matches) != 1:
            raise RuntimeError("Nsight repair package identity is ambiguous")
        artifact = matches[0]
        path = package["download"](artifact, cached_only=True)
        installed = package["package_state"]().get(name + ":" + artifact["architecture"], {})
        if installed.get("status") != "install ok installed" or installed.get("version") != version:
            raise RuntimeError("Accepted Nsight package registration changed")
        for archive, entry in archive_entries(path):
            relative = Path(entry.name.removeprefix("./"))
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError("Unsafe Nsight archive path")
            destination = Path("/") / relative
            if entry.isdir():
                continue
            if not str(destination).startswith("/opt/"):
                continue  # Repairs deliberately exclude system wrappers and metadata.
            safe_parent(destination)
            if destination in seen:
                raise RuntimeError("Overlapping Nsight archive ownership")
            seen.add(destination)
            if entry.issym():
                if (
                    not destination.is_symlink()
                    or destination.lstat().st_uid != 0
                    or os.readlink(destination) != entry.linkname
                ):
                    raise RuntimeError("Nsight package symlink ownership changed")
                continue
            if not entry.isfile():
                raise RuntimeError("Unsupported Nsight package file ownership")
            stream = archive.extractfile(entry)
            checksum_state = hashlib.sha256()
            while chunk := stream.read(1024 * 1024):
                checksum_state.update(chunk)
            checksum = checksum_state.hexdigest()
            stream.close()
            if (
                str(destination) == verification["binaries"][tool]
                and checksum != verification["binarySha256"][tool]
            ):
                raise RuntimeError("Nsight accepted executable differs from its admitted archive")
            omission = regular_state(destination, checksum, entry.mode)
            if omission:
                missing.append(omission)
    for path, content in (
        (package["PROFILE"], package["profile_content"](verification["binaries"])),
        (activation["HOOK"], activation["CONTENT"]),
    ):
        omission = regular_state(path, hashlib.sha256(content.encode()).hexdigest(), 0o644)
        if omission:
            missing.append(omission)
    if len(missing) > 1024:
        raise RuntimeError("Nsight omission repair exceeds its bounded file inventory")
    return sorted(missing, key=lambda row: row["path"])


def publish_missing(source, destination):
    # Linux no-replace rename is atomic even if the process dies at publication.
    # Hardlink + unlink would leave an ambiguous extra link after abrupt exit.
    library = ctypes.CDLL(None, use_errno=True)
    rename = library.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        error = ctypes.get_errno()
        if error != errno.EEXIST:
            raise OSError(error, "Nsight atomic omission publication failed")


def create_file(path, stream, mode, checksum):
    safe_parent(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".cxcli-nsight-repair-", delete=False
        ) as output:
            temporary = Path(output.name)
            digest = hashlib.sha256()
            while chunk := stream.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
            if digest.hexdigest() != checksum:
                raise RuntimeError("Nsight restoration artifact changed")
            output.flush()
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
        publish_missing(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    regular_state(path, checksum, mode)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def restore(package, activation, request, missing):
    original = {row["path"]: row for row in request["omissions"]}
    if any(original.get(row["path"]) != row for row in missing):
        raise RuntimeError("Nsight omission preimage changed after repair admission")
    desired = {row["path"]: row for row in missing}
    for artifact in request["admission"]["artifacts"]:
        if artifact["package"] not in {row[0] for row in package["PACKAGES"].values()}:
            continue
        path = package["download"](artifact, cached_only=True)
        for archive, entry in archive_entries(path):
            destination = "/" + entry.name.removeprefix("./")
            row = desired.get(destination)
            if row and entry.isfile():
                with archive.extractfile(entry) as stream:
                    create_file(Path(destination), stream, row["mode"], row["sha256"])
                desired.pop(destination)
    import io

    for path, content in (
        (package["PROFILE"], package["profile_content"](request["verification"]["binaries"])),
        (activation["HOOK"], activation["CONTENT"]),
    ):
        if str(path) in desired:
            row = desired.pop(str(path))
            create_file(path, io.BytesIO(content.encode()), row["mode"], row["sha256"])
    if desired:
        raise RuntimeError("Nsight restoration lacks an admitted source")


def main(package, activation):
    request = package["decode_payload"](sys.argv[1])
    missing = inventory(package, activation, request["admission"], request["verification"])
    if request["action"] == "repair":
        package["validate_runtime_privileges"]()
        restore(package, activation, request, missing)
        activation["activate"]("verify")
        if package["verify"](request["admission"]) != request["verification"]:
            raise RuntimeError("Restored installation differs from its accepted receipt")
        return
    if request["action"] != "observe":
        raise RuntimeError("Unknown installation observation action")
    print(
        "CXCLI_NSIGHT="
        + json.dumps(
            {"state": "repairable" if missing else "healthy", "omissions": missing}, sort_keys=True
        )
    )
