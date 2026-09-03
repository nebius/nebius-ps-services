from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from nebius_vpngw.agent.vm_ha import (
    AtomicGenerationStore,
    CorruptStateError,
    DigestSet,
    GenerationRevision,
    PeerHeartbeat,
    PeerReplayGuard,
    ReplayState,
    StalePeerStateError,
    StateValidationError,
    TransitionRecord,
)


def _digests(marker: str) -> DigestSet:
    return DigestSet(marker * 64, "b" * 64, "c" * 64)


def _revision(marker: str) -> GenerationRevision:
    return GenerationRevision(
        cluster_id="cluster-a",
        node_id="node-a",
        generation_id=marker * 64,
        digests=_digests(marker),
        committed_at="2026-08-12T00:00:00Z",
    )


def _heartbeat(*, boot_id: str = "boot-a", sequence: int = 0) -> PeerHeartbeat:
    return PeerHeartbeat(
        cluster_id="cluster-a",
        node_id="node-b",
        boot_id=boot_id,
        sequence=sequence,
        sent_at="2026-08-12T00:00:00Z",
        configured_role="passive",
        observed_owner_id="node-a",
        generation_id="a" * 64,
        mtls_epoch=1,
        certificate_fingerprint="d" * 64,
        digests=_digests("a"),
        service_healthy=True,
        route_ready=True,
        promotion_ready=False,
        auto_healing_policy_state="enabled",
        auto_healing_policy_digest="e" * 64,
    )


def test_generation_store_recovers_committed_previous_and_last_known_good(tmp_path) -> None:
    store = AtomicGenerationStore(tmp_path / "ha")
    first = _revision("a")
    second = _revision("d")
    store.stage(first)
    store.commit(first.generation_id)
    store.stage(second)
    pointers = store.commit(second.generation_id)

    assert pointers.committed == second.generation_id
    assert pointers.previous == first.generation_id
    assert pointers.last_known_good == first.generation_id
    assert AtomicGenerationStore(tmp_path / "ha").load_committed() == second

    updated = store.mark_last_known_good(second.generation_id)
    assert updated.last_known_good == second.generation_id


def test_generation_store_never_infers_commit_from_staged_revision(tmp_path) -> None:
    root = tmp_path / "ha"
    store = AtomicGenerationStore(root)
    store.stage(_revision("a"))

    assert store.pointer_path.exists()
    assert store.pointer_backup_path.exists()
    assert AtomicGenerationStore(root).load_committed() is None


def test_generation_store_initializes_precreated_empty_root_and_commits(tmp_path) -> None:
    root = tmp_path / "ha"
    root.mkdir()
    store = AtomicGenerationStore(root)

    assert store.load_committed() is None

    restarted = AtomicGenerationStore(root)
    revision = _revision("a")
    restarted.stage(revision)
    restarted.commit(revision.generation_id)

    assert AtomicGenerationStore(root).load_committed() == revision


def test_generation_store_rejects_loss_of_all_pointer_authority(tmp_path) -> None:
    root = tmp_path / "ha"
    store = AtomicGenerationStore(root)
    first = _revision("a")
    second = _revision("d")
    store.stage(first)
    store.commit(first.generation_id)
    store.pointer_path.unlink()
    store.pointer_backup_path.unlink()

    restarted = AtomicGenerationStore(root)
    with pytest.raises(CorruptStateError, match="pointers.json"):
        restarted.load_committed()
    with pytest.raises(CorruptStateError, match="pointers.backup.json"):
        restarted.recover()

    restarted.stage(second)
    with pytest.raises(CorruptStateError, match="pointers.backup.json"):
        restarted.commit(second.generation_id)


def test_generation_store_recovers_last_durable_pointer_set_after_corruption(tmp_path) -> None:
    root = tmp_path / "ha"
    store = AtomicGenerationStore(root)
    first = _revision("a")
    second = _revision("d")
    store.stage(first)
    store.commit(first.generation_id)
    store.stage(second)
    store.commit(second.generation_id)
    store.pointer_path.write_text("{truncated", encoding="utf-8")

    recovered = AtomicGenerationStore(root).recover()

    assert recovered.committed == first.generation_id
    assert AtomicGenerationStore(root).load_committed() == first


def test_generation_store_rejects_corrupt_primary_without_backup(tmp_path) -> None:
    root = tmp_path / "ha"
    store = AtomicGenerationStore(root)
    first = _revision("a")
    second = _revision("d")
    store.stage(first)
    store.commit(first.generation_id)
    store.pointer_path.write_text("{truncated", encoding="utf-8")
    store.pointer_backup_path.unlink()

    restarted = AtomicGenerationStore(root)
    with pytest.raises(CorruptStateError, match="pointers.backup.json"):
        restarted.recover()

    assert restarted.pointer_path.read_text(encoding="utf-8") == "{truncated"
    assert not restarted.pointer_backup_path.exists()
    assert (restarted.revisions / first.generation_id).is_dir()

    restarted.stage(second)
    with pytest.raises(CorruptStateError, match="pointers.backup.json"):
        restarted.commit(second.generation_id)

    assert restarted.pointer_path.read_text(encoding="utf-8") == "{truncated"
    assert not restarted.pointer_backup_path.exists()
    assert (restarted.revisions / first.generation_id).is_dir()
    assert (restarted.revisions / second.generation_id).is_dir()


def test_generation_store_recovers_missing_primary_from_valid_backup(tmp_path) -> None:
    root = tmp_path / "ha"
    store = AtomicGenerationStore(root)
    first = _revision("a")
    second = _revision("d")
    store.stage(first)
    store.commit(first.generation_id)
    store.stage(second)
    store.commit(second.generation_id)
    store.pointer_path.unlink()

    restarted = AtomicGenerationStore(root)
    with pytest.raises(CorruptStateError, match="pointers.json"):
        restarted.load_pointers()

    recovered = restarted.recover()

    assert recovered.committed == first.generation_id
    assert AtomicGenerationStore(root).load_committed() == first


@pytest.mark.parametrize(
    ("checkpoint", "expected_committed"),
    [
        ("pointers_before_replace", "a" * 64),
        ("pointers_replaced", "d" * 64),
    ],
)
def test_pointer_faults_recover_to_whole_old_or_new_state(
    tmp_path, checkpoint: str, expected_committed: str
) -> None:
    root = tmp_path / "ha"
    initial = AtomicGenerationStore(root)
    first = _revision("a")
    second = _revision("d")
    initial.stage(first)
    initial.commit(first.generation_id)
    initial.stage(second)

    def fail(name, path) -> None:
        if name == checkpoint:
            raise OSError(f"injected {name}")

    failing = AtomicGenerationStore(root, fault_hook=fail)
    with pytest.raises(OSError, match=f"injected {checkpoint}"):
        failing.commit(second.generation_id)

    restarted = AtomicGenerationStore(root)
    assert restarted.load_pointers().committed == expected_committed
    assert restarted.load_committed().generation_id == expected_committed


def test_interrupted_revision_stage_leaves_no_visible_or_temporary_revision(tmp_path) -> None:
    root = tmp_path / "ha"

    def fail(name, path) -> None:
        if name == "revision_before_rename":
            raise OSError("injected revision failure")

    store = AtomicGenerationStore(root, fault_hook=fail)
    revision = _revision("a")
    with pytest.raises(OSError, match="injected revision failure"):
        store.stage(revision)

    assert not (store.revisions / revision.generation_id).exists()
    assert list(store.revisions.iterdir()) == []


def test_generation_checksum_corruption_is_rejected(tmp_path) -> None:
    store = AtomicGenerationStore(tmp_path / "ha")
    revision = _revision("a")
    store.stage(revision)
    store.commit(revision.generation_id)
    state_path = store.revisions / revision.generation_id / "state.json"
    state_path.write_text(
        state_path.read_text().replace("cluster-a", "cluster-b"), encoding="utf-8"
    )

    with pytest.raises(CorruptStateError, match="checksum mismatch"):
        store.load_committed()


def test_transition_journal_is_bounded_and_rejects_malformed_entries(tmp_path) -> None:
    store = AtomicGenerationStore(tmp_path / "ha", journal_retention=2)
    for sequence in range(3):
        store.append_transition(
            TransitionRecord(
                boot_id="boot-a",
                sequence=sequence,
                transition="stage-generation",
                generation_id="a" * 64,
                recorded_at="2026-08-12T00:00:00Z",
                outcome="complete",
            )
        )

    assert [record.sequence for record in store.load_journal()] == [1, 2]
    newest = sorted(store.journal.glob("*.json"))[-1]
    newest.write_text("{}", encoding="utf-8")
    with pytest.raises(CorruptStateError, match="invalid journal entry"):
        store.load_journal()


def test_generation_and_peer_models_reject_noncanonical_or_extra_state() -> None:
    with pytest.raises(StateValidationError, match="generation_id"):
        replace(_revision("a"), generation_id="d" * 64)

    payload = _heartbeat().to_dict()
    payload["claims_cloud_ownership"] = True
    with pytest.raises(StateValidationError, match="keys must be exactly"):
        PeerHeartbeat.from_mapping(payload)


def test_replay_guard_rejects_sequences_and_retired_boot_id_across_restart() -> None:
    guard = PeerReplayGuard()
    guard.accept(
        _heartbeat(sequence=3),
        authenticated_node_id="node-b",
        authenticated_certificate_fingerprint="d" * 64,
        authenticated_mtls_epoch=1,
        expected_cluster_id="cluster-a",
        expected_node_id="node-b",
    )
    with pytest.raises(StalePeerStateError, match="stale or replayed"):
        guard.accept(
            _heartbeat(sequence=3),
            authenticated_node_id="node-b",
            authenticated_certificate_fingerprint="d" * 64,
            authenticated_mtls_epoch=1,
            expected_cluster_id="cluster-a",
            expected_node_id="node-b",
        )

    next_state = guard.accept(
        _heartbeat(boot_id="boot-b", sequence=0),
        authenticated_node_id="node-b",
        authenticated_certificate_fingerprint="d" * 64,
        authenticated_mtls_epoch=1,
        expected_cluster_id="cluster-a",
        expected_node_id="node-b",
    )
    restarted = PeerReplayGuard(ReplayState.from_mapping(next_state.to_dict()))
    with pytest.raises(StalePeerStateError, match="retired boot"):
        restarted.accept(
            _heartbeat(boot_id="boot-a", sequence=4),
            authenticated_node_id="node-b",
            authenticated_certificate_fingerprint="d" * 64,
            authenticated_mtls_epoch=1,
            expected_cluster_id="cluster-a",
            expected_node_id="node-b",
        )


def test_replay_guard_requires_authenticated_identity_and_tracks_first_new_boot_sequence() -> None:
    guard = PeerReplayGuard()
    with pytest.raises(StateValidationError, match="authenticated peer"):
        guard.accept(
            _heartbeat(),
            authenticated_node_id="node-c",
            authenticated_certificate_fingerprint="d" * 64,
            authenticated_mtls_epoch=1,
            expected_cluster_id="cluster-a",
            expected_node_id="node-b",
        )
    guard.accept(
        _heartbeat(sequence=2),
        authenticated_node_id="node-b",
        authenticated_certificate_fingerprint="d" * 64,
        authenticated_mtls_epoch=1,
        expected_cluster_id="cluster-a",
        expected_node_id="node-b",
    )
    next_state = guard.accept(
        _heartbeat(boot_id="boot-b", sequence=7),
        authenticated_node_id="node-b",
        authenticated_certificate_fingerprint="d" * 64,
        authenticated_mtls_epoch=1,
        expected_cluster_id="cluster-a",
        expected_node_id="node-b",
    )
    assert next_state == ReplayState("boot-b", 7, ("boot-a",))
    with pytest.raises(StalePeerStateError, match="stale or replayed"):
        guard.accept(
            _heartbeat(boot_id="boot-b", sequence=7),
            authenticated_node_id="node-b",
            authenticated_certificate_fingerprint="d" * 64,
            authenticated_mtls_epoch=1,
            expected_cluster_id="cluster-a",
            expected_node_id="node-b",
        )


def test_replay_boundary_persists_monotonically_across_store_restart(tmp_path) -> None:
    root = tmp_path / "ha"
    store = AtomicGenerationStore(root)
    first = ReplayState("boot-a", 4)
    next_boot = ReplayState("boot-b", 0, ("boot-a",))
    store.save_replay_state("node-b", first)

    restarted = AtomicGenerationStore(root)
    assert restarted.load_replay_state("node-b") == first
    with pytest.raises(StalePeerStateError, match="must advance"):
        restarted.save_replay_state("node-b", ReplayState("boot-a", 3))
    with pytest.raises(StalePeerStateError, match="must retire"):
        restarted.save_replay_state("node-b", ReplayState("boot-b", 0))

    restarted.save_replay_state("node-b", next_boot)
    assert AtomicGenerationStore(root).load_replay_state("node-b") == next_boot
    with pytest.raises(StalePeerStateError, match="cannot be restored"):
        restarted.save_replay_state("node-b", ReplayState("boot-a", 5, ("boot-b",)))


def test_last_accepted_peer_heartbeat_survives_controller_restart(tmp_path) -> None:
    root = tmp_path / "ha"
    heartbeat = _heartbeat(sequence=7)
    store = AtomicGenerationStore(root)

    with pytest.raises(StalePeerStateError, match="replay boundary"):
        store.save_accepted_peer_heartbeat("node-b", heartbeat)

    store.save_replay_state("node-b", ReplayState("boot-a", 7))
    store.save_accepted_peer_heartbeat("node-b", heartbeat)

    assert AtomicGenerationStore(root).load_accepted_peer_heartbeat("node-b") == heartbeat


def test_last_accepted_peer_heartbeat_fails_closed_when_replay_lineage_advances(
    tmp_path,
) -> None:
    root = tmp_path / "ha"
    store = AtomicGenerationStore(root)
    heartbeat = _heartbeat(sequence=7)
    store.save_replay_state("node-b", ReplayState("boot-a", 7))
    store.save_accepted_peer_heartbeat("node-b", heartbeat)
    store.save_replay_state("node-b", ReplayState("boot-b", 0, ("boot-a",)))

    assert AtomicGenerationStore(root).load_accepted_peer_heartbeat("node-b") is None


def test_last_accepted_peer_heartbeat_fails_closed_when_same_boot_replay_advances(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ha"
    store = AtomicGenerationStore(root)
    heartbeat = _heartbeat(sequence=7)
    store.save_replay_state("node-b", ReplayState("boot-a", 7))
    store.save_accepted_peer_heartbeat("node-b", heartbeat)

    # PeerStateExchange persists the replay boundary before BoundPeerRuntime
    # persists the accepted heartbeat. A crash in that window must not let the
    # older heartbeat become parity evidence after restart.
    store.save_replay_state("node-b", ReplayState("boot-a", 8))

    assert AtomicGenerationStore(root).load_accepted_peer_heartbeat("node-b") is None
    with pytest.raises(StalePeerStateError, match="replay boundary"):
        store.save_accepted_peer_heartbeat("node-b", heartbeat)


def test_corrupt_accepted_peer_heartbeat_still_fails_hard(tmp_path: Path) -> None:
    store = AtomicGenerationStore(tmp_path / "ha")
    store.save_replay_state("node-b", ReplayState("boot-a", 1))
    heartbeat_path = store.peers / "node-b" / "accepted-heartbeat.json"
    heartbeat_path.write_text("{truncated", encoding="utf-8")

    with pytest.raises(CorruptStateError, match="invalid accepted heartbeat"):
        AtomicGenerationStore(store.root).load_accepted_peer_heartbeat("node-b")


def test_replay_boundary_rejects_malformed_state_and_unsafe_peer_path(tmp_path) -> None:
    store = AtomicGenerationStore(tmp_path / "ha")
    with pytest.raises(StateValidationError, match="stable identifier"):
        store.load_replay_state("../node-b")

    store.save_replay_state("node-b", ReplayState("boot-a", 1))
    replay_path = store.peers / "node-b" / "replay.json"
    replay_path.write_text("{truncated", encoding="utf-8")
    with pytest.raises(CorruptStateError, match="invalid replay state"):
        AtomicGenerationStore(store.root).load_replay_state("node-b")


def test_replay_boundary_never_forgets_older_retired_boot_id(tmp_path) -> None:
    store = AtomicGenerationStore(tmp_path / "ha")
    store.save_replay_state("node-b", ReplayState("boot-b", 0, ("boot-a",)))

    with pytest.raises(StalePeerStateError, match="cannot forget"):
        store.save_replay_state("node-b", ReplayState("boot-c", 0, ("boot-b",)))


def test_replay_guard_never_forgets_retired_boot_id() -> None:
    guard = PeerReplayGuard()
    for index in range(20):
        guard.accept(
            _heartbeat(boot_id=f"boot-{index}", sequence=0),
            authenticated_node_id="node-b",
            authenticated_certificate_fingerprint="d" * 64,
            authenticated_mtls_epoch=1,
            expected_cluster_id="cluster-a",
            expected_node_id="node-b",
        )

    with pytest.raises(StalePeerStateError, match="retired boot"):
        guard.accept(
            _heartbeat(boot_id="boot-0", sequence=0),
            authenticated_node_id="node-b",
            authenticated_certificate_fingerprint="d" * 64,
            authenticated_mtls_epoch=1,
            expected_cluster_id="cluster-a",
            expected_node_id="node-b",
        )
