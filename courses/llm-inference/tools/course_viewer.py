#!/usr/bin/env python3
"""Bounded delivery of finished native reports to the installed Nsight viewers."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import uuid

DESCRIPTOR = "/var/lib/nebius-cxcli/nsight-reports.json"
ROOT_UID = 0
SCHEMA = "nebius-cxcli.nsight-reports/v1"
MANIFEST_SCHEMA = "course-viewer-report-set/v1"
DELIVERY_SECONDS = 300
TERMINATION_SECONDS = 10
PREFLIGHT_SECONDS = 10
READBACK_SECONDS = 20
MAX_MESSAGE = 8 * 1024 * 1024


def eligible(row):
    return row["status"] != "skipped" and row["profiler"] in {"nsys", "ncu"}


def delivery_budget(rows):
    count = sum(eligible(row) for row in rows)
    return count * (DELIVERY_SECONDS + TERMINATION_SECONDS) + (
        PREFLIGHT_SECONDS + TERMINATION_SECONDS if count else 0
    )


def component(value):
    if (
        not isinstance(value, str)
        or value in {".", ".."}
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,181}", value)
    ):
        raise ValueError("Unsafe report destination component")
    return value


def relative(value):
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or str(path) != value
        or not path.parts
        or any(p in {".", ".."} for p in path.parts)
    ):
        raise ValueError("Report path must be relative and contained")
    return path


def directory(path, *, shared=False):
    value = str(path)
    parsed = PurePosixPath(value)
    if (
        not parsed.is_absolute()
        or str(parsed) != value
        or ".." in parsed.parts
        or value == "/"
    ):
        raise ValueError("Unsafe installed report directory")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, part in enumerate(parsed.parts[1:]):
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            mode = stat.S_IMODE(info.st_mode)
            leaf = index == len(parsed.parts) - 2
            if info.st_uid not in {0, ROOT_UID} or (
                mode & 0o022 and not mode & stat.S_ISVTX
            ):
                raise ValueError(
                    "Unsafe installed report directory ownership or permissions"
                )
            if shared and leaf and (info.st_uid != ROOT_UID or mode != 0o1777):
                raise ValueError(
                    "Report root requires installer-owned migration to root-owned mode 1777"
                )
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_json(parent, name, *, owner=ROOT_UID, limit=16384):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != owner
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o644
            or info.st_size > limit
        ):
            raise ValueError("Unsafe report binding or manifest")
        value = json.loads(stream.read(limit + 1))
        if not isinstance(value, dict):
            raise ValueError("Invalid report binding or manifest")
        return value


def mount_identity(fd):
    match = re.search(
        r"^mnt_id:\s*(\d+)$", Path(f"/proc/self/fdinfo/{fd}").read_text(), re.MULTILINE
    )
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


def binding(expected=None):
    """Return verified metadata and a retained descriptor for its mounted root."""
    parent = directory(str(PurePosixPath(DESCRIPTOR).parent))
    root = meta = None
    try:
        value = read_json(parent, PurePosixPath(DESCRIPTOR).name)
        if (
            set(value)
            != {
                "schema",
                "installation",
                "storage",
                "producer_path",
                "viewer_path",
                "mount_point",
                "filesystem_type",
                "token",
            }
            or value["schema"] != SCHEMA
            or value["viewer_path"] != "/mnt/reports"
            or not isinstance(value["token"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["token"])
            or not isinstance(value["installation"], dict)
            or not isinstance(value["storage"], dict)
            or set(value["installation"]) != {"generation", "target", "kubernetes_uid"}
            or set(value["storage"])
            != {"namespace", "pvcName", "pvcUid", "pvName", "pvUid"}
            or any(
                not isinstance(v, str) or not v
                for group in (value["installation"], value["storage"])
                for v in group.values()
            )
            or value["mount_point"] == "/"
            or (expected is not None and value != expected)
        ):
            raise ValueError(
                "Missing, stale or invalid Nsight report destination; rerun profiling install"
            )
        root = directory(value["producer_path"], shared=True)
        if not PurePosixPath(value["producer_path"]).is_relative_to(
            value["mount_point"]
        ):
            raise ValueError("Report directory lies outside its installed mount")
        mount = mount_identity(root)
        if (
            mount["point"] != value["mount_point"]
            or mount["type"] != value["filesystem_type"]
        ):
            raise ValueError("Report destination is not on the installed mount")
        meta = os.open(
            ".cxcli-nsight", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root
        )
        info = os.fstat(meta)
        if (
            info.st_uid != ROOT_UID
            or stat.S_IMODE(info.st_mode) != 0o755
            or mount_identity(meta) != mount
        ):
            raise ValueError("Unsafe report storage marker directory")
        if read_json(meta, "binding.json") != value:
            raise ValueError(
                "Report storage marker does not match the installed destination"
            )
        if expected is None and read_json(meta, "admission.json") != {
            "schema": SCHEMA + "/admission",
            "token": value["token"],
            "open": True,
        }:
            raise ValueError(
                "Profiling admission is closed while the installer changes its report binding"
            )
        result, root = (value, root), None
        return result
    finally:
        for fd in (meta, root, parent):
            if fd is not None:
                os.close(fd)


def owned_child(parent, name, *, create=False, mode=0o755):
    component(name)
    if create:
        try:
            os.mkdir(name, mode, dir_fd=parent)
            created = True
        except FileExistsError:
            created = False
    else:
        created = False
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    if created:
        os.fchmod(fd, mode)
        os.fsync(parent)
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != mode:
        os.close(fd)
        raise ValueError("Foreign or unsafe report run directory")
    return fd


def report_fd(parent, name):
    parts = relative(name).parts
    fd = os.dup(parent)
    try:
        for part in parts[:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = child
        result = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
        )
        info = os.fstat(result)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_size <= 0
        ):
            os.close(result)
            raise ValueError("Native report must be a nonempty owned regular file")
        return result
    finally:
        os.close(fd)


def fingerprint(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def checksum(fd):
    digest = hashlib.sha256()
    size = 0
    while chunk := os.read(fd, 1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
    return size, digest.hexdigest()


def verify_set(fd, manifest):
    if read_json(fd, "manifest.json", owner=os.getuid(), limit=MAX_MESSAGE) != manifest:
        raise ValueError("Report destination conflicts with the requested manifest")
    expected = {"manifest.json"}
    for row in manifest["reports"]:
        path = relative(row["path"])
        expected.add(str(path))
        expected.update(str(parent) for parent in path.parents if str(parent) != ".")
        source = report_fd(fd, row["path"])
        try:
            if checksum(source) != (row["size"], row["sha256"]):
                raise ValueError("Published report checksum differs from its manifest")
        finally:
            os.close(source)
    # A matching manifest alone must not bless extra or linked content.
    actual = set()

    def walk(parent, prefix=""):
        for name in os.listdir(parent):
            path = prefix + name
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o755:
                    raise ValueError("Unsafe published report directory")
                child = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
                )
                try:
                    walk(child, path + "/")
                finally:
                    os.close(child)
            elif (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o644
            ):
                raise ValueError("Unsafe published report entry")
            actual.add(path)

    walk(fd)
    if actual != expected:
        raise ValueError("Published report set contains unexpected entries")


def rename_noreplace(source_parent, source, destination_parent, destination):
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    if rename is None:
        raise OSError(
            errno.ENOTSUP, "Atomic no-overwrite report publication is unsupported"
        )
    rename.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    if rename(
        source_parent,
        os.fsencode(source),
        destination_parent,
        os.fsencode(destination),
        1,
    ):
        code = ctypes.get_errno()
        raise OSError(code, "Atomic report publication failed")


def prepare(request, root):
    stage = request["stage"]
    run = owned_child(root, request["run"], create=True)
    wrapper_name = ".staging-" + uuid.uuid4().hex
    wrapper = candidate = source_root = None
    try:
        wrapper = owned_child(run, wrapper_name, create=True, mode=0o700)
        candidate = owned_child(wrapper, stage, create=True)
        source_root = os.open(
            request["source"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        names = request["reports"]
        if not names or len(names) > 10000 or len(set(names)) != len(names):
            raise ValueError("Native report set is missing or ambiguous")
        rows = []
        for name in sorted(names):
            path = relative(name)
            if (
                path.suffix
                != {"nsys": ".nsys-rep", "ncu": ".ncu-rep"}[request["profiler"]]
            ):
                raise ValueError(
                    "Native report type does not match the completed stage"
                )
            src = report_fd(source_root, name)
            destination_parent = os.dup(candidate)
            try:
                before = fingerprint(os.fstat(src))
                for part in path.parts[:-1]:
                    child = owned_child(destination_parent, part, create=True)
                    os.close(destination_parent)
                    destination_parent = child
                dst = os.open(
                    path.name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=destination_parent,
                )
                try:
                    digest = hashlib.sha256()
                    while chunk := os.read(src, 1024 * 1024):
                        digest.update(chunk)
                        data = memoryview(chunk)
                        while data:
                            written = os.write(dst, data)
                            if written <= 0:
                                raise OSError("Short report write")
                            data = data[written:]
                    if fingerprint(os.fstat(src)) != before:
                        raise ValueError("Native report changed during delivery")
                    os.fchmod(dst, 0o644)
                    os.fsync(dst)
                    os.lseek(dst, 0, os.SEEK_SET)
                    size, copied = checksum(dst)
                    if size != before[2] or copied != digest.hexdigest():
                        raise ValueError("Copied report failed checksum verification")
                    rows.append({"path": name, "size": size, "sha256": copied})
                finally:
                    os.close(dst)
                os.fsync(destination_parent)
            finally:
                os.close(src)
                os.close(destination_parent)
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "run": request["run"],
            "stage": stage,
            "reports": rows,
        }
        fd = os.open(
            "manifest.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=candidate,
        )
        with os.fdopen(fd, "w") as stream:
            json.dump(manifest, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.fsync(candidate)
        os.fsync(wrapper)
        result = (run, wrapper, candidate, wrapper_name, manifest)
        run = wrapper = candidate = None
        return result
    finally:
        for fd in (source_root, candidate, wrapper, run):
            if fd is not None:
                os.close(fd)


def emit(value):
    print(json.dumps(value), flush=True)


def worker(request):
    value, root = binding(request.get("binding"))
    try:
        if request["action"] == "validate":
            emit({"status": "ready", "binding": value})
            return
        component(request["run"])
        component(request["stage"])
        if request["action"] == "inspect":
            run = owned_child(root, request["run"])
            try:
                stage = owned_child(run, request["stage"])
                try:
                    verify_set(stage, request["manifest"])
                finally:
                    os.close(stage)
            finally:
                os.close(run)
            emit({"status": "ready"})
            return
        run, wrapper, candidate, wrapper_name, manifest = prepare(request, root)
        try:
            emit({"status": "prepared", "manifest": manifest})
            if sys.stdin.readline() != "commit\n":
                raise ValueError("Report publication was not authorized")
            _, current_root = binding(value)
            try:
                if (
                    mount_identity(current_root) != mount_identity(root)
                    or fingerprint(os.fstat(current_root))[:2]
                    != fingerprint(os.fstat(root))[:2]
                ):
                    raise ValueError("Report destination changed before publication")
            finally:
                os.close(current_root)
            try:
                rename_noreplace(wrapper, request["stage"], run, request["stage"])
            except FileExistsError:
                previous = owned_child(run, request["stage"])
                try:
                    verify_set(previous, manifest)
                finally:
                    os.close(previous)
            os.fsync(run)
            emit({"status": "ready"})
            try:
                os.rmdir(wrapper_name, dir_fd=run)
            except OSError:
                pass  # Exact retries can retain a private prepared candidate.
        finally:
            for fd in (candidate, wrapper, run):
                os.close(fd)
            # Keep partial staging private on failures. No recursive cleanup is
            # needed for correctness, and no uncertain writer is raced by deletion.
    finally:
        os.close(root)


def stop(child):
    for signum, duration in (
        (signal.SIGTERM, TERMINATION_SECONDS / 2),
        (signal.SIGKILL, TERMINATION_SECONDS / 2),
    ):
        if child.poll() is not None:
            return True
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=duration)
            return True
        except subprocess.TimeoutExpired:
            pass
    return child.poll() is not None


def exchange(request, seconds, *, prepared=None, cancelled=lambda: False):
    """No report-filesystem I/O in the supervisor, including recovery reads."""
    deadline = time.monotonic() + seconds
    try:
        outgoing = json.dumps(request).encode() + b"\n"
        if len(outgoing) > MAX_MESSAGE:
            raise ValueError("Report publisher request exceeds limit")
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).absolute()), "worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            bufsize=0,
        )
    except (OSError, ValueError):
        return {
            "status": "failed",
            "reason": "Cannot start report delivery",
            "quiescent": True,
            "manifest": None,
            "commit_sent": False,
        }
    selector = selectors.DefaultSelector()
    manifest = None
    commit_sent = False
    final = None
    quiet = None
    try:
        os.set_blocking(child.stdin.fileno(), False)
        selector.register(child.stdin, selectors.EVENT_WRITE)
        selector.register(child.stdout, selectors.EVENT_READ)
        pending = b""
        while time.monotonic() < deadline and not cancelled():
            for key, _ in selector.select(
                min(0.1, max(0, deadline - time.monotonic()))
            ):
                if key.fileobj is child.stdin:
                    try:
                        written = os.write(key.fd, outgoing[:65536])
                    except BlockingIOError:
                        continue
                    outgoing = outgoing[written:]
                    if not outgoing:
                        selector.unregister(child.stdin)
                    continue
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                pending += chunk
                if len(pending) > MAX_MESSAGE:
                    raise ValueError("Report publisher response exceeds limit")
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    message = json.loads(line)
                    if message["status"] == "prepared":
                        if manifest is not None or prepared is None:
                            raise ValueError("Unexpected report preparation response")
                        manifest = message["manifest"]
                        prepared(manifest)
                        if cancelled() or time.monotonic() >= deadline:
                            break
                        commit_sent = True
                        outgoing = b"commit\n"
                        selector.register(child.stdin, selectors.EVENT_WRITE)
                    else:
                        final = message
            if child.poll() is not None and not selector.get_map():
                break
        quiet = stop(child)
        if quiet and child.returncode == 0 and final and final.get("status") == "ready":
            return {
                **final,
                "quiescent": True,
                "manifest": manifest,
                "commit_sent": commit_sent,
            }
        return {
            "status": "unconfirmed" if commit_sent or not quiet else "failed",
            "reason": "Report delivery interrupted, timed out or failed; private originals retained",
            "quiescent": quiet,
            "manifest": manifest,
            "commit_sent": commit_sent,
        }
    except (OSError, ValueError, KeyError, TypeError) as exc:
        quiet = stop(child)
        return {
            "status": "unconfirmed" if commit_sent or not quiet else "failed",
            "reason": f"Report delivery failed ({type(exc).__name__}); private originals retained",
            "quiescent": quiet,
            "manifest": manifest,
            "commit_sent": commit_sent,
        }
    finally:
        if quiet is None:
            stop(child)
        selector.close()
        child.stdin.close()
        child.stdout.close()


def preflight(cancelled=lambda: False):
    result = exchange({"action": "validate"}, PREFLIGHT_SECONDS, cancelled=cancelled)
    if result["status"] != "ready":
        raise ValueError(
            "Nsight viewer destination unavailable or unsafe; an operator must reconcile profiling install, report permissions and storage"
        )
    return result["binding"]


def deliver(source, row, installed, run, *, prepared, cancelled=lambda: False):
    prefix = PurePosixPath("stages") / row["id"]
    names = [
        str(PurePosixPath(name).relative_to(prefix)) for name in row["native_reports"]
    ]
    request = {
        "action": "publish",
        "binding": installed,
        "source": str(source),
        "reports": names,
        "profiler": row["profiler"],
        "run": run,
        "stage": row["id"],
    }
    # Both exchanges can require termination. Keep their combined worst case
    # within the one 300-second delivery plus 10-second termination reservation.
    result = exchange(
        request,
        DELIVERY_SECONDS - READBACK_SECONDS - TERMINATION_SECONDS,
        prepared=prepared,
        cancelled=cancelled,
    )
    if (
        result["status"] == "unconfirmed"
        and result["quiescent"]
        and result["manifest"] is not None
        and not cancelled()
    ):
        check = exchange(
            {**request, "action": "inspect", "manifest": result["manifest"]},
            READBACK_SECONDS,
            cancelled=cancelled,
        )
        if check["status"] == "ready":
            result["status"] = "ready"
            result.pop("reason", None)
        result["quiescent"] = check["quiescent"]
    if result["status"] == "ready":
        result["viewer_paths"] = [
            f"{installed['viewer_path']}/{run}/{row['id']}/{name}" for name in names
        ]
    result.pop("commit_sent", None)
    return result


if __name__ == "__main__":
    os.umask(0o077)
    try:
        worker(json.loads(sys.stdin.buffer.readline(MAX_MESSAGE + 1)))
    except (OSError, ValueError, KeyError, TypeError):
        emit({"status": "failed"})
        raise SystemExit(2)
