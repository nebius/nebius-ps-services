from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from nebius_vpngw.apply_approval import digest
from nebius_vpngw.deploy import ha_command, upgrade_admission
from nebius_vpngw.deploy.apply_command import CommandJournal
from nebius_vpngw.deploy.vm_ha_lifecycle import VMHALifecycleStatus
from nebius_vpngw.deploy.vm_ha_package import VMHAPackagePlan


@pytest.fixture
def update(tmp_path):
    local = {"project_id": "project", "gateway_group": {"name": "gateway"}}
    action = {
        "recreate": False,
        "prepare_peer_rotation": False,
        "destructive": False,
        "managed_ssh": None,
        "managed_credentials": "reuse",
    }
    journal = CommandJournal("project", "gateway", root=tmp_path / "command")
    command = ha_command.create(
        journal,
        scope=digest({"desired": local, "artifact": "a" * 64, "project": "project"}),
        artifact="a" * 64,
        action_scope=digest(action),
        targets=("vm-0", "vm-1"),
        approval="b" * 64,
        runtime_scope="c" * 64,
        report={},
        attachment_plan=None,
    )
    with journal:
        command.begin()
    prior = journal.snapshot()
    desired = {"configuration": "unchanged"}
    state = NS(
        project_id="project",
        gateway_name="gateway",
        status=VMHALifecycleStatus.ACTIVATING,
        members=tuple(
            NS(instance_name=f"gateway-{n}", compute_id=f"vm-{n}", disk_id=f"disk-{n}")
            for n in range(2)
        ),
        transaction=NS(
            desired_state_digest=digest(desired),
            accepted_cloud_operation_id=None,
            accepted_cloud_operation_effect=None,
        ),
        record_sha256="d" * 64,
    )
    credentials = NS(
        action="reuse",
        credentials=NS(resource_bindings=lambda: {"identity": "same"}),
        observation_record=lambda: {"iam_resources": {"key": {"action": "reuse"}}},
    )
    artifact = NS(
        sha256="e" * 64,
        verify_current=lambda: None,
        dependency_plans=tuple(
            (
                f"gateway-{n}",
                VMHAPackagePlan(
                    wheel=tmp_path / "candidate.whl",
                    observation={"package_version": "0.6.5"},
                    dependency_paths=[],
                    effects=[],
                    temporary=NS(cleanup=lambda: None),
                    target_identity=f"vm-{n}",
                    target=f"192.0.2.{n + 1}",
                    instance=NS(hostname=f"gateway-{n}"),
                    manifest={
                        "wheel_sha256": "e" * 64,
                        "route_projection": None,
                        "version": "0.6.6",
                    },
                ),
            )
            for n in range(2)
        ),
    )
    return dict(
        previous=prior,
        state=state,
        local=local,
        project="project",
        action=action,
        kind="corrective-release",
        artifact=artifact,
        observation={
            "members": [
                {
                    "instance_name": f"gateway-{n}",
                    "compute_id": f"vm-{n}",
                    "public_ip": f"192.0.2.{n + 1}",
                    "present": True,
                }
                for n in range(2)
            ]
        },
        credentials=credentials,
        ssh_policy=NS(
            managed_action=None, known_hosts_sha256="f" * 64, assert_current=lambda: None
        ),
        attachments_changed=False,
        attachment_pending=False,
        impact=NS(destructive=False, resource_creation=False),
        desired=desired,
        settlement=None,
        resources_verified=True,
    )


def test_changed_artifact_gets_fresh_policy_authority_without_reusing_approval(update):
    original = deepcopy(update["previous"])
    record = upgrade_admission.admit(**update)
    assert record["artifact"] == "e" * 64
    assert record["predecessor"] == digest(original)
    assert record["desired"] == digest(update["local"])
    assert "approval" not in record
    assert update["previous"] == original


@pytest.mark.parametrize(
    "change",
    [
        "config",
        "credential",
        "trust",
        "vm",
        "transport",
        "attachment",
        "pending-cloud",
        "impact",
        "preparation",
        "closure",
        "unknown-resource",
    ],
)
def test_expanded_or_unproven_effects_are_never_auto_admitted(update, change):
    if change == "config":
        update["local"]["new"] = True
    elif change == "credential":
        update["credentials"].action = "create"
    elif change == "trust":
        update["ssh_policy"].managed_action = "reconcile"
    elif change == "vm":
        update["artifact"].dependency_plans[0][1].target_identity = "foreign"
    elif change == "transport":
        update["artifact"].dependency_plans[0][1].target = "192.0.2.99"
    elif change == "attachment":
        update["attachment_pending"] = True
    elif change == "pending-cloud":
        update["state"].transaction.accepted_cloud_operation_id = "pending"
    elif change == "impact":
        update["impact"].resource_creation = True
    elif change == "preparation":
        update["action"]["prepare_peer_rotation"] = True
    elif change == "closure":
        update["artifact"].dependency_plans = ()
    else:
        update["resources_verified"] = False
    assert upgrade_admission.admit(**update) is None


def test_admission_changes_when_authoritative_state_changes_before_execution(update):
    initial = upgrade_admission.admit(**update)
    update["observation"]["revision"] = "new"
    assert upgrade_admission.admit(**update) != initial
    corrupted = {**initial, "policy": "0" * 64}
    with pytest.raises(ValueError, match="policy changed"):
        upgrade_admission.validate(corrupted)


def test_non_ha_route_projection_requires_review(update):
    update["artifact"].dependency_plans[0][1].manifest["route_projection"] = {
        "routes": ["owned-route"]
    }
    assert upgrade_admission.admit(**update) is None


@pytest.mark.parametrize("changed", ["predecessor", "observation", "missing-refresh"])
def test_automatic_admission_rechecks_locked_authority(update, tmp_path, changed):
    record = upgrade_admission.admit(**update)
    journal = CommandJournal("project", "gateway", root=tmp_path / "command")
    command = ha_command.create(
        journal,
        scope="f" * 64,
        artifact=record["artifact"],
        action_scope=digest(update["action"]),
        targets=("vm-0", "vm-1"),
        approval="1" * 64,
        runtime_scope="2" * 64,
        report={},
        attachment_plan=None,
        admission=record,
        successor={"predecessor": record["predecessor"]},
    )
    command.admission_check = lambda: record
    if changed == "observation":
        command.admission_check = lambda: {**record, "resources": "0" * 64}
    elif changed == "missing-refresh":
        command.admission_check = None
    with journal:
        if changed == "predecessor":
            journal.state["revision"] += 1
        before = deepcopy(journal.state)
        with pytest.raises(RuntimeError, match="Automatic upgrade"):
            command.begin()
        assert journal.state == before


def test_installed_newer_version_never_automatically_downgrades(update):
    update["artifact"].dependency_plans[0][1].observation["package_version"] = "0.6.7"
    assert upgrade_admission.admit(**update) is None


def test_automatic_upgrade_resumes_persisted_plan_without_new_admission(update, tmp_path):
    record = upgrade_admission.admit(**update)
    root = tmp_path / "command"
    journal = CommandJournal("project", "gateway", root=root)
    command = ha_command.create(
        journal,
        scope="f" * 64,
        artifact=record["artifact"],
        action_scope=digest(update["action"]),
        targets=("vm-0", "vm-1"),
        approval="1" * 64,
        runtime_scope="2" * 64,
        report={},
        attachment_plan=None,
        admission=record,
        successor={"predecessor": record["predecessor"]},
    )
    command.admission_check = lambda: record
    with journal:
        command.begin()
    execution = command.execution_id
    before = journal.path.read_bytes()
    reopened = CommandJournal("project", "gateway", root=root)
    resumed = ha_command.resume(
        reopened,
        scope="f" * 64,
        artifact=record["artifact"],
        action_scope=digest(update["action"]),
        targets=("vm-0", "vm-1"),
    )
    assert resumed is not None and resumed.admission_check is None
    with reopened:
        resumed.begin()
    assert resumed.execution_id == execution
    assert reopened.path.read_bytes() == before
