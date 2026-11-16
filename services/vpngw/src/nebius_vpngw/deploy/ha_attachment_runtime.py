"""Read terminal runtime and prerequisite owners before HA attachment retirement."""

from __future__ import annotations

from copy import copy

from ..apply_approval import digest, manifest_identity
from . import ha_command, ordinary_handoff, package_reservation, vm_ha_corrective
from .apply_plan import CommandPlan, Progress
from .provisioning_trust import selection
from .recovery import ReplanRejected


def original(command, parent: dict, child: dict) -> ha_command.HACommand:
    """Reconstruct the canonical command; never infer authority from progress alone."""
    snapshot = command.snapshot()
    if snapshot is None or snapshot["transaction"] != parent:
        raise ReplanRejected("HA attachment parent changed")
    plan = CommandPlan.parse(parent["plan"])
    intent = snapshot["bindings"].get("runtime")
    if (
        intent is None
        or any(intent.get(key) for key in ("preparation", "continuation", "successor"))
        or parent["progress"].get("attachments") != Progress.STARTED.value
        or any(
            value != Progress.VERIFIED.value
            for key, value in parent["progress"].items()
            if key != "attachments"
        )
        or child["scope"] != plan.configuration_sha256
        or child["approval"] != intent["approval"]
        or child["plan"] != intent["attachments"]
        or child["operation_id"] != parent["execution_id"]
    ):
        raise ReplanRejected("HA attachment lacks its completed original runtime authority")
    credential_effect = next(
        (effect for effect in plan.effects if effect.key == "credentials"), None
    )
    candidate = ha_command.create(
        command,
        scope=plan.configuration_sha256,
        artifact=plan.artifact_sha256,
        action_scope=intent["action_scope"],
        targets=ha_command.runtime_effect(plan).targets,
        approval=intent["approval"],
        runtime_scope=intent["runtime_scope"],
        report=intent["report"],
        attachment_plan=intent["attachments"],
        packages=intent["packages"],
        members=intent.get("members"),
        artifact_key=intent.get("artifact_key", "heartbeat-upgrade-artifact"),
        credentials=snapshot["bindings"].get("credentials"),
        node_ids=credential_effect.targets if credential_effect else (),
        ssh=snapshot["bindings"].get("ssh"),
    )
    if candidate.plan != plan or candidate.bindings != snapshot["bindings"]:
        raise ReplanRejected("HA attachment original command plan differs")
    return candidate


def observe(command, parent, lifecycle, owners, snapshots, guard, prerequisites) -> dict:
    """Fresh readback only: completed journals remain canonical until succession."""
    if prerequisites is None:
        raise ReplanRejected("HA attachment requires fresh credential and SSH observations")
    runtime = original(command, parent["transaction"], guard.transaction)
    receipt = runtime.runtime_receipt(lifecycle)
    if parent["transaction"]["receipts"]["runtime"]["sha256"] != digest(receipt):
        raise ReplanRejected("HA attachment terminal runtime receipt differs")
    credential_plan, ssh_policy = prerequisites()
    if (
        credential_plan.credentials is None
        or credential_plan.observation_record()["action"] != "reuse"
        or any(
            item["action"] != "reuse"
            for item in credential_plan.observation_record()["iam_resources"].values()
        )
        or ssh_policy is None
        or ssh_policy.managed_action is not None
    ):
        raise ReplanRejected("HA attachment runtime credentials or SSH identity changed")
    ssh_policy.assert_current()
    credential_bindings = credential_plan.credentials.resource_bindings()
    retained = dict(lifecycle.transaction.resource_bindings)
    if not credential_bindings or credential_bindings != {
        key: value for key, value in retained.items() if key.startswith("credential-")
    }:
        raise ReplanRejected("HA attachment runtime credential binding changed")
    for name in ("credentials", "ssh"):
        expected = parent["bindings"].get(name)
        if expected is None:
            continue
        snapshot = snapshots[name]
        child = snapshot["transaction"] if snapshot else None
        if (
            child is None
            or child["phase"] != "complete"
            or child["intent"] != expected
            or child["execution_id"] != parent["transaction"]["execution_id"]
            or child["approval"] != parent["transaction"]["approval"]
        ):
            raise ReplanRejected("HA attachment completed prerequisite authority differs")
        owner = copy(owners[name])
        owner.state = snapshot
        if name == "credentials":
            owner._admit_observation(credential_plan, child)
            if (
                child["receipt"] != credential_bindings
                or child["selected"] != credential_plan.public_key_sha256
            ):
                raise ReplanRejected("HA attachment credential receipt changed")
        elif child["selected"] != selection(ssh_policy, tuple(child["intent"]["members"])):
            raise ReplanRejected("HA attachment SSH receipt changed")
        if parent["transaction"]["receipts"][name]["sha256"] != digest(owner.receipt()):
            raise ReplanRejected("HA attachment parent prerequisite receipt changed")
    root = package_reservation.recovery_owner(
        command.root,
        parent["transaction"]["execution_id"],
        digest(runtime.intent),
    )
    if root != (command.root, parent["transaction"]["execution_id"], digest(runtime.intent)):
        raise ReplanRejected("HA attachment requires its original package owner")
    packages = runtime.package_resume(lifecycle)
    if packages is None or set(packages[1]) != {
        member.instance_name for member in lifecycle.members
    }:
        raise ReplanRejected("HA attachment lacks both original package manifests")
    evidence = {}
    configs = {instance.hostname: instance for instance in guard.instances}
    operation = digest(["terminal-ha-attachment", parent])
    for member in lifecycle.members:
        reservation = package_reservation.read(*root, member.instance_name)
        if (
            reservation is None
            or reservation["request"]["package"]["desired"].get("ha_parent") != packages[0]
            or manifest_identity(reservation["request"]["package"]["desired"])
            != manifest_identity(packages[1][member.instance_name])
        ):
            raise ReplanRejected("HA attachment package reservation changed")
        observed = ordinary_handoff.corrective_action(
            guard.ssh,
            guard.targets[member.instance_name][1],
            configs[member.instance_name],
            guard.local,
            dict(
                action="replan-inspect",
                operation=operation,
                package_reservation=reservation,
                terminal_runtime=True,
            ),
        )
        vm_ha_corrective.completed_children(
            observed["children"],
            parent=packages[0],
            artifact=runtime.plan.artifact_sha256,
        )
        if (
            observed["children"]["runtime"]["phase"] != "complete"
            or observed["boot_id"] != guard.transaction["guests"][member.instance_name]["boot"]
            or observed["package_worker"] is None
            or observed["package_worker"]["identity"] != reservation["identity"]
            or observed["intent"] is not None
            or observed["settlement"] is not None
            or not observed.get("installed")
        ):
            raise ReplanRejected("HA attachment package owner is not terminal in this boot")
        evidence[member.instance_name] = observed
    return dict(
        receipt=receipt,
        credentials=credential_bindings,
        ssh=ssh_policy.known_hosts_sha256,
        packages=evidence,
    )
