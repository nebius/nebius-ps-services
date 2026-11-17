"""Automatic admission of an exact retained-pair package update.

This is fresh, bounded policy authority, not a claim that a predecessor approved
new bytes. Existing cloud, package, trust and lifecycle owners still revalidate
and execute their own transitions under their locks.
"""

from __future__ import annotations

import re
from typing import Any

from packaging.version import InvalidVersion, Version

from ..apply_approval import digest, manifest_identity
from .activation_recovery import POLICY
from .apply_plan import CommandPlan
from .vm_ha_lifecycle import VMHALifecycleStatus

SCHEMA = "nebius-vpngw.package-upgrade-admission.v1"
KINDS = frozenset({"apply-convergence", "corrective-release", "upgrade-artifact-recovery"})
FIELDS = frozenset(
    {
        "schema",
        "policy",
        "frontier",
        "predecessor",
        "lifecycle",
        "desired",
        "resources",
        "trust",
        "credentials",
        "artifact",
        "packages",
        "settlement",
    }
)


def validate(record: dict) -> dict:
    if not isinstance(record, dict) or set(record) != FIELDS or record.get("schema") != SCHEMA:
        raise ValueError("Invalid package upgrade admission")
    if record["policy"] != digest(POLICY) or record["frontier"] not in KINDS:
        raise ValueError("Package upgrade admission policy changed")
    for name in FIELDS - {"schema", "frontier"}:
        if not isinstance(record[name], str) or not re.fullmatch(r"[a-f0-9]{64}", record[name]):
            raise ValueError("Package upgrade admission has invalid authority")
    return record


def _settled_creation_action(previous: dict, state: Any, action: dict, settlement: dict) -> bool:
    from .ha_replan import creation_action_matches, validate_record

    proof = validate_record(settlement, previous=state)
    return proof["predecessor"] == digest(previous) and creation_action_matches(
        previous, state, action
    )


def admit(
    *,
    previous: dict | None,
    state: Any,
    local: dict,
    project: str,
    action: dict,
    kind: str,
    artifact: Any,
    observation: dict,
    credentials: Any,
    ssh_policy: Any,
    attachments_changed: bool,
    attachment_pending: bool,
    impact: Any,
    desired: dict,
    settlement: dict | None,
    resources_verified: bool,
) -> dict | None:
    if not callable(getattr(credentials, "observation_record", None)):
        return None
    if (
        kind not in KINDS
        or state is None
        or state.transaction is None
        or state.status not in {VMHALifecycleStatus.ACTIVE, VMHALifecycleStatus.ACTIVATING}
        or state.project_id != project
        or len(state.members) != 2
        or any(not m.compute_id or not m.disk_id for m in state.members)
        or action
        != {
            "recreate": False,
            "prepare_peer_rotation": False,
            "destructive": False,
            "managed_ssh": None,
            "managed_credentials": "reuse",
        }
        or attachments_changed
        or attachment_pending
        or not resources_verified
        or impact.destructive is not False
        or impact.resource_creation is not False
        or credentials.action != "reuse"
        or credentials.credentials is None
        or any(
            item["action"] != "reuse"
            for item in credentials.observation_record()["iam_resources"].values()
        )
        or ssh_policy is None
        or ssh_policy.managed_action is not None
        or state.transaction.accepted_cloud_operation_id is not None
        or state.transaction.accepted_cloud_operation_effect is not None
        or state.transaction.desired_state_digest != digest(desired)
    ):
        return None
    parent = previous.get("transaction") if previous else None
    if parent is not None:
        assert previous is not None
        old = CommandPlan.parse(parent["plan"])
        runtime = previous["bindings"].get("runtime", {})
        # Legacy scopes include the artifact. Compare using its original digest,
        # not by ignoring configuration fields or equating version labels.
        if (
            old.configuration_sha256
            != digest({"desired": local, "artifact": old.artifact_sha256, "project": project})
            or (
                runtime.get("action_scope") != digest(action)
                and not (
                    settlement is not None
                    and _settled_creation_action(previous, state, action, settlement)
                )
            )
            or runtime.get("preparation")
            or old.project_id != project
            or old.gateway_name != state.gateway_name
        ):
            return None
        if runtime.get("continuation"):
            from .ha_preparation import command_authority, receipt

            execution, approval = command_authority(state)
            prepared = receipt(state)
            if (
                execution != parent["execution_id"]
                or not approval
                or approval.get("approval") != runtime.get("approval")
                or not prepared
                or prepared["continuation_scope"] != runtime.get("runtime_scope")
            ):
                return None
        if parent["phase"] == "pending" and kind == "apply-convergence":
            return None
    elif state.status is not VMHALifecycleStatus.ACTIVE:
        return None
    from .vm_ha_package import VMHAPackagePlan

    packages = dict(artifact.dependency_plans)
    if set(packages) != {member.instance_name for member in state.members}:
        return None
    hosts = {item["instance_name"]: item for item in observation.get("members", [])}
    for member in state.members:
        package = packages[member.instance_name]
        if not isinstance(package, VMHAPackagePlan):
            return None
        try:
            if Version(package.manifest["version"]) < Version(
                package.observation["package_version"]
            ):
                return None
        except (KeyError, TypeError, InvalidVersion):
            return None
        observed = hosts.get(member.instance_name, {})
        if (
            package.target_identity != member.compute_id
            or package.instance.hostname != member.instance_name
            or package.target != observed.get("public_ip")
            or not observed.get("present")
            or observed.get("compute_id") != member.compute_id
            or package.manifest.get("wheel_sha256") != artifact.sha256
            or "route_projection" not in package.manifest
            or package.manifest["route_projection"] is not None
        ):
            return None
    artifact.verify_current()
    ssh_policy.assert_current()
    return validate(
        {
            "schema": SCHEMA,
            "policy": digest(POLICY),
            "frontier": kind,
            "predecessor": digest(previous),
            "lifecycle": state.record_sha256,
            "desired": digest(local),
            "resources": digest(observation),
            "trust": ssh_policy.known_hosts_sha256,
            "credentials": digest(credentials.credentials.resource_bindings()),
            "artifact": artifact.sha256,
            "packages": digest(
                {name: manifest_identity(item.manifest) for name, item in packages.items()}
            ),
            "settlement": digest(settlement),
        }
    )
