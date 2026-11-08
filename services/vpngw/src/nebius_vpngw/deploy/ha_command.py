"""Compose retained HA runtime and attachment owners under one command identity.

The lifecycle remains the runtime authority. This envelope cannot release locks,
prove forwarding, or settle a cloud request. The caller must independently verify
current runtime/ownership before accepting the lifecycle's terminal receipt.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ..apply_approval import digest, manifest_identity
from ..apply_approval import validate as validate_approval
from . import security_group_apply as attachments
from .apply_command import CommandJournal
from .apply_plan import CommandPlan, Effect, Progress
from .vm_ha_lifecycle import VMHALifecycleState, VMHALifecycleStatus


@dataclass
class HACommand:
    journal: CommandJournal
    plan: CommandPlan
    intent: dict
    resumed: bool

    @property
    def approval(self) -> str:
        return self.intent["approval"]

    @property
    def execution_id(self) -> str:
        return self.journal.state["transaction"]["execution_id"]

    @property
    def runtime(self) -> Effect:
        return runtime_effect(self.plan)

    @property
    def bindings(self) -> dict:
        return {"runtime": self.intent, **self.intent.get("prerequisites", {})}

    def begin(self) -> None:
        successor = self.intent.get("successor")
        current = self.journal.state["transaction"]
        continuation = self.intent.get("continuation")
        if continuation and (not current or current["phase"] == "complete"):
            if digest(self.journal.state) != continuation["predecessor"]:
                raise RuntimeError("Peer continuation lost its exact preparation command")
        if successor and current and current["approval"] != self.plan.approval:
            self.journal.supersede(
                self.plan,
                intents=self.bindings,
                predecessor=successor["predecessor"],
            )
        else:
            self.journal.begin(self.plan, intents=self.bindings)

    def start_prerequisite(self, key: str) -> None:
        if key not in self.intent.get("prerequisites", {}):
            raise RuntimeError("HA prerequisite is outside the approved command")
        if self.journal.state["transaction"]["progress"][key] != Progress.VERIFIED.value:
            self.journal.started(key)

    def start_runtime(self) -> None:
        self.journal.started("runtime")

    def lifecycle_bindings(self) -> dict[str, str]:
        from ..apply_approval import SCHEMA

        return {
            "runtime-command-execution": self.execution_id,
            "runtime-command-approval": json.dumps(
                {
                    "schema": SCHEMA,
                    "approval": self.approval,
                    "scope": self.intent["runtime_scope"],
                },
                sort_keys=True,
            ),
            "runtime-package-manifests": json.dumps(
                self.intent["packages"]["manifests"], sort_keys=True
            ),
        }

    def package_resume(self, lifecycle: VMHALifecycleState | None) -> tuple[str, dict] | None:
        saved = self.intent.get("packages")
        if saved is None:
            return None  # Guarded successor owners retain their own package authority.
        if lifecycle is None:
            if saved["checkpoint"] is not None:
                raise RuntimeError("Pending HA package intent lost its lifecycle authority")
            return saved["parent"], deepcopy(saved["manifests"])
        if lifecycle.record_sha256 != saved["checkpoint"]:
            transaction = lifecycle.transaction
            bindings = dict(transaction.resource_bindings) if transaction else {}
            from .ha_preparation import command_authority

            execution, _approval = command_authority(lifecycle)
            snapshot = self.journal.snapshot()
            pending = snapshot["transaction"] if snapshot else None
            continuation = self.intent.get("continuation")
            from .ha_created_packages import saved_manifests

            lifecycle_manifests = (
                saved_manifests(lifecycle)
                if continuation
                else json.loads(bindings.get("runtime-package-manifests", "{}"))
            )
            pending_handoff = bool(
                continuation
                and transaction is not None
                and transaction.pending_effect == "authorize-peer-rotation-continuation"
                and transaction.predecessor_sha256 == saved["checkpoint"]
                and bindings.get("runtime-command-execution") == continuation["previous_execution"]
            )
            if (
                transaction is None
                or pending is None
                or transaction.operation_id != saved["parent"]
                or not (execution == pending["execution_id"] or pending_handoff)
                or {
                    name: manifest_identity(manifest)
                    for name, manifest in lifecycle_manifests.items()
                }
                != {
                    name: manifest_identity(manifest)
                    for name, manifest in saved["manifests"].items()
                }
            ):
                raise RuntimeError("Pending HA package intent has a different lifecycle authority")
        return saved["parent"], deepcopy(saved["manifests"])

    def prepared_receipt(self, lifecycle: VMHALifecycleState) -> dict:
        from . import ha_preparation

        snapshot = self.journal.snapshot()
        transaction = lifecycle.transaction
        bindings = dict(transaction.resource_bindings) if transaction else {}
        prepared = ha_preparation.receipt(lifecycle)
        approval = json.loads(bindings.get("runtime-command-approval", "null"))
        validate_approval(approval)
        if (
            not self.intent.get("preparation")
            or self.intent["attachments"] is not None
            or not snapshot
            or not snapshot["transaction"]
            or prepared is None
            or transaction is None
            or transaction.pending_effect is not None
            or ha_preparation.CONTINUATION in bindings
            or lifecycle.project_id != self.plan.project_id
            or lifecycle.gateway_name != self.plan.gateway_name
            or not self.members_match(lifecycle)
            or prepared["artifact"] != self.plan.artifact_sha256
            or prepared["preparation_scope"] != self.intent["runtime_scope"]
            or approval["approval"] != self.approval
            or approval["scope"] != self.intent["runtime_scope"]
            or bindings.get("runtime-command-execution") != snapshot["transaction"]["execution_id"]
            or transaction.operation_id != self.intent["packages"]["parent"]
        ):
            raise RuntimeError("HA command lacks its exact prepared lifecycle receipt")
        return {"prepared": digest(prepared), "lifecycle": lifecycle.record_sha256}

    def finish_preparation(self, lifecycle: VMHALifecycleState) -> None:
        self.journal.verified("runtime", self.prepared_receipt(lifecycle))
        self.journal.complete()

    def execute_attachments(
        self,
        manager: Any,
        ssh: Any,
        local: dict,
        instances: tuple,
        child_journal: attachments.AttachmentJournal,
        *,
        lifecycle: VMHALifecycleState,
        verify_ha: Any,
        observed: dict,
        request_retry: dict | None = None,
        refresh_ssh_policy: Any = None,
    ) -> None:
        """Caller has just independently proven native parity and HA authority."""
        with self.journal:
            self.begin()
            self.verify_runtime(lifecycle)
            selected = self.intent["attachments"]
            if selected is not None:
                if (
                    self.journal.state["transaction"]["progress"]["attachments"]
                    != Progress.VERIFIED.value
                ):
                    self.journal.started("attachments")
                    attachments.execute(
                        manager,
                        ssh,
                        selected,
                        local,
                        instances,
                        child_journal,
                        scope=self.plan.configuration_sha256,
                        approval=self.approval,
                        operation_id=self.execution_id,
                        verify_ha=verify_ha,
                        request_retry=request_retry,
                        command_journal=self.journal,
                        refresh_ssh_policy=refresh_ssh_policy,
                    )
            observed = attachments.inspect_plan(
                manager,
                tuple(observed["members"]),
                tuple(observed["desired_ids"]),
            )
            self.finish_attachments(child_journal.read_transaction(), observed)

    def runtime_receipt(self, lifecycle: VMHALifecycleState) -> dict:
        current = self.journal.pending()
        execution_id = current["execution_id"] if current else None
        transaction = lifecycle.transaction
        bindings = dict(transaction.resource_bindings) if transaction else {}
        try:
            successor = self.intent.get("successor")
            if successor:
                from .vm_ha_corrective import authority
                from .vm_ha_upgrade_recovery import recovery_authority

                recovered = (
                    authority(lifecycle)
                    if successor["kind"] == "corrective-release"
                    else recovery_authority(lifecycle)
                )
                exact_authority = bool(
                    recovered
                    and recovered["approval"] == self.approval
                    and recovered["artifact"] == self.plan.artifact_sha256
                    and recovered["prior_artifact"] == successor["prior_artifact"]
                    and recovered["prior_record"] == successor["prior_lifecycle"]
                )
            else:
                from .ha_preparation import command_authority

                execution, approval = command_authority(lifecycle)
                if approval is None:
                    raise RuntimeError("HA runtime has no command approval")
                validate_approval(approval)
                exact_authority = (
                    execution == execution_id
                    and approval["approval"] == self.approval
                    and approval["scope"] == self.intent["runtime_scope"]
                )
            valid = (
                lifecycle.status is VMHALifecycleStatus.ACTIVE
                and lifecycle.project_id == self.plan.project_id
                and lifecycle.gateway_name == self.plan.gateway_name
                and self.members_match(lifecycle)
                and transaction is not None
                and transaction.pending_effect is None
                and (
                    self.intent.get("packages") is None
                    or transaction.operation_id == self.intent["packages"]["parent"]
                )
                and bindings[self.intent.get("artifact_key", "heartbeat-upgrade-artifact")]
                == self.plan.artifact_sha256
                and exact_authority
            )
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise RuntimeError("HA command lacks its exact terminal lifecycle receipt")
        return {"lifecycle": lifecycle.record_sha256}

    def members_match(self, lifecycle: VMHALifecycleState) -> bool:
        members = self.intent.get("members")
        if members is None:
            return sorted(member.compute_id for member in lifecycle.members) == sorted(
                self.runtime.targets
            )
        from .ha_members import verify_members

        return verify_members(lifecycle, members)

    def terminal_runtime(self, lifecycle: VMHALifecycleState) -> bool:
        try:
            self.runtime_receipt(lifecycle)
            return True
        except RuntimeError:
            return False

    def verify_runtime(self, lifecycle: VMHALifecycleState) -> None:
        self.journal.verified("runtime", self.runtime_receipt(lifecycle))

    def attachment_plan(self, observed: dict, child: dict | None) -> dict | None:
        original = self.intent["attachments"]
        if original is None:
            if attachments.changed(observed) or child and child["phase"] == "pending":
                raise RuntimeError("Pending HA apply does not authorize attachment changes")
            return None
        pending = self.journal.pending()
        execution_id = pending["execution_id"] if pending else None
        if (
            child is not None
            and child["phase"] == "pending"
            and (
                child["operation_id"] != execution_id
                or child["approval"] != self.approval
                or child["plan"] != original
            )
        ):
            successor = self.intent.get("successor")
            if not (
                successor
                and attachments.unused_prelude(child)
                and digest(child) == successor["attachment_predecessor"]
            ):
                raise RuntimeError("Pending attachments belong to another HA command")
        # Projection admission is not a terminal receipt: the child owner still
        # has to settle every uncertain/accepted mutation under this execution.
        return attachments.resume_plan(
            observed,
            {"scope": self.plan.configuration_sha256, "plan": original},
            scope=self.plan.configuration_sha256,
        )

    def finish_attachments(self, child: dict | None, observed: dict) -> None:
        original = self.attachment_plan(observed, child)
        if original is not None:
            if (
                child is None
                or child["phase"] != "complete"
                or child["operation_id"] != self.execution_id
                or child["approval"] != self.approval
                or child["scope"] != self.plan.configuration_sha256
                or child["plan"] != original
                or set(child["verified"]) != set(original["members"])
                or any(item["status"] != "settled" for item in child["operations"].values())
                or attachments.changed(observed)
            ):
                raise RuntimeError("HA command lacks its exact terminal attachment receipt")
            self.journal.verified(
                "attachments", {"child": digest(child), "observed": digest(observed)}
            )
        self.journal.complete()


def runtime_effect(plan: CommandPlan) -> Effect:
    effect = next((item for item in plan.effects if item.key == "runtime"), None)
    if effect is None or effect.owner != "ha-lifecycle":
        raise RuntimeError("Command has no HA lifecycle effect")
    return effect


def pending_prerequisites(
    journal: CommandJournal,
    *,
    preparation: bool = False,
    lifecycle: VMHALifecycleState | None = None,
) -> dict | None:
    """Read original prerequisite authority before mutable discovery observations."""
    state = journal.snapshot()
    transaction = state["transaction"] if state else None
    if not transaction:
        return None
    if transaction["phase"] != "pending" and not (
        preparation
        and state is not None
        and state["bindings"].get("runtime", {}).get("preparation")
        and lifecycle is not None
        and lifecycle.status is VMHALifecycleStatus.ACTIVATING
        and lifecycle.transaction is not None
        and dict(lifecycle.transaction.resource_bindings).get("runtime-command-execution")
        == transaction["execution_id"]
    ):
        return None
    plan = CommandPlan.parse(transaction["plan"])
    if not any(item.key == "runtime" and item.owner == "ha-lifecycle" for item in plan.effects):
        return None
    assert state is not None
    runtime = state["bindings"].get("runtime")
    if runtime is None:
        raise RuntimeError("Pending HA command lost its runtime intent")
    if any(
        state["bindings"].get(key) != value
        for key, value in runtime.get("prerequisites", {}).items()
    ):
        raise RuntimeError("Pending HA prerequisite intent changed")
    return state


def resume(
    journal: CommandJournal,
    *,
    scope: str,
    artifact: str,
    action_scope: str,
    targets: tuple[str, ...],
    preparation: bool = False,
    lifecycle: VMHALifecycleState | None = None,
) -> HACommand | None:
    state = journal.snapshot()
    transaction = state["transaction"] if state else None
    if transaction is None:
        return None
    assert state is not None
    if transaction["phase"] == "complete" and not (
        preparation
        and state["bindings"].get("runtime", {}).get("preparation")
        and lifecycle is not None
        and lifecycle.status is VMHALifecycleStatus.ACTIVATING
    ):
        return None
    plan = CommandPlan.parse(transaction["plan"])
    if not any(item.key == "runtime" and item.owner == "ha-lifecycle" for item in plan.effects):
        return None
    if plan.artifact_sha256 != artifact:
        raise RuntimeError("Pending HA apply artifact changed; resume the original operation")
    intent = state["bindings"].get("runtime")
    target_match = tuple(sorted(runtime_effect(plan).targets)) == tuple(sorted(targets))
    if (
        not target_match
        and transaction["phase"] == "complete"
        and preparation
        and lifecycle is not None
        and intent is not None
        and intent.get("members") is not None
    ):
        from .ha_members import verify_members

        target_match = verify_members(lifecycle, intent["members"])
    if (
        intent is None
        or plan.configuration_sha256 != scope
        or plan.artifact_sha256 != artifact
        or not target_match
        or intent["action_scope"] != action_scope
    ):
        raise RuntimeError("Pending HA apply requires its original artifact, targets and intent")
    return HACommand(journal, plan, intent, True)


def create(
    journal: CommandJournal,
    *,
    scope: str,
    artifact: str,
    action_scope: str,
    targets: tuple[str, ...],
    approval: str,
    runtime_scope: str,
    report: dict,
    attachment_plan: dict | None,
    successor: dict | None = None,
    packages: dict | None = None,
    preparation: bool = False,
    continuation: dict | None = None,
    credentials: dict | None = None,
    node_ids: tuple[str, ...] = (),
    ssh: dict | None = None,
    members: dict | None = None,
    artifact_key: str = "heartbeat-upgrade-artifact",
) -> HACommand:
    intent: dict = {
        "approval": approval,
        "action_scope": action_scope,
        "runtime_scope": runtime_scope,
        "report": report,
        "attachments": attachment_plan,
        "packages": deepcopy(packages),
    }
    if members is not None:
        intent["members"] = deepcopy(members)
        intent["artifact_key"] = artifact_key
    if successor is not None:
        intent["successor"] = successor
    if preparation:
        if attachment_plan is not None or packages is None or continuation is not None:
            raise RuntimeError("Preparation needs only its exact runtime package intent")
        intent["preparation"] = True
    if continuation is not None:
        intent["continuation"] = deepcopy(continuation)
    prerequisites = {}
    effects = []
    if credentials is not None:
        prerequisites["credentials"] = deepcopy(credentials)
        effects.append(
            Effect(
                "credentials",
                "managed-credentials",
                "enroll",
                node_ids,
                digest(credentials),
                digest(credentials),
                False,
            )
        )
    if ssh is not None:
        prerequisites["ssh"] = deepcopy(ssh)
        effects.append(
            Effect(
                "ssh",
                "ssh-bootstrap",
                "publish",
                tuple(sorted(ssh["targets"])),
                digest(ssh),
                digest(ssh),
                False,
                tuple(item.key for item in effects),
            )
        )
    if prerequisites:
        intent["prerequisites"] = prerequisites
    effects.append(
        Effect(
            "runtime",
            "ha-lifecycle",
            "apply",
            targets,
            digest(intent),
            runtime_scope,
            True,
            tuple(prerequisites),
        )
    )
    if attachment_plan is not None:
        effects.append(
            Effect(
                "attachments",
                "security-groups",
                "reconcile",
                tuple(
                    attachment_plan["members"][name]["compute_id"]
                    for name in sorted(attachment_plan["members"])
                ),
                digest({"approval": approval, "plan": attachment_plan}),
                digest(attachment_plan),
                True,
                ("runtime",),
            )
        )
    plan = CommandPlan(journal.project, journal.gateway, scope, artifact, tuple(effects))
    return HACommand(journal, plan, intent, False)


def package_intent(
    state: VMHALifecycleState | None,
    approval: str,
    manifests: dict,
    *,
    operation: str | None = None,
) -> dict:
    from .vm_ha_lifecycle import provisioning_operation_id

    if state is not None and state.status in {
        VMHALifecycleStatus.PROVISIONING,
        VMHALifecycleStatus.ACTIVATING,
    }:
        if state.transaction is None:
            raise RuntimeError("Pending lifecycle lacks package operation authority")
        parent = state.transaction.operation_id
    else:
        parent = provisioning_operation_id(approval, state.record_sha256 if state else None)
    if operation is not None:
        if (
            state is not None
            and state.status in {VMHALifecycleStatus.PROVISIONING, VMHALifecycleStatus.ACTIVATING}
            and parent != operation
        ):
            raise RuntimeError("Package intent has a different replacement operation")
        parent = operation
    return {
        "parent": parent,
        "checkpoint": state.record_sha256 if state else None,
        "manifests": deepcopy(manifests),
    }


def require_runtime_scope(command: HACommand, scope: str) -> None:
    if command.intent["runtime_scope"] != scope:
        raise RuntimeError("Pending HA apply does not authorize different package requests")
    pending = command.journal.pending()
    if (
        pending
        and pending["progress"]["runtime"] == Progress.VERIFIED.value
        and not command.intent.get("preparation")
    ):
        raise RuntimeError("Verified HA runtime drift requires a separately reviewed repair")


def continuation_intent(journal: CommandJournal, state: VMHALifecycleState, record: dict) -> dict:
    """Only an exact completed preparation can hand its child to a new command."""
    previous = journal.snapshot()
    if not previous or not previous["transaction"]:
        raise RuntimeError("Peer continuation lost its completed preparation command")
    transaction = previous["transaction"]
    intent = previous["bindings"].get("runtime")
    if transaction["phase"] != "complete" or not intent or not intent.get("preparation"):
        raise RuntimeError("Complete the original preparation before its continuation")
    prior = HACommand(journal, CommandPlan.parse(transaction["plan"]), intent, True)
    prior.prepared_receipt(state)
    from .ha_preparation import receipt

    prepared = receipt(state)
    if prepared is None or record["scope"] != prepared["continuation_scope"]:
        raise RuntimeError("Peer continuation changed its original preparation scope")
    return {
        "previous_execution": transaction["execution_id"],
        "predecessor": digest(previous),
        "prepared": digest(prepared),
    }


def reviewed_successor(
    previous: dict,
    *,
    lifecycle: VMHALifecycleState,
    kind: str,
    old_scope: str,
    action_scope: str,
    targets: tuple[str, ...],
    attachment_child: dict | None,
) -> dict:
    """Called only after the existing guarded recovery owner admits its plan."""
    transaction = previous["transaction"]
    old = CommandPlan.parse(transaction["plan"])
    if (
        transaction["phase"] != "pending"
        or old.configuration_sha256 != old_scope
        or runtime_effect(old).targets != targets
        or previous["bindings"]["runtime"]["action_scope"] != action_scope
        or lifecycle.status is not VMHALifecycleStatus.ACTIVATING
        or lifecycle.transaction is None
        or lifecycle.transaction.accepted_cloud_operation_id is not None
        or kind not in {"corrective-release", "upgrade-artifact-recovery"}
    ):
        raise RuntimeError("Reviewed HA successor does not match the pending command")
    if (
        attachment_child is not None
        and attachment_child["phase"] == "pending"
        and (
            not attachments.unused_prelude(attachment_child)
            or attachment_child["operation_id"] != transaction["execution_id"]
            or attachment_child["plan"] != previous["bindings"]["runtime"]["attachments"]
        )
    ):
        raise RuntimeError("Settle pending attachment effects before a corrective HA command")
    return {
        "predecessor": digest(previous),
        "prior_artifact": old.artifact_sha256,
        "prior_lifecycle": lifecycle.record_sha256,
        "kind": kind,
        "attachment_predecessor": digest(attachment_child) if attachment_child else None,
    }
