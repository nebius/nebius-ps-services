from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from nebius_cxcli.slurm_jobs import SlurmJobControlRecord
from nebius_cxcli.soperator_checks_campaign_release import CampaignChecksRelease


@pytest.fixture
def owner():
    held = SlurmJobControlRecord(
        job_id="7",
        user_id="user(1001)",
        state="PENDING",
        batch_flag=1,
        submit_time="2026-01-01T00:00:00",
        job_name="training",
        priority=0,
        reason="JobHeldUser",
    )
    state = {"present": True, "held": held, "deletes": 0}
    events = [{"action": "requeue-hold-applied", "job_control_postimages": [held.as_payload()]}]
    proof = {"owner": "campaign", "reservation": "reserve", "fingerprint": "sha256:fixed"}

    def delete():
        state["present"] = False
        state["deletes"] += 1

    owner = CampaignChecksRelease(
        owner="campaign",
        reservation="reserve",
        load_events=lambda: tuple(events),
        record_event=lambda event: events.append(copy.deepcopy(event)),
        present=lambda: state["present"],
        observe=lambda: {"fingerprint": "sha256:fixed", "users": ["root"]},
        delete=delete,
        read_job=lambda _id: state["held"],
        authority=lambda: None,
    )
    return owner, proof, state, events


def test_parent_releases_only_reservation_and_preserves_held_job(owner):
    release, proof, state, events = owner
    held = state["held"]
    release.release(proof)
    release.verify(proof)
    release.release(proof)
    assert state["deletes"] == 1
    assert state["held"] == held
    assert [e["action"] for e in events][1:] == [
        "maintenance-reservation-delete-intent",
        "maintenance-reservation-delete-applied",
    ]


def test_parent_recovers_delete_success_before_applied_receipt(owner):
    release, proof, state, events = owner
    original = release.record_event

    def record(event):
        if event["action"].endswith("applied"):
            raise KeyboardInterrupt
        original(event)

    release.record_event = record
    with pytest.raises(KeyboardInterrupt):
        release.release(proof)
    assert not state["present"]
    release.record_event = original
    release.release(proof)
    assert state["deletes"] == 1
    release.verify(proof)


@pytest.mark.parametrize(
    "change", ["foreign-owner", "missing", "reused-job", "unheld-job", "fingerprint", "users"]
)
def test_parent_refuses_unproven_or_changed_release(owner, change):
    release, proof, state, events = owner
    if change == "foreign-owner":
        proof["owner"] = "foreign"
    elif change == "missing":
        state["present"] = False
    elif change == "reused-job":
        state["held"] = replace(state["held"], submit_time="2026-01-02T00:00:00")
    elif change == "unheld-job":
        state["held"] = replace(state["held"], priority=5, reason="Resources")
    elif change == "fingerprint":
        proof["fingerprint"] = "changed"
    else:
        release.observe = lambda: {
            "fingerprint": proof["fingerprint"],
            "users": ["root", "foreign"],
        }
    with pytest.raises(RuntimeError):
        release.release(proof)
    assert state["deletes"] == 0
    assert len(events) == 1
