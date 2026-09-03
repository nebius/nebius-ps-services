from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from nebius_cxcli.soperator_recovery_journal import (
    SoperatorRecoveryIdentity,
    SoperatorRecoveryJournal,
)


@dataclass(frozen=True)
class _Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class _JournalRunner:
    def __init__(self) -> None:
        self.resource: dict[str, object] | None = None
        self.version = 0
        self.reject_next_replace = False

    def __call__(
        self,
        args,
        *,
        timeout_seconds: int,
        check: bool,
        input_text: str | None = None,
    ) -> _Result:
        del timeout_seconds, check
        command = list(args)
        verb = command[command.index("kube-system") + 1]
        if verb == "get":
            if self.resource is None:
                return _Result(1, stderr="NotFound")
            return _Result(0, stdout=json.dumps(self.resource))
        if verb == "create":
            if self.resource is not None:
                return _Result(1, stderr="AlreadyExists")
            self._store(input_text)
            return _Result(0)
        if verb == "replace":
            if self.reject_next_replace:
                self.reject_next_replace = False
                return _Result(1, stderr="Conflict")
            incoming = json.loads(input_text or "{}")
            incoming_version = str(incoming.get("metadata", {}).get("resourceVersion") or "")
            current_version = (
                str(self.resource.get("metadata", {}).get("resourceVersion") or "")
                if self.resource is not None
                else ""
            )
            if not current_version or incoming_version != current_version:
                return _Result(1, stderr="Conflict")
            self._store(input_text)
            return _Result(0)
        raise AssertionError(command)

    def _store(self, input_text: str | None) -> None:
        payload = json.loads(input_text or "{}")
        self.version += 1
        payload.setdefault("metadata", {})["resourceVersion"] = str(self.version)
        self.resource = payload


def _identity(*, image_token: str = "a") -> SoperatorRecoveryIdentity:
    return SoperatorRecoveryIdentity(
        operation_id="sha256:" + "1" * 64,
        cluster_id="mk8s-a",
        kubernetes_uid="kube-a",
        lease_uid="lease-a",
        fencing_epoch=3,
        source_release="3.0.4",
        target_release="4.1.7",
        target_jail_image="registry.example.invalid/jail@sha256:" + image_token * 64,
        infrastructure_receipt_sha256="sha256:" + "b" * 64,
        rootfs_classification_sha256="sha256:" + "c" * 64,
    )


def _journal(runner: _JournalRunner, path: Path, **identity: str) -> SoperatorRecoveryJournal:
    return SoperatorRecoveryJournal(
        runner=runner,
        kube_context="ctx",
        identity=_identity(**identity),
        local_path=path,
        assert_authority=lambda: object(),
    )


def _complete_safe_replay_prefix(journal: SoperatorRecoveryJournal) -> None:
    journal.establish()
    journal.begin_stage(name="rootfs-admission-decision", intent={"mode": "target-wins"})
    journal.complete_stage(
        name="rootfs-admission-decision",
        evidence={"mode": "target-wins"},
    )
    journal.begin_stage(
        name="rootfs-passive-target-identity",
        intent={"pvcName": "jail-rootfs-b", "targetSlot": "B"},
    )
    journal.complete_stage(
        name="rootfs-passive-target-identity",
        evidence={"pvcUid": "pvc-uid-b"},
    )


def test_recovery_journal_writes_intent_before_completion_and_seals(tmp_path: Path) -> None:
    runner = _JournalRunner()
    journal = _journal(runner, tmp_path / "journal.json")

    journal.establish()
    journal.begin_stage(name="rootfs-populate", intent={"image": _identity().target_jail_image})
    stage = journal.complete_stage(
        name="rootfs-populate",
        evidence={"jobUid": "job-a"},
    )
    sealed = journal.seal()

    assert stage["status"] == "complete"
    assert sealed["status"] == "complete"
    assert (tmp_path / "journal.json").stat().st_mode & 0o777 == 0o600


def test_recovery_journal_rejects_changed_stage_intent(tmp_path: Path) -> None:
    runner = _JournalRunner()
    journal = _journal(runner, tmp_path / "journal.json")
    journal.establish()
    journal.begin_stage(name="rootfs-populate", intent={"slot": "slot-b"})

    with pytest.raises(RuntimeError, match="intent changed"):
        journal.begin_stage(name="rootfs-populate", intent={"slot": "slot-a"})


def test_recovery_journal_rejects_foreign_target_image_on_resume(tmp_path: Path) -> None:
    runner = _JournalRunner()
    _journal(runner, tmp_path / "first.json").establish()

    with pytest.raises(RuntimeError, match="recovery identity changed"):
        _journal(runner, tmp_path / "second.json", image_token="b").establish()


def test_recovery_journal_fails_closed_when_cas_is_lost(tmp_path: Path) -> None:
    runner = _JournalRunner()
    journal = _journal(runner, tmp_path / "journal.json")
    journal.establish()
    runner.reject_next_replace = True

    with pytest.raises(RuntimeError, match="CAS authority"):
        journal.begin_stage(name="rootfs-populate", intent={"slot": "slot-b"})


def test_recovery_journal_writes_ahead_and_seals_safe_replay_supersession(
    tmp_path: Path,
) -> None:
    runner = _JournalRunner()
    journal = _journal(runner, tmp_path / "journal.json")
    _complete_safe_replay_prefix(journal)

    intent_payload = journal.begin_safe_replay_supersession(
        predecessor_operation_id="sha256:" + "d" * 64,
        discarded_replay_receipt_sha256="sha256:" + "e" * 64,
        workload_name="inventory-job-a",
        workload_uid="job-uid-a",
        workload_resource_version="123",
        workload_sha256="sha256:" + "f" * 64,
        pod_identity_sha256="sha256:" + "0" * 64,
    )
    sealed = journal.supersede_safe_replay()

    assert intent_payload["status"] == "active"
    assert intent_payload["supersededReplayIntent"]["workloadUid"] == "job-uid-a"
    assert sealed["status"] == "superseded-safe-replay"
    assert sealed["supersededReplay"] == sealed["supersededReplayIntent"]


def test_recovery_journal_rejects_changed_safe_replay_supersession_intent(
    tmp_path: Path,
) -> None:
    runner = _JournalRunner()
    journal = _journal(runner, tmp_path / "journal.json")
    _complete_safe_replay_prefix(journal)
    evidence = {
        "predecessor_operation_id": "sha256:" + "d" * 64,
        "discarded_replay_receipt_sha256": "sha256:" + "e" * 64,
        "workload_name": "inventory-job-a",
        "workload_uid": "job-uid-a",
        "workload_resource_version": "123",
        "workload_sha256": "sha256:" + "f" * 64,
        "pod_identity_sha256": "sha256:" + "0" * 64,
    }
    journal.begin_safe_replay_supersession(**evidence)

    with pytest.raises(RuntimeError, match="intent changed"):
        journal.begin_safe_replay_supersession(**{**evidence, "workload_uid": "job-uid-b"})


def test_recovery_journal_rejects_supersession_after_inventory_boundary(
    tmp_path: Path,
) -> None:
    runner = _JournalRunner()
    journal = _journal(runner, tmp_path / "journal.json")
    _complete_safe_replay_prefix(journal)
    journal.begin_stage(name="rootfs-passive-target-preflight", intent={"job": "job-a"})
    journal.complete_stage(
        name="rootfs-passive-target-preflight",
        evidence={"jobUid": "job-uid-a"},
    )

    with pytest.raises(RuntimeError, match="safe inventory boundary"):
        journal.begin_safe_replay_supersession(
            predecessor_operation_id="sha256:" + "d" * 64,
            discarded_replay_receipt_sha256="sha256:" + "e" * 64,
            workload_name="inventory-job-a",
            workload_uid="job-uid-a",
            workload_resource_version="123",
            workload_sha256="sha256:" + "f" * 64,
            pod_identity_sha256="sha256:" + "0" * 64,
        )
