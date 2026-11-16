import json

import pytest

from nebius_vpngw.repair_state import PHASES, Journal


def test_new_private_storage_creates_every_missing_parent_private(tmp_path):
    from nebius_vpngw.repair_state import directory

    target = tmp_path / "repair" / "archives" / "operation" / "systemd" / "unit"
    directory(target)
    for path in (target, *target.parents):
        if path == tmp_path:
            break
        assert path.stat().st_mode & 0o077 == 0


@pytest.fixture
def intent():
    return {
        "project": "project-fixture",
        "gateway": "gateway-fixture",
        "configuration": "a" * 64,
        "artifact": "b" * 64,
        "ha": True,
        "targets": [
            {
                "name": f"node-{i}",
                "compute": f"vm-{i}",
                "disk": f"disk-{i}",
                "transport": f"192.0.2.{i + 1}",
                "binding": str(i) * 64,
            }
            for i in range(2)
        ],
    }


def test_inspection_does_not_create_repair_state(tmp_path):
    journal = Journal(tmp_path / "repair")
    assert journal.load() is None
    assert not journal.root.exists()


def test_settled_failure_allows_successor_without_claiming_success(tmp_path, intent):
    journal = Journal(tmp_path / "repair")
    first = journal.begin(intent)
    journal.effect(
        "native-install", {"artifact": "old"}, state="accepted", receipt={"worker": "worker-a"}
    )
    journal.settle_pending({"cgroup": "empty", "native_locks": "held"})
    assert journal.load()["effects"]["native-install"]["receipt"]["worker"] == "worker-a"
    with pytest.raises(RuntimeError, match="settled preceding"):
        journal.advance("isolated")
    second = journal.begin({**intent, "artifact": "c" * 64}, predecessor_settled=True)
    assert second["id"] != first["id"]
    archived = json.loads((journal.root / "history" / (first["id"] + ".json")).read_text())
    assert archived["phase"] == "confirmed"
    assert archived["effects"]["native-install"]["state"] == "settled"


def test_resume_retains_accepted_operation_and_execution_identity(tmp_path, intent):
    journal = Journal(tmp_path / "repair")
    original = journal.begin(intent)
    journal.effect(
        "stop-node-0", {"vm": "vm-0"}, state="accepted", receipt={"operation": "accepted-stop"}
    )
    resumed = Journal(journal.root).begin(intent)
    assert resumed["id"] == original["id"]
    assert resumed["effects"]["stop-node-0"]["receipt"] == {"operation": "accepted-stop"}
    with pytest.raises(RuntimeError, match="settled"):
        journal.advance("isolated")
    journal.effect(
        "stop-node-0",
        {"vm": "vm-0"},
        state="complete",
        receipt={"operation": "accepted-stop", "stopped": True},
    )
    assert journal.advance("isolated")["phase"] == "isolated"


def test_completed_effect_cannot_be_reopened_or_retargeted(tmp_path, intent):
    journal = Journal(tmp_path / "repair")
    journal.begin(intent)
    completed = journal.effect(
        "install-node-0", {"artifact": "b" * 64}, state="complete", receipt={"verified": True}
    )
    assert journal.effect("install-node-0", {"artifact": "b" * 64}) == completed
    with pytest.raises(RuntimeError, match="request changed"):
        journal.effect("install-node-0", {"artifact": "c" * 64})


def test_changed_intent_cannot_discard_uncertain_effect(tmp_path, intent):
    journal = Journal(tmp_path / "repair")
    journal.begin(intent)
    journal.effect("stop-node-0", {"vm": "vm-0"})
    with pytest.raises(RuntimeError, match="must settle"):
        journal.begin({**intent, "artifact": "c" * 64})
    with pytest.raises(RuntimeError, match="unsettled"):
        journal.begin({**intent, "artifact": "c" * 64}, predecessor_settled=True)


def test_successor_archives_original_after_settlement(tmp_path, intent):
    journal = Journal(tmp_path / "repair")
    original = journal.begin(intent)
    successor = journal.begin({**intent, "artifact": "c" * 64}, predecessor_settled=True)
    assert successor["id"] != original["id"]
    assert (
        json.loads((journal.root / "history" / (original["id"] + ".json")).read_bytes()) == original
    )


def test_completed_repair_new_confirmation_starts_new_operation(tmp_path, intent):
    journal = Journal(tmp_path / "repair")
    original = journal.begin(intent)
    for phase in PHASES[1:]:
        journal.advance(phase)
    assert journal.begin(intent)["id"] != original["id"]


def test_repair_journal_cannot_be_redirected_through_symlink(tmp_path, intent):
    victim = tmp_path / "unrelated"
    victim.write_text("preserve")
    journal = Journal(tmp_path / "repair")
    journal.root.mkdir(mode=0o700)
    journal.path.symlink_to(victim)
    with pytest.raises(OSError):
        journal.begin(intent)
    assert victim.read_text() == "preserve"


def test_skipped_phases_cannot_manufacture_completion(tmp_path, intent):
    journal = Journal(tmp_path / "repair")
    journal.begin(intent)
    with pytest.raises(RuntimeError, match="preceding effects"):
        journal.advance("complete")


def test_accepted_effect_cannot_replace_provider_receipt(tmp_path, intent):
    journal = Journal(tmp_path / "repair")
    journal.begin(intent)
    journal.effect("stop", {"vm": "vm-0"}, state="accepted", receipt={"operation": "first"})
    for state in ("accepted", "complete"):
        with pytest.raises(RuntimeError, match="Accepted repair effect receipt changed"):
            journal.effect("stop", {"vm": "vm-0"}, state=state, receipt={"operation": "second"})
    assert journal.load()["effects"]["stop"]["receipt"]["operation"] == "first"


def destruction_receipt(record):
    from nebius_vpngw.repair_state import digest

    targets = sorted(t["compute"] for t in record["intent"]["targets"])
    return {
        "schema": "nebius-vpngw.repair-destruction.v1",
        "repair": record["id"],
        "repair_sha256": digest(record),
        "project": record["intent"]["project"],
        "gateway": record["intent"]["gateway"],
        "destroy_plan": "d" * 64,
        "targets": targets,
        "cloud_sha256": "e" * 64,
        "absence": [digest(targets)] * 2,
        "quarantine_absent": True,
    }


@pytest.mark.parametrize("boundary", ["archive", "receipt", "unlink"])
def test_destruction_retirement_resumes_without_completing_repair(
    tmp_path, intent, monkeypatch, boundary
):
    from pathlib import Path

    from nebius_vpngw import repair_state

    journal = Journal(tmp_path / "repair")
    journal.begin(intent)
    journal.effect(
        "native-install", {"artifact": "old"}, state="accepted", receipt={"worker": "old"}
    )
    original = journal.load()
    receipt = destruction_receipt(original)
    publish, unlink = repair_state.publish, Path.unlink

    def fail_publish(path, value):
        if (boundary == "archive" and path.parent.name == "history") or (
            boundary == "receipt" and path.parent.name == "retirements"
        ):
            raise OSError("interrupted publication")
        publish(path, value)

    def fail_unlink(path, *args, **kwargs):
        if boundary == "unlink" and path == journal.path:
            raise OSError("interrupted unlink")
        unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(repair_state, "publish", fail_publish)
        patch.setattr(Path, "unlink", fail_unlink)
        with pytest.raises(OSError, match="interrupted"):
            journal.retire_destroyed(original, receipt)
    assert journal.load() == original
    journal.retire_destroyed(original, receipt)
    assert journal.load() is None
    assert (
        json.loads((journal.root / "history" / (original["id"] + "-destroyed.json")).read_text())
        == original
    )
    journal.retire_destroyed(original, receipt)
    fresh = journal.begin(
        {**intent, "targets": [dict(t, compute=t["compute"] + "-new") for t in intent["targets"]]}
    )
    assert fresh["id"] != original["id"]
    with pytest.raises(RuntimeError, match="different active repair"):
        journal.retire_destroyed(original, receipt)


@pytest.mark.parametrize(
    "change", ["project", "gateway", "targets", "absence", "quarantine_absent"]
)
def test_repair_retirement_rejects_inexact_destruction(tmp_path, intent, change):
    journal = Journal(tmp_path / "repair")
    original = journal.begin(intent)
    receipt = destruction_receipt(original)
    receipt[change] = (
        False
        if change == "quarantine_absent"
        else []
        if change in {"targets", "absence"}
        else "foreign"
    )
    with pytest.raises(RuntimeError, match="exact destruction proof"):
        journal.retire_destroyed(original, receipt)
    assert journal.load() == original
