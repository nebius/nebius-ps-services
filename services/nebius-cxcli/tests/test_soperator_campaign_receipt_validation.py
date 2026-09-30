"""Campaign authority is validated before both recovery and publication."""

import json
from dataclasses import asdict, replace
from itertools import product

import pytest

from nebius_cxcli import soperator_full_stack_upgrade as campaign
from nebius_cxcli.soperator_receipt_io import write_owner_only_json
from test_soperator_full_stack_upgrade import _intent


def _receipt(statuses=None, *, status="active", maintenance="active"):
    receipt = campaign._new_receipt(_intent())
    states = statuses or ("pending",) * len(receipt.segments)
    assert len(states) == len(receipt.segments)
    return replace(
        receipt,
        status=status,
        maintenance=maintenance,
        segments=tuple(
            replace(segment, status=state, evidence_sha256=campaign._sha256({}))
            for segment, state in zip(receipt.segments, states, strict=True)
        ),
    )


def _payload(receipt):
    return json.loads(json.dumps(asdict(receipt)))


def _states(prefix=0, frontier=None):
    states = ["complete"] * prefix
    if frontier:
        states.append(frontier)
    return (*states, *(("pending",) * (len(_intent().segments) - len(states))))


@pytest.mark.parametrize(
    "status,maintenance",
    product(("active", "complete"), ("pending", "entering", "active", "restoring", "restored")),
)
@pytest.mark.parametrize("ledger", ("pending", "complete", "prefix", "running", "failed"))
def test_receipt_state_table(tmp_path, status, maintenance, ledger):
    states = {
        "pending": _states(),
        "complete": _states(len(_intent().segments)),
        "prefix": _states(2),
        "running": _states(2, "running"),
        "failed": _states(2, "failed"),
    }[ledger]
    receipt = _receipt(states, status=status, maintenance=maintenance)
    valid = (
        status == "active"
        and (
            maintenance == "active"
            or maintenance in {"pending", "entering"}
            and ledger == "pending"
            or maintenance == "restoring"
            and ledger == "complete"
        )
        or status == "complete"
        and maintenance == "restored"
        and ledger == "complete"
    )
    path = tmp_path / "campaign.json"
    if valid:
        campaign._receipt_from_payload(_payload(receipt))
        campaign._write_receipt(path, receipt)
        assert campaign.load_campaign_receipt(path) is not None
    else:
        with pytest.raises(RuntimeError, match="inconsistent state"):
            campaign._receipt_from_payload(_payload(receipt))
        with pytest.raises(RuntimeError, match="inconsistent state"):
            campaign._write_receipt(path, receipt)
        assert not path.exists()


@pytest.mark.parametrize("prefix", range(len(_intent().segments)))
@pytest.mark.parametrize("frontier", (None, "running", "failed"))
def test_active_receipt_accepts_every_completed_prefix(prefix, frontier):
    campaign._receipt_from_payload(_payload(_receipt(_states(prefix, frontier))))


_INVALID = (
    "target_ref",
    "cluster_id",
    "kubernetes_uid",
    "missing",
    "duplicate",
    "reordered",
    "foreign",
    "extra",
    "empty",
    "complete-after-pending",
    "complete-after-running",
    "complete-after-failed",
    "two-running",
    "two-failed",
    "running-failed",
    "failed-running",
    "pending-running",
    "pending-failed",
)


def _invalid_receipt(case):
    receipt = _receipt()
    if case in {"target_ref", "cluster_id", "kubernetes_uid"}:
        return replace(receipt, **{case: "untrusted-value-must-not-appear"})
    segments = receipt.segments
    if case == "missing":
        segments = segments[1:]
    elif case == "duplicate":
        segments = (segments[0], segments[0], *segments[2:])
    elif case == "reordered":
        segments = (segments[1], segments[0], *segments[2:])
    elif case == "foreign":
        segments = (replace(segments[0], name="untrusted-value-must-not-appear"), *segments[1:])
    elif case == "extra":
        segments = (*segments, segments[-1])
    elif case == "empty":
        segments = ()
    else:
        first, second = {
            "complete-after-pending": ("pending", "complete"),
            "complete-after-running": ("running", "complete"),
            "complete-after-failed": ("failed", "complete"),
            "two-running": ("running", "running"),
            "two-failed": ("failed", "failed"),
            "running-failed": ("running", "failed"),
            "failed-running": ("failed", "running"),
            "pending-running": ("pending", "running"),
            "pending-failed": ("pending", "failed"),
        }[case]
        segments = (
            replace(segments[0], status=first),
            replace(segments[1], status=second),
            *segments[2:],
        )
    return replace(receipt, segments=segments)


@pytest.mark.parametrize("case", _INVALID)
@pytest.mark.parametrize("existing", (False, True))
def test_invalid_receipt_rejected_on_read_and_write_without_replacing_bytes(
    tmp_path, case, existing
):
    receipt = _invalid_receipt(case)
    with pytest.raises(RuntimeError) as error:
        campaign._receipt_from_payload(_payload(receipt))
    assert "untrusted-value-must-not-appear" not in str(error.value)
    path = tmp_path / "nested" / "campaign.json"
    if existing:
        campaign._write_receipt(path, campaign._new_receipt(_intent()))
    before = path.read_bytes() if existing else None
    with pytest.raises(RuntimeError):
        campaign._write_receipt(path, receipt)
    assert (path.read_bytes() if path.exists() else None) == before
    if not existing:
        assert not path.parent.exists()


@pytest.mark.parametrize("new_intent", (False, True))
@pytest.mark.parametrize("case", (*_INVALID, "false-completion"))
def test_invalid_load_stops_before_callbacks_archival_or_replacement(tmp_path, new_intent, case):
    receipt = (
        _invalid_receipt(case)
        if case != "false-completion"
        else replace(campaign._new_receipt(_intent()), status="complete")
    )
    path = tmp_path / "campaign.json"
    write_owner_only_json(path, _payload(receipt))
    before = path.read_bytes()
    intent = _intent()
    if new_intent:
        intent = replace(intent, source_release=intent.target_release)

    def forbidden(*_args):
        pytest.fail("invalid receipt reached a campaign callback")

    with pytest.raises(RuntimeError):
        campaign.run_campaign(
            path=path,
            intent=intent,
            segment_executors={name: forbidden for name in intent.segments},
            enter_maintenance=forbidden,
            restore_maintenance=forbidden,
            assert_fence=forbidden,
        )
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def _run(path, calls):
    def segment(name):
        calls.append(name)
        return campaign.CampaignSegmentResult(evidence={"ready": True})

    def maintenance(record, _existing):
        record({"checkpoint": "observed"})
        return {"ready": True}

    return campaign.run_campaign(
        path=path,
        intent=_intent(),
        segment_executors={name: lambda name=name: segment(name) for name in _intent().segments},
        enter_maintenance=maintenance,
        restore_maintenance=maintenance,
        assert_fence=lambda: None,
    )


def test_every_published_runner_checkpoint_can_be_reloaded_and_resumed(tmp_path, monkeypatch):
    original_write = campaign._write_receipt
    snapshots = []

    def capture(path, receipt):
        original_write(path, receipt)
        snapshots.append(campaign.load_campaign_receipt(path))

    monkeypatch.setattr(campaign, "_write_receipt", capture)
    _run(tmp_path / "reference.json", [])
    assert {r.maintenance for r in snapshots} == {
        "pending",
        "entering",
        "active",
        "restoring",
        "restored",
    }
    for boundary, expected in enumerate(snapshots, start=1):
        writes = 0
        path = tmp_path / str(boundary) / "campaign.json"

        def interrupt_after_write(path, receipt, boundary=boundary):
            nonlocal writes
            original_write(path, receipt)
            writes += 1
            if writes == boundary:
                raise KeyboardInterrupt

        monkeypatch.setattr(campaign, "_write_receipt", interrupt_after_write)
        with pytest.raises(KeyboardInterrupt):
            _run(path, [])
        saved = campaign.load_campaign_receipt(path)
        assert (saved.status, saved.maintenance) == (expected.status, expected.maintenance)
        assert [s.status for s in saved.segments] == [s.status for s in expected.segments]
        monkeypatch.setattr(campaign, "_write_receipt", original_write)
        calls = []
        assert _run(path, calls).status == "complete"
        remaining = [s.name for s in saved.segments if s.status != "complete"]
        assert calls == ([_intent().segments[-1]] if saved.status == "complete" else remaining)


@pytest.mark.parametrize("failure", (RuntimeError, KeyboardInterrupt))
def test_completed_revalidation_can_stop_and_resume_with_diagnostic_supervisor(tmp_path, failure):
    path = tmp_path / "campaign.json"
    _run(path, [])

    def fail():
        raise failure("interrupted revalidation")

    def forbidden(*_args):
        pytest.fail("completed campaign repeated a maintenance or earlier segment callback")

    executors = dict.fromkeys(_intent().segments, forbidden)
    executors[_intent().segments[-1]] = fail
    with pytest.raises(failure):
        campaign.run_campaign(
            path=path,
            intent=_intent(),
            segment_executors=executors,
            enter_maintenance=forbidden,
            restore_maintenance=forbidden,
            assert_fence=lambda: None,
        )
    saved = campaign.load_campaign_receipt(path)
    assert saved.status == "complete" and saved.maintenance == "restored"
    assert saved.supervisor["state"] == ("retrying" if failure is RuntimeError else "running")
    for state in ("recovery-required", "terminal-failed"):
        campaign._write_receipt(
            path, replace(saved, supervisor={**saved.supervisor, "state": state})
        )
        calls = []
        assert _run(path, calls).status == "complete"
        assert calls == [_intent().segments[-1]]
