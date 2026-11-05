from dataclasses import replace

import pytest

from nebius_vpngw.agent.vm_ha.evidence import PeerEvidenceTracker
from nebius_vpngw.agent.vm_ha.models import DigestSet, HeartbeatEvidence, PeerHeartbeat


def heartbeat(node="b", **changes):
    value = PeerHeartbeat(
        cluster_id="cluster",
        node_id=node,
        boot_id="boot",
        sequence=1,
        sent_at="2026-11-02T00:00:00Z",
        configured_role="passive",
        observed_owner_id="a",
        generation_id="a" * 64,
        mtls_epoch=1,
        certificate_fingerprint="d" * 64,
        digests=DigestSet("a" * 64, "b" * 64, "c" * 64),
        service_healthy=True,
        route_ready=True,
        promotion_ready=True,
        auto_healing_policy_state="enabled",
        auto_healing_policy_digest="e" * 64,
        evidence=HeartbeatEvidence(state="healthy", process_id="f" * 32, sample_id=1),
    )
    return replace(value, **changes)


def tracker(now):
    return PeerEvidenceTracker(
        local_node_id="a", peer_node_id="b", generation_id="a" * 64, clock=lambda: now[0]
    )


def test_positive_health_requires_round_trip_and_expires_without_wall_clock_assumption():
    now = [100.0]
    proof = tracker(now)
    peer = heartbeat()
    assert not proof.accept(peer).local_health_fresh(now[0])
    challenge = proof.issue(heartbeat("a")).evidence.challenge
    now[0] += 3
    peer = replace(
        peer, evidence=replace(peer.evidence, sample_id=2, echo=challenge, age_seconds=2)
    )
    received = proof.accept(peer)
    assert received.local_health_fresh(117.99)
    assert not received.local_health_fresh(118)
    # Proof is process-local, never restored from the wire or persisted cache.
    assert not PeerHeartbeat.from_mapping(received.to_dict()).local_health_fresh(104)


def test_same_sample_new_challenge_never_renews_expiry_and_unknown_supersedes():
    now = [100.0]
    proof = tracker(now)
    first = proof.issue(heartbeat("a")).evidence.challenge
    now[0] += 1
    peer = heartbeat(
        evidence=HeartbeatEvidence(state="healthy", process_id="f" * 32, sample_id=1, echo=first)
    )
    expiry = proof.accept(peer).health_valid_until
    now[0] += 3
    second = proof.issue(heartbeat("a")).evidence.challenge
    now[0] += 1
    repeated = proof.accept(replace(peer, evidence=replace(peer.evidence, echo=second)))
    assert repeated.health_valid_until == expiry
    unknown = replace(peer, evidence=replace(peer.evidence, sample_id=2, state="unknown"))
    assert not proof.accept(unknown).local_health_fresh(now[0])
    assert not proof.accept(peer).local_health_fresh(now[0])


@pytest.mark.parametrize("elapsed", [10, 20])
def test_expired_echo_cannot_prove_health(elapsed):
    now = [100.0]
    proof = tracker(now)
    nonce = proof.issue(heartbeat("a")).evidence.challenge
    now[0] += elapsed
    assert not proof.accept(
        heartbeat(
            evidence=HeartbeatEvidence(
                state="healthy", process_id="f" * 32, sample_id=1, echo=nonce
            )
        )
    ).local_health_fresh(now[0])


def test_restart_without_reboot_discards_challenge_history():
    now = [100.0]
    nonce = tracker(now).issue(heartbeat("a")).evidence.challenge
    restarted = tracker(now)
    assert not restarted.accept(
        heartbeat(
            evidence=HeartbeatEvidence(
                state="healthy", process_id="f" * 32, sample_id=1, echo=nonce
            )
        )
    ).local_health_fresh(now[0])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("process_id", None),
        ("challenge", None),
        ("state", []),
        ("cloud_status", {}),
        ("controller_phase", []),
        ("age_seconds", None),
        ("duration_seconds", None),
        ("age_seconds", float("nan")),
        ("cloud_age_seconds", float("inf")),
        ("serving", 1),
        ("sample_id", True),
    ],
)
def test_evidence_rejects_malformed_required_types(field, value):
    from nebius_vpngw.agent.vm_ha.models import StateValidationError

    with pytest.raises(StateValidationError):
        HeartbeatEvidence.from_mapping({**HeartbeatEvidence().to_dict(), field: value})


def test_challenge_ring_eviction_generation_and_reordering_fail_closed():
    now = [100.0]
    proof = tracker(now)
    evicted = proof.issue(heartbeat("a")).evidence.challenge
    for _ in range(16):
        latest = proof.issue(heartbeat("a")).evidence.challenge
    old = heartbeat(evidence=HeartbeatEvidence(state="healthy", sample_id=1, echo=evicted))
    assert not proof.accept(old).local_health_fresh(now[0])
    current = replace(old, evidence=replace(old.evidence, sample_id=2, echo=latest))
    assert proof.accept(current).local_health_fresh(now[0])
    assert not proof.accept(old).local_health_fresh(now[0])
    foreign = replace(
        current,
        generation_id="b" * 64,
        digests=replace(current.digests, configuration="b" * 64),
        evidence=replace(current.evidence, sample_id=3),
    )
    assert not proof.accept(foreign).local_health_fresh(now[0])
    now[0] += 10
    assert not proof.accept(
        replace(current, evidence=replace(current.evidence, sample_id=4))
    ).local_health_fresh(now[0])


def test_wire_v4_is_rejected_and_v5_roundtrips():
    from nebius_vpngw.agent.vm_ha.models import StateValidationError

    value = heartbeat().to_dict()
    assert PeerHeartbeat.from_mapping(value) == heartbeat()
    value["schema"] = "nebius-vpngw/vm-ha-heartbeat-v4"
    with pytest.raises(StateValidationError):
        PeerHeartbeat.from_mapping(value)


@pytest.mark.parametrize("path_state", ["unknown", "unhealthy"])
def test_service_only_sample_has_causal_expiry_without_path_authority(path_state):
    now = [100.0]
    proof = tracker(now)
    nonce = proof.issue(heartbeat("a")).evidence.challenge
    now[0] = 103.0
    peer = heartbeat(
        route_ready=False,
        promotion_ready=False,
        evidence=HeartbeatEvidence(
            state=path_state,
            process_id="f" * 32,
            sample_id=1,
            echo=nonce,
            age_seconds=2,
        ),
    )
    received = proof.accept(peer)
    assert received.local_service_fresh(117.99)
    assert not received.local_service_fresh(118)
    assert not received.local_health_fresh(104)
    assert not PeerHeartbeat.from_mapping(received.to_dict()).local_service_fresh(104)
    issued = proof.issue(replace(received, node_id="a"))
    assert issued.service_valid_until is None and issued.health_valid_until is None


@pytest.mark.parametrize(
    "defect",
    ["sample-zero", "sample-old", "missing-echo", "expired-echo", "wrong-generation", "closed"],
)
def test_service_only_evidence_rejects_missing_or_stale_causal_proof(defect):
    now = [100.0]
    proof = tracker(now)
    nonce = proof.issue(heartbeat("a")).evidence.challenge
    peer = heartbeat(
        route_ready=False,
        promotion_ready=False,
        evidence=HeartbeatEvidence(
            state="unknown",
            process_id="f" * 32,
            sample_id=1,
            echo=nonce,
        ),
        service_valid_until=1000.0,
    )
    if defect == "sample-zero":
        peer = replace(peer, evidence=replace(peer.evidence, sample_id=0))
    if defect == "sample-old":
        peer = replace(peer, evidence=replace(peer.evidence, age_seconds=5))
    if defect == "missing-echo":
        peer = replace(peer, evidence=replace(peer.evidence, echo=None))
    if defect == "expired-echo":
        now[0] += 10
    if defect == "wrong-generation":
        peer = replace(
            peer, generation_id="b" * 64, digests=replace(peer.digests, configuration="b" * 64)
        )
    if defect == "closed":
        proof.close()
    assert not proof.accept(peer).local_service_fresh(now[0])


def test_service_sample_cannot_renew_resurrect_or_restore_across_processes():
    now = [100.0]
    proof = tracker(now)
    nonce = proof.issue(heartbeat("a")).evidence.challenge
    peer = heartbeat(
        evidence=HeartbeatEvidence(state="unknown", process_id="f" * 32, sample_id=1, echo=nonce)
    )
    first = proof.accept(peer)
    now[0] += 3
    next_nonce = proof.issue(heartbeat("a")).evidence.challenge
    peer = replace(peer, evidence=replace(peer.evidence, echo=next_nonce, age_seconds=3))
    assert proof.accept(peer).service_valid_until == first.service_valid_until
    assert not proof.accept(replace(peer, service_healthy=False)).local_service_fresh(now[0])
    assert not proof.accept(peer).local_service_fresh(now[0])
    fresh = replace(peer, evidence=replace(peer.evidence, sample_id=2, age_seconds=0))
    assert proof.accept(fresh).local_service_fresh(now[0])
    older = replace(
        peer, service_valid_until=1000.0, health_valid_until=1000.0, echo_valid_until=1000.0
    )
    rejected = proof.accept(older)
    assert (
        rejected.service_valid_until is None
        and rejected.health_valid_until is None
        and rejected.echo_valid_until is None
    )
    assert not tracker(now).accept(fresh).local_service_fresh(now[0])
