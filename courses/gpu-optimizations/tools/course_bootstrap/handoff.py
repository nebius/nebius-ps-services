"""Bounded private settings protocol; also usable as a stdlib-only SSH probe."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import uuid
from urllib.parse import urlsplit

LIMIT = 8192
MARKER = "# course-monitoring-identity/v1 "
FIELDS = {"COURSE_WORKSPACE", "COURSE_METRICS_URL", "COURSE_PUSHGATEWAY"}
IDENTITIES = {"cluster", "vmagent", "vmsingle", "metrics_service", "pushgateway_service"}


def sha(content):
    return hashlib.sha256(content.encode()).hexdigest()


def parse(content):
    if not isinstance(content, str) or len(content.encode()) > LIMIT or "\x00" in content:
        raise ValueError("Invalid or oversized monitoring settings")
    values, identity = {}, None
    for line in content.splitlines():
        if line.lstrip().startswith("# course-monitoring-identity/"):
            if identity is not None or not line.startswith(MARKER):
                raise ValueError("Unsupported or duplicate monitoring identity")
            identity = json.loads(line[len(MARKER):])
            if not isinstance(identity, dict) or identity.keys() != IDENTITIES or any(
                not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", v)
                for v in identity.values()
            ):
                raise ValueError("Invalid monitoring identity")
        elif line.strip() and not line.lstrip().startswith("#"):
            fields = shlex.split(line)
            if len(fields) != 2 or fields[0] != "export" or "=" not in fields[1]:
                raise ValueError("Invalid worker monitoring environment")
            key, value = fields[1].split("=", 1)
            if key not in FIELDS or key in values:
                raise ValueError("Worker monitoring environment has an unexpected field")
            values[key] = value
    if values.keys() != FIELDS:
        raise ValueError("Worker monitoring environment is incomplete")
    if identity is None:
        raise ValueError("Versioned monitoring identity is required; existing settings are preserved")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", values["COURSE_WORKSPACE"]):
        raise ValueError("Invalid publication workspace")
    for key in FIELDS - {"COURSE_WORKSPACE"}:
        url = urlsplit(values[key])
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username
                or url.password or url.query or url.fragment or any(c.isspace() for c in values[key])):
            raise ValueError("Invalid monitoring endpoint")
        _ = url.port  # Reject malformed ports before any mutation.
    return values, identity


def render(workspace, receipt):
    identity = {
        "cluster": receipt["cluster_identity"]["kubernetes_uid"],
        "vmagent": receipt["vmagent_uid"],
        "vmsingle": receipt["vmsingle_uid"],
        "metrics_service": receipt["service_uid"],
        "pushgateway_service": receipt["pushgateway_service_uid"],
    }
    content = MARKER + json.dumps(identity, sort_keys=True, separators=(",", ":")) + "\n"
    content += "".join(f"export {key}={shlex.quote(value)}\n" for key, value in {
        "COURSE_WORKSPACE": workspace,
        "COURSE_METRICS_URL": receipt["metrics_url"],
        "COURSE_PUSHGATEWAY": receipt["pushgateway_url"],
    }.items())
    parse(content)
    return content


def compatible(previous, candidate):
    new_values, new_identity = parse(candidate)
    if previous is not None:
        values, identity = parse(previous)
        if values != new_values:
            raise ValueError("Destination monitoring binding differs; use its original installation")
        if identity != new_identity:
            raise ValueError("Cluster or monitoring backend was replaced; existing binding is preserved")


def directory(path, *, private=False):
    """Pin a directory without following any component's symlink."""
    path = Path(os.path.abspath(path))
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022 or (
            private and stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ValueError("Require an owned safe directory")
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_private(fd, name):
    try:
        child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return None
    with os.fdopen(child, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > LIMIT):
            raise ValueError("Require a private owned bounded regular settings file")
        data = stream.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError("Oversized private settings")
        return data.decode()


def inspect(root):
    try:
        fd = directory(root)
    except FileNotFoundError:
        return {"content": None, "digest": None, "publication": False}
    try:
        content = read_private(fd, ".course-environment.sh")
        if content is not None:
            parse(content)
        publication = os.path.lexists(root / ".runtime/publications")
        if publication and content is None:
            raise ValueError("Publication state exists without monitoring settings")
        return {"content": content, "digest": sha(content) if content is not None else None,
                "publication": publication}
    finally:
        os.close(fd)


def unconfigured(snapshot):
    if snapshot["content"] is not None or snapshot["publication"]:
        raise ValueError("--local-only requires an unconfigured destination; use COURSE_AUTO_PUBLISH=0 per job")


def receive(root, request):
    from .state import setup_lock
    from verify_source_sync import verify

    root_fd = directory(root)
    try:
        with setup_lock(root / ".runtime"):
            # Recheck path identity after locking and immediately before replacement.
            current_fd = directory(root)
            try:
                if os.fstat(current_fd)[:2] != os.fstat(root_fd)[:2]:
                    raise ValueError("Destination changed during handoff")
            finally:
                os.close(current_fd)
            verify(root, request["sources"])
            current = inspect(root)
            if request.get("local_only"):
                unconfigured(current)
                return current["digest"]
            content = request["content"]
            compatible(current["content"], content)
            if current["digest"] != request["previous_digest"]:
                raise ValueError("Monitoring settings changed during synchronization; repeat sync")
            if current["publication"]:
                from course_publication_state import authority
                authority(root)
            if current["content"] == content:
                return sha(content)
            temporary = ".course-settings-" + uuid.uuid4().hex
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                         0o600, dir_fd=root_fd)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content.encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                latest = inspect(root)
                if latest != current:
                    raise ValueError("Monitoring settings changed during delivery")
                check_fd = directory(root)
                try:
                    if os.fstat(check_fd)[:2] != os.fstat(root_fd)[:2]:
                        raise ValueError("Destination changed during delivery")
                finally:
                    os.close(check_fd)
                os.replace(temporary, ".course-environment.sh", src_dir_fd=root_fd, dst_dir_fd=root_fd)
                os.fsync(root_fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=root_fd)
                except FileNotFoundError:
                    pass
            return sha(content)
    finally:
        os.close(root_fd)


def remote_main(action, destination):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", destination):
        raise ValueError("Invalid destination")
    root = Path.home() / destination
    if action == "inspect":
        print(json.dumps(inspect(root)))
    elif action == "receive":
        data = sys.stdin.buffer.read(256 * 1024 + 1)
        if len(data) > 256 * 1024:
            raise ValueError("Oversized handoff request")
        print(json.dumps({"digest": receive(root, json.loads(data))}))
    else:
        raise ValueError("Invalid internal handoff operation")


if __name__ == "__main__":
    try:
        remote_main(*sys.argv[1:])
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        sys.exit("ERROR: Private settings inspection failed; check ownership, format and destination.")
