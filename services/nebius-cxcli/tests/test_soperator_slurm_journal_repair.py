from __future__ import annotations

import copy
import json

import pytest

from nebius_cxcli.soperator_slurm_journal_repair import admit_action_identity_repair
from nebius_cxcli.soperator_slurm_recovery import (
    SOPERATOR_SLURM_RECOVERY_SCHEMA,
    normalize_slurm_recovery_event,
    validate_slurm_recovery_actions,
)


def event(name, *, broken=False, **fields):
    row = {
        "namespace": "soperator",
        "checkpoint_id": "checkpoint",
        "action": name,
        "node_names": ["worker-0", "worker-1"],
        **fields,
    }
    result = normalize_slurm_recovery_event(row, fencing_epoch=7)
    if broken:
        # The former writer omitted tuple node subjects before JSON made them lists.
        result["actionId"] = normalize_slurm_recovery_event(
            {**row, "node_names": []}, fencing_epoch=7
        )["actionId"]
    return result


def journal():
    return {
        "schema": SOPERATOR_SLURM_RECOVERY_SCHEMA,
        "targetRef": "target",
        "command": "deploy",
        "startedAt": "2026-01-01T00:00:00Z",
        "policy": {"jobPolicy": "wait-to-finish"},
        "slurmPreimage": {"mode": "not-required"},
        "operationSpecSha256": "sha256:" + "a" * 64,
        "status": "recovery-required",
        "actions": [
            event("slurm-gate-started"),
            event("scheduling-pause-recorded", broken=True, partitions=[{"partition": "gpu"}]),
            event("scheduling-pause-applied", broken=True, partitions=[{"partition": "gpu"}]),
            event("no-blocking-jobs", broken=True),
            event("slurm-gate-complete"),
        ],
    }


def test_exact_repair_retains_originals_and_only_changes_action_ids():
    from nebius_cxcli import cli

    source = journal()
    before = copy.deepcopy(source)
    with pytest.raises(RuntimeError, match="identity changed"):
        validate_slurm_recovery_actions(source["actions"])
    result = admit_action_identity_repair(source, local=before, allow_repair=True)
    assert source == before
    assert result["actionIdentityRepair"]["originalActions"] == before["actions"]
    assert cli._soperator_slurm_operation_evidence(
        result
    ) == cli._soperator_slurm_operation_evidence(before)
    for old, new in zip(before["actions"], result["actions"], strict=True):
        assert {**new, "actionId": old["actionId"]} == old
    validate_slurm_recovery_actions(result["actions"])
    # The cluster write may complete before the old local copy is refreshed.
    result = json.loads(json.dumps(result))
    assert admit_action_identity_repair(result, local=before, allow_repair=False) == result
    result["actions"].append(event("restoration-observed"))
    assert admit_action_identity_repair(result, local=result, allow_repair=False) == result


def test_valid_journal_needs_no_repair_or_local_copy():
    source = journal()
    source["actions"] = [event("slurm-gate-started"), event("slurm-gate-complete")]
    assert admit_action_identity_repair(source, local=None, allow_repair=False) == source


@pytest.mark.parametrize("difference", ["missing", "actions", "policy", "operationSpecSha256"])
def test_repair_requires_independent_matching_journals(difference):
    source = journal()
    local = copy.deepcopy(source)
    if difference == "missing":
        local = None
    else:
        local[difference] = None
    with pytest.raises(RuntimeError, match="matching exact operation"):
        admit_action_identity_repair(source, local=local, allow_repair=True)


@pytest.mark.parametrize(
    "difference",
    ["scope", "unknown_action", "hash", "kind", "epoch", "missing_gate", "duplicate_gate"],
)
def test_unproven_action_changes_remain_rejected(difference):
    source = journal()
    row = source["actions"][1]
    if difference == "scope":
        row["node_names"] = ["worker-other"]
    elif difference == "unknown_action":
        row["action"] = "pending-hold-recorded"
    elif difference == "hash":
        row["actionId"] = "sha256:" + "b" * 64
    elif difference == "kind":
        row["actionKind"] = "nodes-drain-recorded"
    elif difference == "epoch":
        row["fencingEpoch"] = 0
    elif difference == "missing_gate":
        source["actions"].pop()
    else:
        source["actions"].append(copy.deepcopy(source["actions"][-1]))
    with pytest.raises(RuntimeError):
        admit_action_identity_repair(source, local=copy.deepcopy(source), allow_repair=True)


@pytest.mark.parametrize("field", ["schema", "command", "status", "operationSpecSha256"])
def test_repair_does_not_admit_other_operations(field):
    source = journal()
    source[field] = "unrelated"
    with pytest.raises(RuntimeError, match="matching exact operation"):
        admit_action_identity_repair(source, local=copy.deepcopy(source), allow_repair=True)


@pytest.mark.parametrize("difference", ["identity", "original", "prefix", "digest", "schema"])
def test_repair_seal_is_revalidated(difference):
    source = journal()
    result = admit_action_identity_repair(source, local=source, allow_repair=True)
    receipt = result["actionIdentityRepair"]
    if difference == "identity":
        result["operationSpecSha256"] = "sha256:" + "c" * 64
    elif difference == "original":
        receipt["originalActions"][1]["node_names"] = ["worker-other"]
    elif difference == "prefix":
        result["actions"][1]["actionId"] = "sha256:" + "c" * 64
    elif difference == "digest":
        receipt["correctedSha256"] = "sha256:" + "c" * 64
    else:
        receipt["schema"] = "unrelated"
    with pytest.raises(RuntimeError):
        admit_action_identity_repair(result, local=result, allow_repair=True)


def test_unapproved_repair_stays_rejected():
    source = journal()
    with pytest.raises(RuntimeError, match="identity changed"):
        admit_action_identity_repair(source, local=source, allow_repair=False)
