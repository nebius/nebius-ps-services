"""Crash-safe promotion of one logical project-file generation."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import tempfile
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_BUNDLE_TRANSACTION_SCHEMA = "nebius-cxcli-project-bundle-transaction/v2"
_ABSENT_DIGEST = "absent"
_WRITE_ACTION = "write"
_DELETE_ACTION = "delete"
_STATE_DIRECTORY = ".nebius-cxcli"
_JOURNAL_FILENAME = "project-bundle-transaction.json"
_LOCK_FILENAME = "project-bundle-transaction.lock"
_GENERATIONS_DIRECTORY = "project-bundle-generations"


class ProjectBundleSafetyError(RuntimeError):
    """The transaction cannot safely classify or modify project state."""


@dataclass(frozen=True)
class ProjectBundleTarget:
    path: Path
    content: bytes | None
    action: str


@dataclass(frozen=True)
class ProjectBundlePreimage:
    """Immutable canonical target bytes and the matching transaction digest."""

    path: Path
    content: bytes | None
    sha256: str


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _is_sha256_digest(value: str) -> bool:
    return (
        len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _is_generation_id(value: str) -> bool:
    return len(value) == 32 and all(character in "0123456789abcdef" for character in value)


def _stable_json(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_fsynced(path: Path, content: bytes, *, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, mode)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)


def _atomic_owner_only_json(path: Path, payload: Mapping[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(_stable_json(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary.unlink()


def _require_owned_directory(path: Path, *, create: bool = False) -> None:
    if create:
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ProjectBundleSafetyError(f"project transaction directory is missing: {path}") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ProjectBundleSafetyError(f"project transaction path is not a safe directory: {path}")
    if metadata.st_uid != os.getuid():
        raise ProjectBundleSafetyError(
            f"project transaction directory is not owner-controlled: {path}"
        )


def _require_safe_ancestors(project_dir: Path, target: Path) -> None:
    relative = target.relative_to(project_dir)
    current = project_dir
    _require_owned_directory(current)
    for part in relative.parts[:-1]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ProjectBundleSafetyError(
                f"project transaction target has an unsafe parent: {target}"
            )
        if metadata.st_uid != os.getuid():
            raise ProjectBundleSafetyError(
                f"project transaction target parent is not owner-controlled: {target}"
            )


def _target_preimage(
    project_dir: Path,
    target: Path,
) -> tuple[ProjectBundlePreimage, int]:
    _require_safe_ancestors(project_dir, target)
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        return (
            ProjectBundlePreimage(
                path=target,
                content=None,
                sha256=_ABSENT_DIGEST,
            ),
            0o600,
        )
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ProjectBundleSafetyError(
            f"project transaction target is not a regular file: {target}"
        )
    if metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
        raise ProjectBundleSafetyError(
            f"project transaction target is not uniquely owner-controlled: {target}"
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags)
    except OSError as exc:
        raise ProjectBundleSafetyError(
            f"project transaction target changed while opening: {target}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
        ):
            raise ProjectBundleSafetyError(
                f"project transaction target changed while opening: {target}"
            )
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        finished = os.fstat(descriptor)
        if (
            finished.st_dev,
            finished.st_ino,
            finished.st_mode,
            finished.st_uid,
            finished.st_size,
            finished.st_mtime_ns,
            finished.st_ctime_ns,
            finished.st_nlink,
        ) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_uid,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
            opened.st_nlink,
        ):
            raise ProjectBundleSafetyError(
                f"project transaction target changed while reading: {target}"
            )
        try:
            current = target.lstat()
        except FileNotFoundError as exc:
            raise ProjectBundleSafetyError(
                f"project transaction target changed while reading: {target}"
            ) from exc
        if (
            current.st_dev,
            current.st_ino,
            current.st_mode,
            current.st_uid,
            current.st_size,
            current.st_mtime_ns,
            current.st_ctime_ns,
            current.st_nlink,
        ) != (
            finished.st_dev,
            finished.st_ino,
            finished.st_mode,
            finished.st_uid,
            finished.st_size,
            finished.st_mtime_ns,
            finished.st_ctime_ns,
            finished.st_nlink,
        ):
            raise ProjectBundleSafetyError(
                f"project transaction target changed while reading: {target}"
            )
        content = b"".join(chunks)
        return (
            ProjectBundlePreimage(
                path=target,
                content=content,
                sha256=_sha256(content),
            ),
            stat.S_IMODE(opened.st_mode),
        )
    finally:
        os.close(descriptor)


def _target_state(project_dir: Path, target: Path) -> tuple[str, int]:
    preimage, mode = _target_preimage(project_dir, target)
    return preimage.sha256, mode


def normalize_project_bundle_target(project_dir: Path, raw_path: Path) -> Path:
    """Return one lexical target under the physical project root."""

    project_dir = Path(project_dir).resolve(strict=True)
    candidate = raw_path if raw_path.is_absolute() else project_dir / raw_path
    candidate = Path(os.path.abspath(candidate))
    try:
        relative = candidate.relative_to(project_dir)
    except ValueError as exc:
        relative = None
        for alias_root in candidate.parents:
            try:
                if alias_root.resolve(strict=True) == project_dir:
                    relative = candidate.relative_to(alias_root)
                    break
            except (OSError, RuntimeError):
                continue
        if relative is None:
            raise ProjectBundleSafetyError(
                f"project transaction target escapes the project directory: {raw_path}"
            ) from exc
        candidate = project_dir / relative
    if not relative.parts:
        raise ProjectBundleSafetyError("project transaction cannot replace the project directory")
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise ProjectBundleSafetyError(f"project transaction target is invalid: {raw_path}")
    if relative.parts[0] == _STATE_DIRECTORY:
        raise ProjectBundleSafetyError(
            "project transaction targets cannot overlap transaction state"
        )
    return candidate


class ProjectBundleTransaction:
    """Commit and recover complete project-file generations without rollback."""

    def __init__(
        self,
        project_dir: Path,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        self.project_dir = Path(project_dir).resolve(strict=True)
        self.state_dir = self.project_dir / _STATE_DIRECTORY
        self.generations_dir = self.state_dir / _GENERATIONS_DIRECTORY
        self.journal_path = self.state_dir / _JOURNAL_FILENAME
        self.lock_path = self.state_dir / _LOCK_FILENAME
        self._failpoint = failpoint or (lambda _name: None)

    def _ensure_state_directories(self) -> None:
        _require_owned_directory(self.project_dir)
        _require_owned_directory(self.state_dir, create=True)
        os.chmod(self.state_dir, 0o700)
        _require_owned_directory(self.generations_dir, create=True)
        os.chmod(self.generations_dir, 0o700)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._ensure_state_directories()
        descriptor = os.open(
            self.lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise ProjectBundleSafetyError("project transaction lock is unsafe")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read_journal(self) -> dict[str, Any] | None:
        try:
            metadata = self.journal_path.lstat()
        except FileNotFoundError:
            return None
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ProjectBundleSafetyError("project transaction journal is unsafe")
        try:
            payload = json.loads(self.journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectBundleSafetyError("project transaction journal is invalid") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != PROJECT_BUNDLE_TRANSACTION_SCHEMA
        ):
            raise ProjectBundleSafetyError("project transaction journal has an unsupported schema")
        if payload.get("status") not in {"committed", "complete"}:
            raise ProjectBundleSafetyError("project transaction journal has an invalid status")
        return payload

    def _validated_entries(
        self,
        payload: Mapping[str, Any],
        *,
        require_staged: bool = True,
    ) -> list[tuple[Mapping[str, Any], Path, Path | None]]:
        generation_id = str(payload.get("generationId") or "")
        if not _is_generation_id(generation_id):
            raise ProjectBundleSafetyError("project transaction generation identity is invalid")
        logical_generation = str(payload.get("generationSha256") or "")
        if not _is_sha256_digest(logical_generation):
            raise ProjectBundleSafetyError(
                "project transaction logical generation digest is invalid"
            )
        generation_dir = self.generations_dir / generation_id
        if require_staged:
            _require_owned_directory(generation_dir)
        raw_targets = payload.get("targets")
        if not isinstance(raw_targets, list) or not raw_targets:
            raise ProjectBundleSafetyError("project transaction journal has no targets")
        entries: list[tuple[Mapping[str, Any], Path, Path | None]] = []
        seen: set[str] = set()
        for item in raw_targets:
            if not isinstance(item, Mapping):
                raise ProjectBundleSafetyError("project transaction target record is invalid")
            relative_text = str(item.get("path") or "")
            if relative_text in seen:
                raise ProjectBundleSafetyError("project transaction journal repeats a target")
            seen.add(relative_text)
            relative = Path(relative_text)
            if relative.is_absolute() or not relative.parts or ".." in relative.parts:
                raise ProjectBundleSafetyError("project transaction target record is unsafe")
            target = normalize_project_bundle_target(self.project_dir, relative)
            action = str(item.get("action") or "")
            if action not in {_WRITE_ACTION, _DELETE_ACTION}:
                raise ProjectBundleSafetyError("project transaction target action is invalid")
            new_digest = str(item.get("newSha256") or "")
            old_digest = str(item.get("oldSha256") or "")
            if old_digest != _ABSENT_DIGEST and not _is_sha256_digest(old_digest):
                raise ProjectBundleSafetyError("project transaction preimage digest is invalid")
            mode = item.get("mode")
            if (
                isinstance(mode, bool)
                or not isinstance(mode, int)
                or mode < 0
                or mode > 0o777
                or not mode & stat.S_IRUSR
            ):
                raise ProjectBundleSafetyError("project transaction target mode is invalid")
            staged: Path | None = None
            if action == _WRITE_ACTION:
                if not _is_sha256_digest(new_digest):
                    raise ProjectBundleSafetyError(
                        "project transaction write postimage digest is invalid"
                    )
                staged = generation_dir / relative
                _require_safe_ancestors(generation_dir, staged)
                if require_staged:
                    try:
                        staged_metadata = staged.lstat()
                    except FileNotFoundError as exc:
                        raise ProjectBundleSafetyError(
                            "committed project generation is incomplete"
                        ) from exc
                    if (
                        not stat.S_ISREG(staged_metadata.st_mode)
                        or stat.S_ISLNK(staged_metadata.st_mode)
                        or staged_metadata.st_uid != os.getuid()
                        or staged_metadata.st_nlink != 1
                    ):
                        raise ProjectBundleSafetyError(
                            "committed project generation contains an unsafe file"
                        )
                    if _sha256(staged.read_bytes()) != new_digest:
                        raise ProjectBundleSafetyError(
                            "committed project generation digest does not match"
                        )
            elif new_digest != _ABSENT_DIGEST or old_digest == _ABSENT_DIGEST:
                raise ProjectBundleSafetyError("project transaction deletion tombstone is invalid")
            entries.append((item, target, staged))
        return entries

    def _materialize(self, payload: dict[str, Any]) -> None:
        entries = self._validated_entries(payload)
        states: list[tuple[Mapping[str, Any], Path, Path | None, str]] = []
        for item, target, staged in entries:
            current_digest, _mode = _target_state(self.project_dir, target)
            old_digest = str(item["oldSha256"])
            new_digest = str(item["newSha256"])
            if current_digest not in {old_digest, new_digest}:
                raise ProjectBundleSafetyError(
                    f"project transaction target changed outside the committed generation: {target}"
                )
            states.append((item, target, staged, current_digest))

        for item, target, staged, current_digest in states:
            if current_digest == item["newSha256"]:
                continue
            if item["action"] == _DELETE_ACTION:
                current_digest, _mode = _target_state(self.project_dir, target)
                if current_digest != item["oldSha256"]:
                    raise ProjectBundleSafetyError(
                        f"project transaction target changed during materialization: {target}"
                    )
                target.unlink()
                _fsync_directory(target.parent)
                self._failpoint("after-materialize")
                continue
            if staged is None:
                raise ProjectBundleSafetyError("committed project generation is incomplete")
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _require_safe_ancestors(self.project_dir, target)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.", dir=target.parent
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as handle:
                    handle.write(staged.read_bytes())
                    handle.flush()
                    os.fsync(handle.fileno())
                os.fchmod(descriptor, int(item["mode"]))
                os.close(descriptor)
                descriptor = -1
                current_digest, _mode = _target_state(self.project_dir, target)
                if current_digest != item["oldSha256"]:
                    raise ProjectBundleSafetyError(
                        f"project transaction target changed during materialization: {target}"
                    )
                os.replace(temporary, target)
                _fsync_directory(target.parent)
                self._failpoint("after-materialize")
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                with suppress(FileNotFoundError):
                    temporary.unlink()

        completed = dict(payload)
        completed["status"] = "complete"
        _atomic_owner_only_json(self.journal_path, completed)
        self._failpoint("after-complete")

    def recover(self) -> bool:
        """Finish one committed generation; return whether a journal existed."""

        with self._locked():
            payload = self._read_journal()
            if payload is None:
                return False
            if payload["status"] == "committed":
                self._materialize(payload)
            return True

    def current_generation_sha256(self) -> str | None:
        """Recover and return the exact last committed logical generation."""

        with self._locked():
            payload = self._read_journal()
            if payload is None:
                return None
            if payload["status"] == "committed":
                self._materialize(payload)
                payload = self._read_journal()
                if payload is None:
                    raise ProjectBundleSafetyError(
                        "project transaction journal disappeared after recovery"
                    )
            entries = self._validated_entries(payload, require_staged=False)
            if any(
                _target_state(self.project_dir, target)[0] != item["newSha256"]
                for item, target, _staged in entries
            ):
                return None
            digest = str(payload.get("generationSha256") or "")
            if not _is_sha256_digest(digest):
                raise ProjectBundleSafetyError(
                    "project transaction logical generation digest is invalid"
                )
            return digest

    def snapshot_preimages(
        self,
        targets: Iterable[Path],
    ) -> dict[Path, ProjectBundlePreimage]:
        """Recover, then snapshot exact canonical target bytes under the lock."""

        normalized: list[Path] = []
        seen: set[Path] = set()
        for raw_path in targets:
            target = normalize_project_bundle_target(self.project_dir, Path(raw_path))
            if target in seen:
                raise ProjectBundleSafetyError(
                    f"project transaction snapshot repeats target: {target}"
                )
            seen.add(target)
            normalized.append(target)
        if not normalized:
            raise ValueError("project transaction snapshot requires at least one target")
        normalized.sort(key=lambda path: path.relative_to(self.project_dir).as_posix())

        with self._locked():
            prior = self._read_journal()
            if prior is not None and prior["status"] == "committed":
                self._materialize(prior)
            return {target: _target_preimage(self.project_dir, target)[0] for target in normalized}

    def commit(
        self,
        updates: Mapping[Path, bytes | str],
        *,
        removals: Iterable[Path] = (),
        expected_preimages: Mapping[Path, str] | None = None,
        generation_sha256: str | None = None,
    ) -> None:
        """Commit and materialize one complete generation of exact targets."""

        removal_paths = tuple(Path(path) for path in removals)
        if not updates and not removal_paths:
            raise ValueError("project transaction requires at least one target")
        with self._locked():
            prior = self._read_journal()
            if prior is not None and prior["status"] == "committed":
                self._materialize(prior)

            normalized: list[ProjectBundleTarget] = []
            seen: set[Path] = set()
            for raw_path, raw_content in updates.items():
                target = normalize_project_bundle_target(self.project_dir, Path(raw_path))
                if target in seen:
                    raise ValueError(f"project transaction repeats target: {target}")
                seen.add(target)
                content = (
                    raw_content.encode("utf-8") if isinstance(raw_content, str) else raw_content
                )
                normalized.append(
                    ProjectBundleTarget(
                        path=target,
                        content=bytes(content),
                        action=_WRITE_ACTION,
                    )
                )
            for raw_path in removal_paths:
                target = normalize_project_bundle_target(self.project_dir, raw_path)
                if target in seen:
                    raise ValueError(f"project transaction repeats target: {target}")
                seen.add(target)
                normalized.append(
                    ProjectBundleTarget(
                        path=target,
                        content=None,
                        action=_DELETE_ACTION,
                    )
                )
            normalized.sort(key=lambda item: item.path.relative_to(self.project_dir).as_posix())

            preimages: dict[Path, tuple[str, int]] = {
                item.path: _target_state(self.project_dir, item.path) for item in normalized
            }
            if expected_preimages is not None:
                expected = {
                    normalize_project_bundle_target(self.project_dir, Path(path)): str(digest)
                    for path, digest in expected_preimages.items()
                }
                if set(expected) != set(preimages):
                    raise ProjectBundleSafetyError(
                        "project transaction preimage authority has a different target set"
                    )
                changed = [path for path, state in preimages.items() if expected[path] != state[0]]
                if changed:
                    raise ProjectBundleSafetyError(
                        "project transaction target changed after generation admission: "
                        + ", ".join(str(path) for path in sorted(changed))
                    )
            logical_generation = str(generation_sha256 or "").strip()
            if logical_generation and not _is_sha256_digest(logical_generation):
                raise ValueError("project transaction logical generation must be a SHA-256")
            if not logical_generation:
                generation_payload = [
                    {
                        "action": item.action,
                        "path": item.path.relative_to(self.project_dir).as_posix(),
                        "sha256": (
                            _sha256(item.content) if item.content is not None else _ABSENT_DIGEST
                        ),
                    }
                    for item in normalized
                ]
                logical_generation = _sha256(_stable_json({"targets": generation_payload}))
            generation_id = uuid.uuid4().hex
            generation_dir = self.generations_dir / generation_id
            generation_dir.mkdir(mode=0o700)
            _require_owned_directory(generation_dir)
            targets_payload: list[dict[str, Any]] = []
            for item in normalized:
                relative = item.path.relative_to(self.project_dir)
                staged = generation_dir / relative
                old_digest, mode = preimages[item.path]
                if item.action == _DELETE_ACTION and old_digest == _ABSENT_DIGEST:
                    raise ProjectBundleSafetyError(
                        f"project transaction cannot delete an absent target: {item.path}"
                    )
                if item.content is not None:
                    staged.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    os.chmod(staged.parent, 0o700)
                    _write_fsynced(staged, item.content, mode=0o600)
                    _fsync_directory(staged.parent)
                targets_payload.append(
                    {
                        "action": item.action,
                        "path": relative.as_posix(),
                        "oldSha256": old_digest,
                        "newSha256": (
                            _sha256(item.content) if item.content is not None else _ABSENT_DIGEST
                        ),
                        "mode": mode,
                    }
                )
                self._failpoint("after-stage-file")
            _fsync_directory(generation_dir)
            _fsync_directory(self.generations_dir)
            self._failpoint("after-stage-generation")

            for item in normalized:
                if _target_state(self.project_dir, item.path)[0] != preimages[item.path][0]:
                    raise ProjectBundleSafetyError(
                        f"project transaction target changed before commit: {item.path}"
                    )
            self._failpoint("before-commit")
            journal = {
                "schema": PROJECT_BUNDLE_TRANSACTION_SCHEMA,
                "status": "committed",
                "generationId": generation_id,
                "generationSha256": logical_generation,
                "targets": targets_payload,
            }
            _atomic_owner_only_json(self.journal_path, journal)
            self._failpoint("after-commit")
            self._materialize(journal)


def recover_project_bundle(project_dir: Path) -> bool:
    """Reader gate for completing any committed project-file generation."""

    return ProjectBundleTransaction(project_dir).recover()
