"""Self-contained report-destination reconciler, run in the accepted login jail.

This contract is independent of package receipts and contains no credentials.
Readers compare mount identities within their own namespace, never across nodes.
"""

import fcntl
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from contextlib import suppress
from pathlib import Path, PurePosixPath

SCHEMA = "nebius-cxcli.nsight-reports/v1"
DESCRIPTOR = "/var/lib/nebius-cxcli/nsight-reports.json"
METADATA = ".cxcli-nsight"
MARKER = "binding.json"
MAX_BYTES = 16384
OWNER_UID = 0


def absolute(value):
    path = PurePosixPath(value)
    if not path.is_absolute() or str(path) != value or ".." in path.parts or value == "/":
        raise ValueError("Report destination requires a normalized absolute path")
    return path


def directory(path, *, shared=False):
    parts = absolute(str(path)).parts[1:]
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, part in enumerate(parts):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            mode = stat.S_IMODE(info.st_mode)
            leaf = index == len(parts) - 1
            if (
                info.st_uid not in {0, OWNER_UID}
                or (mode & 0o022 and not mode & stat.S_ISVTX)
                or (shared and leaf and (info.st_uid != OWNER_UID or mode != 0o1777))
            ):
                raise ValueError(
                    "Unsafe report directory: an operator must verify ownership and migrate "
                    "the exact shared root to root-owned mode 1777; ancestors must not be "
                    "group/world writable. No permissions were changed."
                )
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_json(parent, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != OWNER_UID
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o644
            or info.st_size > MAX_BYTES
        ):
            raise ValueError("Unsafe Nsight report binding record")
        return json.loads(stream.read(MAX_BYTES + 1))


def mount_identity(fd):
    info = Path(f"/proc/self/fdinfo/{fd}").read_text()
    match = re.search(r"^mnt_id:\s*(\d+)$", info, re.MULTILINE)
    if not match:
        raise ValueError("Cannot establish report mount identity")
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields = line.split()
        if fields[0] == match[1]:
            separator = fields.index("-")
            point = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[4])
            return {
                "id": fields[0],
                "device": fields[2],
                "point": point,
                "type": fields[separator + 1],
            }
    raise ValueError("Report mount is absent from this process namespace")


def write_json(parent, name, value):
    try:
        previous = read_json(parent, name)
    except FileNotFoundError:
        previous = None
    if previous == value:
        return
    temporary = ".binding-" + secrets.token_hex(16)
    fd = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
    )
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=parent)


def metadata_directory(root, *, create=True):
    created = False
    if create:
        try:
            os.mkdir(METADATA, 0o755, dir_fd=root)
            created = True
        except FileExistsError:
            pass
    fd = os.open(METADATA, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
    if created:
        os.fchmod(fd, 0o755)
        os.fsync(root)
    info = os.fstat(fd)
    if info.st_uid != OWNER_UID or stat.S_IMODE(info.st_mode) != 0o755:
        os.close(fd)
        raise ValueError("Unsafe Nsight storage binding directory")
    return fd


def validate_record(value):
    keys = {
        "schema",
        "installation",
        "storage",
        "producer_path",
        "viewer_path",
        "mount_point",
        "filesystem_type",
        "token",
    }
    if (
        not isinstance(value, dict)
        or set(value) not in (keys, keys | {"inactive"})
        or value.get("schema") != SCHEMA
        or not isinstance(value.get("token"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", value["token"])
        or ("inactive" in value and value["inactive"] is not True)
    ):
        raise ValueError("Foreign or malformed report binding; operator reconciliation required")


def writer_lock(parent):
    try:
        fd = os.open(
            "reconcile.lock",
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        os.fchmod(fd, 0o644)
        os.fsync(parent)
    except FileExistsError:
        fd = os.open("reconcile.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != OWNER_UID
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o644
        ):
            raise ValueError("Unsafe report reconciliation lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd)
        raise


def reconcile(request):
    """Close admission, prove quiescence, then publish marker and descriptor."""
    parent = directory(str(PurePosixPath(DESCRIPTOR).parent))
    root = marker_dir = old_root = old_meta = lock = old_lock = None
    try:
        if read_json(parent, "nsight.json") != request["verification"]:
            raise ValueError("Active jail does not match accepted Nsight installation")
        root = directory(request["producer_path"], shared=True)
        mount = mount_identity(root)
        if mount["point"] != request["mount_point"] or mount["point"] == "/":
            raise ValueError("Report directory is not on the accepted persistent mount")
        binding = {
            "schema": SCHEMA,
            "installation": request["installation"],
            "storage": request["storage"],
            "producer_path": request["producer_path"],
            "viewer_path": "/mnt/reports",
            "mount_point": mount["point"],
            "filesystem_type": mount["type"],
        }
        marker_dir = metadata_directory(root)
        # Retain exclusion even if the client loses its exec acknowledgement.
        # A surviving remote writer keeps this kernel lock until it terminates.
        lock = writer_lock(marker_dir)
        if mount_identity(marker_dir) != mount:
            raise ValueError("Report marker is on a different mount")

        def optional(fd, name):
            try:
                return read_json(fd, name)
            except FileNotFoundError:
                return None

        def matches(value):
            return (
                isinstance(value, dict)
                and {k: v for k, v in value.items() if k != "token"} == binding
            )

        def gate(token, opened):
            return {"schema": SCHEMA + "/admission", "token": token, "open": opened}

        previous = optional(parent, PurePosixPath(DESCRIPTOR).name)
        marker = optional(marker_dir, MARKER)
        for record in (previous, marker):
            if record is not None:
                validate_record(record)
        if (
            previous is not None
            and matches(previous)
            and marker is not None
            and matches(marker)
            and previous != marker
        ):
            raise ValueError("Report descriptor and storage marker disagree")
        selected = (
            marker
            if matches(marker)
            else previous
            if matches(previous)
            else {**binding, "token": secrets.token_hex(32)}
        )
        admission = optional(marker_dir, "admission.json")
        if admission is not None and (
            set(admission) != {"schema", "token", "open"}
            or admission["schema"] != SCHEMA + "/admission"
            or not isinstance(admission["open"], bool)
            or not isinstance(admission["token"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", admission["token"])
        ):
            raise ValueError("Foreign report admission record")
        unchanged = (
            previous == selected
            and marker == selected
            and admission == gate(selected["token"], True)
        )
        if unchanged:
            return {"schema": SCHEMA, "status": "ready", "token": selected["token"]}

        # Existing jobs validate their frozen binding, not the admission gate.
        # New jobs must observe an open gate after validating the binding. A job
        # that observed the old open gate already exists in Slurm and is included
        # by the subsequent authoritative squeue check.
        if previous is not None:
            old_root = directory(previous["producer_path"], shared=True)
            old_mount = mount_identity(old_root)
            if (
                old_mount["point"] != previous["mount_point"]
                or old_mount["type"] != previous["filesystem_type"]
            ):
                raise ValueError(
                    "Previous report storage is unavailable; operator reconciliation required"
                )
            old_meta = metadata_directory(old_root, create=False)
            if (os.fstat(old_meta).st_dev, os.fstat(old_meta).st_ino) != (
                os.fstat(marker_dir).st_dev,
                os.fstat(marker_dir).st_ino,
            ):
                old_lock = writer_lock(old_meta)
            if mount_identity(old_meta) != old_mount:
                raise ValueError("Previous report marker is on another filesystem")
            prior_marker = optional(old_meta, MARKER)
            recoverable_markers = (None, previous, {**previous, "inactive": True})
            if previous["producer_path"] == request["producer_path"]:
                recoverable_markers += (selected,)
            if prior_marker not in recoverable_markers:
                raise ValueError("Previous report binding is not intact")
            prior_admission = optional(old_meta, "admission.json")
            closed = (
                isinstance(prior_admission, dict)
                and set(prior_admission) == {"schema", "token", "open"}
                and prior_admission["schema"] == SCHEMA + "/admission"
                and prior_admission["open"] is False
                and isinstance(prior_admission["token"], str)
                and re.fullmatch(r"[0-9a-f]{64}", prior_admission["token"])
            )
            if prior_admission not in (None, gate(previous["token"], True)) and not closed:
                raise ValueError("Previous report admission is not intact")
            write_json(old_meta, "admission.json", gate(previous["token"], False))
        else:
            prior_admission = None
        # This may be the same directory as old_meta. Its token stays bound to
        # the still-current marker until quiescence permits a new binding.
        write_json(marker_dir, "admission.json", gate((marker or selected)["token"], False))
        try:
            active = subprocess.run(
                ["squeue", "--noheader"], capture_output=True, text=True, timeout=15, check=True
            )
            if active.stdout.strip():
                raise ValueError(
                    "Report binding change requires quiescent Slurm jobs; rerun profiling install after they finish"
                )
        except (ValueError, OSError, subprocess.SubprocessError):
            if old_meta is not None and prior_admission is not None:
                write_json(old_meta, "admission.json", prior_admission)
            elif admission is not None:
                write_json(marker_dir, "admission.json", admission)
            raise
        if previous is not None and not matches(previous):
            # An exact inactive marker is a recoverable interrupted preimage.
            write_json(old_meta, MARKER, {**previous, "inactive": True})
            os.unlink(PurePosixPath(DESCRIPTOR).name, dir_fd=parent)
            os.fsync(parent)
        write_json(marker_dir, MARKER, selected)
        write_json(parent, PurePosixPath(DESCRIPTOR).name, selected)
        if (
            read_json(marker_dir, MARKER) != selected
            or read_json(parent, PurePosixPath(DESCRIPTOR).name) != selected
        ):
            raise ValueError("Report binding readback changed")
        write_json(marker_dir, "admission.json", gate(selected["token"], True))
        return {"schema": SCHEMA, "status": "ready", "token": selected["token"]}
    finally:
        for fd in (old_lock, lock, old_meta, old_root, marker_dir, root, parent):
            if fd is not None:
                os.close(fd)


def main():
    import base64

    request = json.loads(base64.b64decode(sys.argv[1], validate=True))
    try:
        print(json.dumps(reconcile(request), sort_keys=True))
    except ValueError as exc:
        print(json.dumps({"schema": SCHEMA, "status": "failed", "reason": str(exc)}))
    except (OSError, subprocess.SubprocessError):
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "status": "failed",
                    "reason": "Cannot verify report storage, metadata or Slurm quiescence",
                }
            )
        )


if __name__ == "__main__":
    main()
