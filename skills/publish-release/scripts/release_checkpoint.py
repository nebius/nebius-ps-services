"""Owner-private release progress storage; checkpoints never authorize effects."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path

SCHEMA = "publish-release/session-v1"
SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*-v[0-9]+\.[0-9]+\.[0-9]+\Z")


class ReleaseError(Exception):
    """Safe, bounded diagnostic with no raw command output."""


def secure_dir(path: Path) -> None:
    if path.is_symlink():
        raise ReleaseError("Private state directory cannot be a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise ReleaseError(
            "Private state directory must be owned by this user and mode 0700"
        )


def secure_fd(path: Path, flags: int) -> int:
    fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or info.st_nlink != 1
    ):
        os.close(fd)
        raise ReleaseError("Unsafe private checkpoint or lock file")
    return fd


def state_home() -> Path:
    # Match the selected host without redirecting another host's environment.
    if os.environ.get("SKILLS_AGENT") == "claude" or os.environ.get(
        "CLAUDE_CONFIG_DIR"
    ):
        home = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    else:
        home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    # Check every existing ancestor before following any private path.
    for part in (home, *home.parents):
        if part.is_symlink():
            raise ReleaseError("Agent home cannot traverse symlinks")
    root = home / "publish-release"
    secure_dir(root)
    return root


class Store:
    def __init__(self, root: Path, scope: dict):
        self.scope = scope
        checkout = {k: v for k, v in scope.items() if k != "project"}
        key = hashlib.sha256(json.dumps(checkout, sort_keys=True).encode()).hexdigest()
        self.project_key = hashlib.sha256(scope["project"].encode()).hexdigest()[:16]
        self.directory = root / key
        secure_dir(root)
        secure_dir(self.directory)

    @contextlib.contextmanager
    def locked(self):
        fd = secure_fd(self.directory / "lock", os.O_CREAT | os.O_RDWR)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ReleaseError(
                    "Another release observer is active for this project"
                ) from exc
            yield
        finally:
            os.close(fd)

    def path(self, tag: str) -> Path:
        if not TAG.fullmatch(tag):
            raise ReleaseError("Expected a normalized project-vMAJOR.MINOR.PATCH tag")
        return self.directory / f"{self.project_key}-{tag}.json"

    def require_preparation_available(self, tag: str) -> None:
        for path in self.directory.glob("*.json"):
            if path == self.path(tag):
                continue
            with os.fdopen(secure_fd(path, os.O_RDONLY)) as stream:
                other = json.load(stream)
            if other.get("schema") != SCHEMA:
                raise ReleaseError("Unknown release checkpoint in shared checkout")
            if other.get("pr") is None and not other.get("complete"):
                raise ReleaseError(
                    "Another release is preparing this checkout; resume it first"
                )

    def load(self, tag: str) -> dict:
        try:
            with os.fdopen(secure_fd(self.path(tag), os.O_RDONLY)) as stream:
                value = json.load(stream)
        except (OSError, ValueError) as exc:
            raise ReleaseError("Release checkpoint is absent or malformed") from exc
        if (
            value.get("schema") != SCHEMA
            or value.get("scope") != self.scope
            or value.get("tag") != tag
        ):
            raise ReleaseError("Release checkpoint schema or identity mismatch")
        required = {
            "branch",
            "base",
            "workflow",
            "assets",
            "pr",
            "head",
            "merge",
            "tag_object",
            "run",
            "waits",
            "complete",
        }
        if not required <= value.keys() or not isinstance(value["waits"], dict):
            raise ReleaseError("Incomplete release checkpoint")
        for key in ("head", "merge", "tag_object"):
            if value[key] is not None and not SHA.fullmatch(value[key]):
                raise ReleaseError("Malformed release commit identity")
        return value

    def save(self, value: dict) -> None:
        path = self.path(value["tag"])
        if path.exists() or path.is_symlink():
            os.close(secure_fd(path, os.O_RDONLY))
        fd, name = tempfile.mkstemp(prefix=".checkpoint-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(value, stream, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def select(self, tag: str | None) -> dict:
        if tag:
            return self.load(tag)
        values = [
            self.load(p.stem[len(self.project_key) + 1 :])
            for p in self.directory.glob(f"{self.project_key}-*.json")
        ]
        pending = [v for v in values if not v["complete"]]
        if len(pending) != 1:
            raise ReleaseError(
                "Specify --tag: expected exactly one unfinished project release"
            )
        return pending[0]
