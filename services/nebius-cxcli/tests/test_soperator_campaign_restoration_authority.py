"""Final policy reconciliation retains the campaign's existing authority."""

from dataclasses import asdict, replace

import pytest

from nebius_cxcli.soperator_failures import SoperatorSafetyPauseError
from nebius_cxcli.soperator_full_stack_upgrade import (
    CampaignMainWorkloadAuthority,
    CampaignSegmentResult,
    _new_receipt,
    _sha256,
    _write_receipt,
    load_campaign_receipt,
    run_campaign,
)
from test_soperator_flux_sources import _main_identity
from test_soperator_full_stack_upgrade import _intent


def _restoring(tmp_path):
    intent, path = _intent(), tmp_path / "campaign.json"
    _write_receipt(path, replace(_new_receipt(intent), maintenance="active"))
    authority = CampaignMainWorkloadAuthority(path, intent, lambda: None)
    authority.freeze(_main_identity())
    receipt = load_campaign_receipt(path)
    assert receipt is not None
    _write_receipt(
        path,
        replace(
            receipt,
            maintenance="restoring",
            segments=tuple(
                replace(segment, status="complete", evidence_sha256=_sha256({}))
                for segment in receipt.segments
            ),
        ),
    )
    return intent, path, authority


def test_main_authority_can_refine_during_final_restoration(tmp_path):
    intent, path, authority = _restoring(tmp_path)
    identity = replace(_main_identity(), generation=4, observed_generation=4)
    assert authority.freeze(identity) == identity
    before = path.read_bytes()
    assert CampaignMainWorkloadAuthority(path, intent, lambda: None).freeze(identity) == identity
    assert path.read_bytes() == before
    with pytest.raises(SoperatorSafetyPauseError, match="authority changed"):
        authority.freeze(_main_identity())
    assert path.read_bytes() == before


@pytest.mark.parametrize("invalid", ["missing-binding", "unfinished", "missing-segment"])
def test_restoration_cannot_establish_unproved_workload_authority(tmp_path, invalid):
    _, path, authority = _restoring(tmp_path)
    receipt = load_campaign_receipt(path)
    assert receipt is not None
    if invalid == "missing-binding":
        receipt = replace(receipt, maintenance_evidence={})
    elif invalid == "unfinished":
        receipt = replace(
            receipt,
            segments=(replace(receipt.segments[0], status="running"), *receipt.segments[1:]),
        )
    else:
        receipt = replace(receipt, segments=receipt.segments[1:])
    _write_receipt(path, receipt)
    before = path.read_bytes()
    with pytest.raises(SoperatorSafetyPauseError, match="authority is unavailable"):
        authority.freeze(_main_identity())
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "change", [{"uid": "replacement"}, {"source_revision": "sha256:" + "f" * 64}]
)
def test_restoration_rejects_workload_substitution(tmp_path, change):
    _, path, authority = _restoring(tmp_path)
    before = path.read_bytes()
    with pytest.raises(SoperatorSafetyPauseError, match="authority changed"):
        authority.freeze(replace(_main_identity(), generation=4, observed_generation=4, **change))
    assert path.read_bytes() == before


def test_restoration_rejects_corrupt_binding_and_lost_write_fence(tmp_path):
    intent, path, authority = _restoring(tmp_path)
    receipt = load_campaign_receipt(path)
    assert receipt is not None
    binding = dict(receipt.maintenance_evidence["checksMainWorkloadAuthority"])
    binding["sha256"] = "sha256:" + "0" * 64
    _write_receipt(
        path, replace(receipt, maintenance_evidence={"checksMainWorkloadAuthority": binding})
    )
    before = path.read_bytes()
    with pytest.raises(SoperatorSafetyPauseError, match="authority is invalid"):
        authority.freeze(_main_identity())
    assert path.read_bytes() == before
    _write_receipt(path, receipt)
    before = path.read_bytes()
    fences = 0

    def fence():
        nonlocal fences
        fences += 1
        if fences == 2:
            raise SoperatorSafetyPauseError("lease lost")

    with pytest.raises(SoperatorSafetyPauseError, match="lease lost"):
        CampaignMainWorkloadAuthority(path, intent, fence).freeze(
            replace(_main_identity(), generation=4, observed_generation=4)
        )
    assert path.read_bytes() == before


@pytest.mark.parametrize("write_before_event", [False, True])
def test_restoration_retains_latest_callback_evidence(tmp_path, write_before_event):
    intent, path = _intent(), tmp_path / "campaign.json"
    identity = replace(_main_identity(), generation=4, observed_generation=4)
    binding = {"identity": asdict(identity), "sha256": _sha256(asdict(identity))}

    def restore(record, _evidence):
        if not write_before_event:
            record({"action": "restored", "workers": ("worker-0", "worker-1")})
        latest = load_campaign_receipt(path)
        assert latest is not None
        # Model a durable callback owner, independently of freeze's state guard.
        _write_receipt(
            path,
            replace(
                latest,
                maintenance_evidence={
                    **latest.maintenance_evidence,
                    "checksMainWorkloadAuthority": binding,
                    "retainedRecoveryEvidence": {"verified": True},
                },
            ),
        )
        if write_before_event:
            record({"action": "restored", "workers": ("worker-0", "worker-1")})
        return {"restored": True}

    completed = run_campaign(
        path=path,
        intent=intent,
        segment_executors={
            name: lambda: CampaignSegmentResult(evidence={}) for name in intent.segments
        },
        enter_maintenance=lambda *_: {"active": True},
        restore_maintenance=restore,
        assert_fence=lambda: None,
    )
    entry = completed.maintenance_evidence["entry"]
    assert entry["checksMainWorkloadAuthority"] == binding
    assert entry["retainedRecoveryEvidence"] == {"verified": True}
    assert entry["events"] == [{"action": "restored", "workers": ["worker-0", "worker-1"]}]


@pytest.mark.parametrize("record_event", [False, True])
def test_restoration_never_overwrites_changed_campaign_authority(tmp_path, record_event):
    intent, path = _intent(), tmp_path / "campaign.json"
    changed = None

    def restore(record, _evidence):
        nonlocal changed
        latest = load_campaign_receipt(path)
        assert latest is not None
        _write_receipt(path, replace(latest, cluster_id="different-cluster"))
        changed = path.read_bytes()
        if record_event:
            record({"action": "restored"})
        return {"restored": True}

    with pytest.raises(RuntimeError, match="restoration authority changed"):
        run_campaign(
            path=path,
            intent=intent,
            segment_executors={
                name: lambda: CampaignSegmentResult(evidence={}) for name in intent.segments
            },
            enter_maintenance=lambda *_: {"active": True},
            restore_maintenance=restore,
            assert_fence=lambda: None,
        )
    assert path.read_bytes() == changed


def test_restoration_resumes_with_refined_authority_without_repeating_segments(tmp_path):
    intent, path = _intent(), tmp_path / "campaign.json"
    authority = CampaignMainWorkloadAuthority(path, intent, lambda: None)
    calls = []
    restores = 0

    def segment():
        calls.append("segment")
        authority.freeze(_main_identity())
        return CampaignSegmentResult(evidence={})

    def restore(record, _evidence):
        nonlocal restores
        restores += 1
        identity = replace(
            _main_identity(), generation=restores + 2, observed_generation=restores + 2
        )
        authority.freeze(identity)
        record({"action": "restore-progress", "attempt": restores})
        if restores == 1:
            raise RuntimeError("interrupted restoration")
        authority.freeze(replace(identity, generation=5, observed_generation=5))
        return {"restored": True}

    kwargs = {
        "path": path,
        "intent": intent,
        "segment_executors": {name: segment for name in intent.segments},
        "enter_maintenance": lambda *_: {"active": True},
        "restore_maintenance": restore,
        "assert_fence": lambda: None,
    }
    with pytest.raises(RuntimeError, match="interrupted restoration"):
        run_campaign(**kwargs)
    interrupted = load_campaign_receipt(path)
    assert interrupted is not None and interrupted.maintenance == "restoring"
    assert (
        interrupted.maintenance_evidence["checksMainWorkloadAuthority"]["identity"]["generation"]
        == 3
    )
    completed = run_campaign(**kwargs)
    assert calls == ["segment"] * len(intent.segments)
    assert completed.status == "complete" and completed.maintenance == "restored"
    entry = completed.maintenance_evidence["entry"]
    assert entry["checksMainWorkloadAuthority"]["identity"]["generation"] == 5
    assert len(entry["events"]) == 2
