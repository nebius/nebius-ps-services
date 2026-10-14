"""Private installation identity, durable state and nonblocking publication locks."""

from __future__ import annotations
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

SCHEMA = "course-publication/v1"
MAX_JSON = 32 * 1024 * 1024


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def read(path):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError(
            "Publication evidence/state must be a regular file without links"
        )
    if path.stat().st_size > MAX_JSON:
        raise ValueError("Publication JSON exceeds its size limit")
    return json.loads(
        path.read_text(),
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON")),
    )


def atomic(path, value):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Unsafe publication path")
    fd, name = tempfile.mkstemp(prefix=".publication-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def locked(path, *, timeout=0):
    """Never wait behind another job's network operation."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        yield
    finally:
        os.close(fd)


def settings(course):
    from course_bootstrap.runtime import context, monitoring
    from publish_results import LABEL, validate_endpoint

    root, _, _ = context(course)
    values = monitoring(root)
    if not values:
        return root, None
    workspace = values["COURSE_WORKSPACE"]
    if not LABEL.fullmatch(workspace):
        raise ValueError("Invalid publication workspace")
    return root, {
        "root": str(root),
        "workspace": workspace,
        "gateway": validate_endpoint(values["COURSE_PUSHGATEWAY"]),
        "metrics_url": validate_endpoint(values["COURSE_METRICS_URL"]),
    }


def authority(course):
    root, binding = settings(course)
    if binding is None:
        raise ValueError("Monitoring is unconfigured")
    directory = root / ".runtime/publications"
    identity = read(directory / "identity.json")
    if identity.get("schema") != SCHEMA or identity.get("binding") != binding:
        raise ValueError(
            "Publication installation binding differs; initialize the correct installation"
        )
    if not re.fullmatch(r"[0-9a-f]{32}", identity.get("publisher_id", "")):
        raise ValueError("Invalid publication installation identity")
    counter = read(directory / "sequence.json")
    if (
        counter.get("publisher_id") != identity["publisher_id"]
        or type(counter.get("sequence")) is not int
        or counter["sequence"] < 0
    ):
        raise ValueError("Publication sequence state is missing or inconsistent")
    index = read(directory / "groups.json")
    if index.get("publisher_id") != identity["publisher_id"] or not isinstance(
        index.get("keys"), list
    ):
        raise ValueError("Publication group registry is missing or inconsistent")
    for child in (
        directory,
        directory / "groups",
        directory / "runs",
        directory / "offers",
    ):
        if child.is_symlink() or not child.is_dir() or child.stat().st_mode & 0o077:
            raise ValueError("Publication state requires private directories")
    return directory, identity


def initialize(course):
    root, binding = settings(course)
    if binding is None:
        raise ValueError("Install private monitoring settings, then rerun the lab setup command")
    runtime = root / ".runtime"
    if runtime.is_symlink() or not runtime.is_dir():
        raise ValueError("Prepare the course runtime with the lab setup command")
    directory = runtime / "publications"
    with locked(runtime / "publication-init.lock"):
        if directory.exists() or directory.is_symlink():
            return authority(course)[1]
        temporary = Path(tempfile.mkdtemp(prefix=".publication-init-", dir=runtime))
        identity = {
            "schema": SCHEMA,
            "publisher_id": uuid.uuid4().hex,
            "binding": binding,
        }
        # A partial initialization never becomes an apparent valid authority.
        for name in ("groups", "runs", "offers"):
            (temporary / name).mkdir(mode=0o700)
        atomic(temporary / "identity.json", identity)
        atomic(
            temporary / "sequence.json",
            {"publisher_id": identity["publisher_id"], "sequence": 0},
        )
        atomic(
            temporary / "groups.json",
            {"publisher_id": identity["publisher_id"], "keys": []},
        )
        os.rename(temporary, directory)
        fd = os.open(runtime, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return identity


def register(course, job_path):
    directory, identity = authority(course)
    job = read(job_path)
    from course_bootstrap.runtime import load
    from course_execution import capabilities

    active = next((row for row in job["stages"] if row.get("runtime")), None)
    if active is None:
        active = capabilities(course)[job["lab_id"]]["variants"][0]
    _, runtime = load(course, active["runtime"], runtime_id=True)
    python = runtime["environment"]["COURSE_PUBLISH_PYTHON"]
    with locked(directory / "sequence.lock", timeout=2):
        counter = read(directory / "sequence.json")
        sequence = counter["sequence"] + 1
        if (directory / "runs" / f"{sequence}.json").exists():
            raise ValueError(
                "Publication counter cannot reuse an existing registration"
            )
        value = {
            "schema": SCHEMA,
            "publisher_id": identity["publisher_id"],
            "sequence": sequence,
            "course": course.name,
            "lab": job["lab_id"],
            "job_id": job["job_id"],
            "started_at": job["started_at"],
            "job_path": str(job_path.absolute()),
            "python": python,
        }
        atomic(directory / "sequence.json", {**counter, "sequence": sequence})
        atomic(directory / "runs" / f"{sequence}.json", value)
    return {"status": "registered", "registration": value}
