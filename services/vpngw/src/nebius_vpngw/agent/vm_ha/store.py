"""Crash-safe local persistence for immutable VM-HA generations."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import (
    GenerationRevision,
    ReplayState,
    StalePeerStateError,
    StateValidationError,
    TransitionRecord,
    canonical_json,
)

FaultHook = Callable[[str, Path], None]
_PEER_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")


class CorruptStateError(StateValidationError):
    """Raised when durable state fails canonical or checksum validation."""


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write JSON durably without exposing a partial destination file."""

    parent_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not parent_existed:
        _fsync_directory(path.parent)
        _fsync_directory(path.parent.parent)
    serialized = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        json.loads(temporary.read_text(encoding="utf-8"))
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    serialized = canonical_json(payload)
    return {
        "payload": payload,
        "schema": "nebius-vpngw/vm-ha-envelope-v1",
        "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    }


def _unwrap(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"schema", "sha256", "payload"}:
        raise CorruptStateError("state envelope has an invalid shape")
    if value["schema"] != "nebius-vpngw/vm-ha-envelope-v1":
        raise CorruptStateError("state envelope has an unsupported schema")
    payload = value["payload"]
    if not isinstance(payload, Mapping):
        raise CorruptStateError("state envelope payload must be an object")
    actual = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    if value["sha256"] != actual:
        raise CorruptStateError("state envelope checksum mismatch")
    return payload


@dataclass(frozen=True)
class GenerationPointers:
    """One atomic set of generation roles."""

    committed: str | None = None
    previous: str | None = None
    last_known_good: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "committed": self.committed,
            "last_known_good": self.last_known_good,
            "previous": self.previous,
            "schema": "nebius-vpngw/vm-ha-pointers-v1",
        }

    @classmethod
    def from_mapping(cls, value: object) -> GenerationPointers:
        if not isinstance(value, Mapping):
            raise CorruptStateError("generation pointers must be an object")
        if set(value) != {"schema", "committed", "previous", "last_known_good"}:
            raise CorruptStateError("generation pointers have an invalid shape")
        if value["schema"] != "nebius-vpngw/vm-ha-pointers-v1":
            raise CorruptStateError("generation pointers have an unsupported schema")
        pointers = cls(value["committed"], value["previous"], value["last_known_good"])
        for generation_id in (
            pointers.committed,
            pointers.previous,
            pointers.last_known_good,
        ):
            if generation_id is not None:
                GenerationRevision.from_mapping(
                    {
                        "schema": "nebius-vpngw/vm-ha-generation-v1",
                        "cluster_id": "validation",
                        "node_id": "validation",
                        "generation_id": generation_id,
                        "digests": {
                            "configuration": generation_id,
                            "static_routes": "0" * 64,
                            "bgp_policy": "0" * 64,
                        },
                        "committed_at": "validationZ",
                    }
                )
        return pointers


class AtomicGenerationStore:
    """Persist immutable revisions, atomic pointers, and bounded audit evidence."""

    def __init__(
        self,
        root: Path,
        *,
        revision_retention: int = 3,
        journal_retention: int = 128,
        fault_hook: FaultHook | None = None,
    ) -> None:
        if revision_retention < 3:
            raise ValueError("revision_retention must retain at least three revisions")
        if journal_retention < 1:
            raise ValueError("journal_retention must be positive")
        self.root = root
        self.revisions = root / "revisions"
        self.journal = root / "journal"
        self.peers = root / "peers"
        self.pointer_path = root / "pointers.json"
        self.pointer_backup_path = root / "pointers.backup.json"
        self.lock_path = root / ".lock"
        self.revision_retention = revision_retention
        self.journal_retention = journal_retention
        self._fault_hook = fault_hook
        self.revisions.mkdir(parents=True, exist_ok=True)
        self.journal.mkdir(parents=True, exist_ok=True)
        self.peers.mkdir(parents=True, exist_ok=True)
        for directory in (self.revisions, self.journal, self.peers, self.root):
            _fsync_directory(directory)
        _fsync_directory(self.root.parent)

    def _checkpoint(self, name: str, path: Path) -> None:
        if self._fault_hook is not None:
            self._fault_hook(name, path)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def stage(self, revision: GenerationRevision) -> None:
        """Durably add an immutable revision without making it committed."""

        destination = self.revisions / revision.generation_id
        with self._locked():
            if destination.exists():
                if self._read_revision(destination) != revision:
                    raise CorruptStateError("generation identity already contains different state")
                return

            temporary = Path(
                tempfile.mkdtemp(prefix=f".{revision.generation_id}.", dir=self.revisions)
            )
            try:
                atomic_write_json(temporary / "state.json", _envelope(revision.to_dict()))
                self._checkpoint("revision_written", temporary)
                _fsync_directory(temporary)
                self._checkpoint("revision_before_rename", temporary)
                os.rename(temporary, destination)
                _fsync_directory(self.revisions)
                self._checkpoint("revision_committed", destination)
            except BaseException:
                if temporary.exists():
                    shutil.rmtree(temporary)
                raise

    def commit(self, generation_id: str) -> GenerationPointers:
        """Atomically advance the local committed generation."""

        with self._locked():
            self._read_revision(self.revisions / generation_id)
            current = self._load_pointers_unlocked(allow_backup=True)
            if current.committed == generation_id:
                return current
            updated = GenerationPointers(
                committed=generation_id,
                previous=current.committed,
                last_known_good=current.last_known_good or current.committed or generation_id,
            )
            self._write_pointers_unlocked(updated, current)
            self._prune_revisions_unlocked(updated)
            return updated

    def mark_last_known_good(self, generation_id: str) -> GenerationPointers:
        with self._locked():
            self._read_revision(self.revisions / generation_id)
            current = self._load_pointers_unlocked(allow_backup=True)
            if generation_id not in {current.committed, current.previous}:
                raise StateValidationError("last-known-good must be committed or previous")
            updated = GenerationPointers(current.committed, current.previous, generation_id)
            self._write_pointers_unlocked(updated, current)
            return updated

    def load_pointers(self) -> GenerationPointers:
        with self._locked():
            return self._load_pointers_unlocked(allow_backup=False)

    def recover(self) -> GenerationPointers:
        """Recover only from a previously durable pointer set; never infer a commit."""

        with self._locked():
            try:
                return self._load_pointers_unlocked(allow_backup=False)
            except CorruptStateError:
                recovered = self._read_pointer_file(self.pointer_backup_path)
                self._validate_pointer_targets(recovered)
                atomic_write_json(self.pointer_path, _envelope(recovered.to_dict()))
                return recovered

    def load_committed(self) -> GenerationRevision | None:
        with self._locked():
            pointers = self._load_pointers_unlocked(allow_backup=False)
            if pointers.committed is None:
                return None
            return self._read_revision(self.revisions / pointers.committed)

    def append_transition(self, record: TransitionRecord) -> None:
        with self._locked():
            indices = self._journal_indices()
            next_index = indices[-1] + 1 if indices else 0
            destination = self.journal / f"{next_index:020d}.json"
            temporary = self.journal / f".{next_index:020d}.tmp"
            try:
                with temporary.open("x", encoding="utf-8") as stream:
                    stream.write(json.dumps(_envelope(record.to_dict()), sort_keys=True, indent=2))
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                self._checkpoint("journal_before_rename", temporary)
                os.rename(temporary, destination)
                _fsync_directory(self.journal)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise

            stale = self._journal_indices()[: -self.journal_retention]
            for index in stale:
                (self.journal / f"{index:020d}.json").unlink()
            if stale:
                _fsync_directory(self.journal)

    def load_journal(self) -> tuple[TransitionRecord, ...]:
        with self._locked():
            records: list[TransitionRecord] = []
            for index in self._journal_indices():
                path = self.journal / f"{index:020d}.json"
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                    records.append(TransitionRecord.from_mapping(_unwrap(value)))
                except (OSError, json.JSONDecodeError, StateValidationError) as error:
                    raise CorruptStateError(
                        f"invalid journal entry {path.name}: {error}"
                    ) from error
            return tuple(records)

    def save_replay_state(self, peer_node_id: str, state: ReplayState) -> None:
        """Persist the accepted peer replay boundary atomically and monotonically."""

        path = self._replay_state_path(peer_node_id)
        with self._locked():
            current = self._load_replay_state_unlocked(path)
            if current is not None:
                if state.current_boot_id in current.retired_boot_ids:
                    raise StalePeerStateError("retired replay boot identity cannot be restored")
                if not set(current.retired_boot_ids).issubset(state.retired_boot_ids):
                    raise StalePeerStateError("replay state cannot forget retired boot identities")
                if state.current_boot_id == current.current_boot_id:
                    if state.highest_sequence <= current.highest_sequence:
                        raise StalePeerStateError("replay state sequence must advance")
                elif current.current_boot_id not in state.retired_boot_ids:
                    raise StalePeerStateError(
                        "new replay state must retire the previously accepted boot identity"
                    )
            atomic_write_json(path, _envelope(state.to_dict()))

    def load_replay_state(self, peer_node_id: str) -> ReplayState | None:
        with self._locked():
            return self._load_replay_state_unlocked(self._replay_state_path(peer_node_id))

    def _replay_state_path(self, peer_node_id: str) -> Path:
        if not _PEER_ID_RE.fullmatch(peer_node_id):
            raise StateValidationError("peer_node_id must be a stable identifier")
        return self.peers / peer_node_id / "replay.json"

    def _load_replay_state_unlocked(self, path: Path) -> ReplayState | None:
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return ReplayState.from_mapping(_unwrap(value))
        except (OSError, json.JSONDecodeError, StateValidationError) as error:
            raise CorruptStateError(
                f"invalid replay state for {path.parent.name}: {error}"
            ) from error

    def _journal_indices(self) -> list[int]:
        return sorted(
            int(path.stem)
            for path in self.journal.glob("[0-9]" * 20 + ".json")
            if path.stem.isdigit()
        )

    def _read_revision(self, directory: Path) -> GenerationRevision:
        try:
            value = json.loads((directory / "state.json").read_text(encoding="utf-8"))
            revision = GenerationRevision.from_mapping(_unwrap(value))
        except (OSError, json.JSONDecodeError, StateValidationError) as error:
            raise CorruptStateError(
                f"invalid generation revision {directory.name}: {error}"
            ) from error
        if revision.generation_id != directory.name:
            raise CorruptStateError("generation directory does not match its payload identity")
        return revision

    def _read_pointer_file(self, path: Path) -> GenerationPointers:
        if not path.exists():
            return GenerationPointers()
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return GenerationPointers.from_mapping(_unwrap(value))
        except (OSError, json.JSONDecodeError, StateValidationError) as error:
            raise CorruptStateError(
                f"invalid generation pointer file {path.name}: {error}"
            ) from error

    def _load_pointers_unlocked(self, *, allow_backup: bool) -> GenerationPointers:
        try:
            pointers = self._read_pointer_file(self.pointer_path)
            self._validate_pointer_targets(pointers)
            return pointers
        except CorruptStateError:
            if not allow_backup:
                raise
            pointers = self._read_pointer_file(self.pointer_backup_path)
            self._validate_pointer_targets(pointers)
            return pointers

    def _validate_pointer_targets(self, pointers: GenerationPointers) -> None:
        targets = {
            pointers.committed,
            pointers.previous,
            pointers.last_known_good,
        }
        for generation_id in targets:
            if generation_id is not None:
                self._read_revision(self.revisions / generation_id)

    def _write_pointers_unlocked(
        self, updated: GenerationPointers, current: GenerationPointers
    ) -> None:
        self._checkpoint("pointers_before_backup", self.pointer_backup_path)
        atomic_write_json(self.pointer_backup_path, _envelope(current.to_dict()))
        self._checkpoint("pointers_before_replace", self.pointer_path)
        atomic_write_json(self.pointer_path, _envelope(updated.to_dict()))
        self._checkpoint("pointers_replaced", self.pointer_path)

    def _prune_revisions_unlocked(self, pointers: GenerationPointers) -> None:
        protected = {pointers.committed, pointers.previous, pointers.last_known_good} - {None}
        candidates = sorted(
            (
                path
                for path in self.revisions.iterdir()
                if path.is_dir() and not path.name.startswith(".")
            ),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        keep = set(protected)
        keep.update(path.name for path in candidates[: self.revision_retention])
        for path in candidates:
            if path.name not in keep:
                shutil.rmtree(path)
        _fsync_directory(self.revisions)
