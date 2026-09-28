from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from destroy_fakes import Cloud, receipt
from nebius_cxcli.destroy import (
    DestroyReceipt,
    digest,
    expected_destroy_confirmation,
    load_destroy_receipt,
    run_destroy,
    write_destroy_receipt,
)
from nebius_cxcli.destroy_cloud import TerminalDestroyOperationError


def execute(current, cloud, saved, **kwargs):
    def save(value):
        saved.append(copy.deepcopy(value))
        cloud.events.append("save:" + (value.checkpoints[-1] if value.checkpoints else "planned"))

    callbacks = {
        key: (lambda key=key: cloud.events.append(key))
        for key in ("reconcile", "publish", "clear_baseline")
    }
    callbacks.update(kwargs)
    return run_destroy(
        receipt=current,
        cloud=cloud,
        save=save,
        approval_mode="interactive",
        confirmation=expected_destroy_confirmation(
            current.cluster_id, delete_sfs=current.approved["delete_sfs"], filesystem_count=1
        ),
        **callbacks,
    )


@pytest.mark.parametrize("delete_sfs", [False, True])
@pytest.mark.parametrize("ownership", ["managed", "onboarded"])
def test_one_sdk_cluster_request_and_explicit_storage_disposition(delete_sfs, ownership):
    cloud, saved = Cloud(), []
    result = execute(receipt(delete_sfs=delete_sfs, ownership=ownership), cloud, saved)
    assert result.status == "complete"
    assert [row[0] for row in cloud.calls] == ["cluster", *(["filesystem"] if delete_sfs else [])]
    assert ("filesystem-a" in cloud.present) is not delete_sfs
    assert (
        cloud.events.index("cluster-absence")
        < cloud.events.index("reconcile")
        < cloud.events.index("publish")
        < cloud.events.index("clear_baseline")
    )
    request_saved = next(row for row in saved if row.requests)
    assert request_saved.requests["cluster:mk8scluster-a"]["operation_id"] == ""
    op_saved = next(
        row
        for row in saved
        if row.requests and row.requests["cluster:mk8scluster-a"]["operation_id"]
    )
    assert not op_saved.requests["cluster:mk8scluster-a"]["absent"]


@pytest.mark.parametrize("interactive,phrase", [(False, "destroy mk8scluster-a"), (True, "yes")])
def test_first_approval_requires_tty_and_phrase(interactive, phrase):
    cloud, saved = Cloud(), []
    with pytest.raises(RuntimeError):
        run_destroy(
            receipt=receipt(),
            cloud=cloud,
            save=saved.append,
            approval_mode="interactive" if interactive else "resume",
            confirmation=phrase,
            reconcile=lambda: None,
            publish=lambda: None,
            clear_baseline=lambda: None,
        )
    assert not saved and not cloud.calls


def test_cloud_drift_does_not_publish_approval_or_delete():
    cloud, saved = Cloud(), []
    cloud.fail_scope = True
    with pytest.raises(RuntimeError, match="scope changed"):
        execute(receipt(), cloud, saved)
    assert not saved and not cloud.calls


@pytest.mark.parametrize("failure", ["fail_submit", "fail_poll"])
def test_resume_correlates_unknown_acceptance_or_polls_known_operation(failure):
    cloud, saved = Cloud(), []
    setattr(cloud, failure, True)
    with pytest.raises(TimeoutError):
        execute(receipt(), cloud, saved)
    assert saved[-1].status == "failed"
    setattr(cloud, failure, False)
    result = execute(saved[-1], cloud, saved)
    assert result.status == "complete"
    assert len(cloud.calls) == 1
    assert ("recover:cluster" in cloud.events) is (failure == "fail_submit")


def test_unknown_acceptance_does_not_replay(monkeypatch):
    cloud, saved = Cloud(), []
    cloud.fail_submit = True
    with pytest.raises(TimeoutError):
        execute(receipt(), cloud, saved)

    def unknown(*args):
        raise RuntimeError("acceptance is unknown")

    monkeypatch.setattr(cloud, "recover_operation", unknown)
    with pytest.raises(RuntimeError, match="unknown"):
        execute(saved[-1], cloud, saved)
    assert len(cloud.calls) == 1 and "publish" not in cloud.events


def test_lost_receipt_write_prevents_submission():
    cloud = Cloud()

    def failed_save(value):
        if value.requests:
            raise RuntimeError("lost backend lease")

    with pytest.raises(RuntimeError, match="lost backend lease"):
        run_destroy(
            receipt=receipt(),
            cloud=cloud,
            save=failed_save,
            approval_mode="interactive",
            confirmation="destroy mk8scluster-a",
            reconcile=lambda: None,
            publish=lambda: None,
            clear_baseline=lambda: None,
        )
    assert not cloud.calls


@pytest.mark.parametrize("frontier", ["reconcile", "publish", "clear_baseline"])
def test_resume_after_each_local_frontier_never_repeats_sdk_delete(frontier):
    cloud, saved = Cloud(), []

    def fail():
        raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        execute(receipt(), cloud, saved, **{frontier: fail})
    result = execute(saved[-1], cloud, saved)
    assert result.status == "complete" and len(cloud.calls) == 1


def test_terminal_failure_retry_requires_new_confirmation(monkeypatch):
    cloud, saved = Cloud(), []
    original = cloud.poll_delete

    def fail(*args):
        raise TerminalDestroyOperationError("terminal failure")

    monkeypatch.setattr(cloud, "poll_delete", fail)
    with pytest.raises(TerminalDestroyOperationError):
        execute(receipt(), cloud, saved)
    current = saved[-1]
    with pytest.raises(RuntimeError, match="renewed"):
        run_destroy(
            receipt=current,
            cloud=cloud,
            save=saved.append,
            approval_mode="resume",
            confirmation=None,
            reconcile=lambda: None,
            publish=lambda: None,
            clear_baseline=lambda: None,
        )
    monkeypatch.setattr(cloud, "poll_delete", original)
    result = execute(current, cloud, saved)
    assert result.status == "complete" and len(cloud.calls) == 2
    assert cloud.calls[0][2] != cloud.calls[1][2]
    assert result.requests["cluster:mk8scluster-a"]["previous_operations"] == ["operation-cluster"]


def test_receipt_roundtrip_is_private_and_immutable(tmp_path):
    path = tmp_path / "receipt.json"
    original = receipt()
    write_destroy_receipt(path, original)
    assert path.stat().st_mode & 0o777 == 0o600
    assert load_destroy_receipt(path) == original
    changed = dict(original.approved, delete_sfs=True)
    with pytest.raises(ValueError, match="modified"):
        replace(original, approved=changed)
    with pytest.raises(RuntimeError, match="only v1"):
        DestroyReceipt.from_payload({"schema": "nebius-cxcli.destroy.v2"})
    with pytest.raises(ValueError, match="unordered"):
        replace(original, checkpoints=("cluster_absent",))
    assert digest(original.approved) == original.approval_fingerprint
