"""Local deployment recovery and process ownership, independent of S3 history."""

from __future__ import annotations

import fcntl
import json
import math
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .deployment_state import MAX_OBJECT_BYTES, ObjectVersion, _read_regular, canonical_json, digest
from .lease_clock import elapsed
from .owned_process import ExecutionScope
from .paths import ProjectPaths
from .terraform_backend import TerraformBackendSettings


class DeploymentLocalLock:
    """Fail-fast kernel lock; file presence alone never establishes ownership."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def __enter__(self) -> DeploymentLocalLock:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if not nofollow:
            raise RuntimeError("This platform cannot safely open the Deployment lock")
        try:
            self._fd = os.open(
                self.path,
                os.O_CREAT | os.O_RDWR | nofollow | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
        except OSError as exc:
            raise RuntimeError(f"Deployment lock is not a safe regular file: {self.path}") from exc
        opened = os.fstat(self._fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            os.close(self._fd)
            self._fd = None
            raise RuntimeError(f"Deployment lock is not a single-link regular file: {self.path}")
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self._fd)
            self._fd = None
            raise RuntimeError(f"Another deployment is using this project: {self.path}") from exc
        os.fchmod(self._fd, 0o600)
        os.ftruncate(self._fd, 0)
        os.write(
            self._fd,
            (
                json.dumps({"pid": os.getpid(), "createdAt": datetime.now(UTC).isoformat()}) + "\n"
            ).encode("utf-8"),
        )
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._fd is None:
            return
        # Close this reference without explicitly unlocking references inherited
        # by process supervisors that may still be stopping their children.
        os.close(self._fd)
        self._fd = None


class LocalObjectStore:
    """Owner-only, atomic local journals; callers hold the local mutation lock."""

    def __init__(self, root: Path) -> None:
        self.root = root.absolute()

    @classmethod
    def for_project(cls, paths: ProjectPaths) -> LocalObjectStore:
        return cls(paths.project_dir / ".nebius-cxcli" / "deployment-journals")

    def _path(self, key: str) -> Path:
        parts = key.split("/")
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("Invalid local deployment journal key")
        path = self.root.joinpath(*parts)
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise RuntimeError("Local deployment journals cannot use symbolic links")
        return path

    def read(self, key: str) -> ObjectVersion | None:
        path = self._path(key)
        try:
            content = _read_regular(path)
        except FileNotFoundError:
            return None
        value = json.loads(content)
        if not isinstance(value, dict):
            raise RuntimeError("Invalid local deployment journal")
        return ObjectVersion(value, digest(value))

    def attempt_keys(self, prefix: str) -> tuple[str, ...]:
        """Discover only input-addressed journals beneath this backend's local namespace."""
        directory = self._path(prefix + "/attempts")
        if not directory.exists():
            return ()
        return tuple(
            prefix + "/attempts/" + path.name
            for path in sorted(directory.iterdir())
            if re.fullmatch(r"[a-f0-9]{64}\.json", path.name)
        )

    def write(self, key: str, value: Mapping[str, Any], *, etag: str | None) -> str:
        content = canonical_json(value)
        if len(content) > MAX_OBJECT_BYTES:
            raise RuntimeError("Local deployment journal exceeds the size limit")
        path = self._path(key)
        previous = self.read(key)
        if (previous.etag if previous else None) != etag:
            raise RuntimeError("Local deployment journal changed during execution")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
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
        return digest(value)


class LocalExecutionOwner:
    """Hold a kernel lock and supervise children for the current invocation."""

    def __init__(self, *, settings: TerraformBackendSettings, operation_id: str) -> None:
        self.settings = settings
        self.operation_id = operation_id
        self.holder_identity = "cxcli-local-" + uuid4().hex
        key = digest(
            {"endpoint": settings.endpoint, "bucket": settings.bucket, "key": settings.key}
        )
        self.lock = DeploymentLocalLock(
            Path.home() / ".cache" / "nebius-cxcli" / "locks" / (key[7:] + ".lock")
        )
        self.scope = ExecutionScope(self.holder_identity, math.inf)
        self._held = False

    def __enter__(self) -> LocalExecutionOwner:
        self.lock.__enter__()
        try:
            self.scope.ownership_fd = self.lock._fd
            self.scope.activate()
        except BaseException:
            self.lock.__exit__(None, None, None)
            raise
        self._held = True
        return self

    def assert_held(self) -> None:
        if not self._held:
            raise RuntimeError("Local deployment execution has ended")
        self.scope.check()

    def bind_cluster_identity(self, *, cluster_id: str, kubernetes_uid: str) -> None:
        self.assert_held()
        if not cluster_id or not kubernetes_uid:
            raise RuntimeError("Deployment cluster identity is incomplete")

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            clean = self.scope.close(elapsed() + 60)
        finally:
            self._held = False
            try:
                self.scope.deactivate()
            finally:
                self.lock.__exit__(exc_type, exc, traceback)
        if not clean:
            message = "Contained deployment process shutdown was not confirmed"
            if exc is not None:
                exc.add_note(message)
            else:
                raise RuntimeError(message)
