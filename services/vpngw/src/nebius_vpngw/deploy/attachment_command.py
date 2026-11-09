"""Compose an attachment-only HA command with its existing cloud effect owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..apply_approval import digest
from . import security_group_apply as sg_apply
from .apply_command import CommandJournal
from .apply_plan import CommandPlan, Effect


def _plan(
    journal: CommandJournal, scope: str, artifact: str, child: dict, approval: str
) -> CommandPlan:
    return CommandPlan(
        journal.project,
        journal.gateway,
        scope,
        artifact,
        (
            Effect(
                "attachments",
                "security-groups",
                "reconcile",
                tuple(child["members"][name]["compute_id"] for name in sorted(child["members"])),
                digest({"approval": approval, "plan": child}),
                digest(child),
                True,
            ),
        ),
    )


@dataclass
class AttachmentCommand:
    journal: CommandJournal
    plan: CommandPlan
    attachments: dict
    approval: str
    scope: str
    completed_child: bool
    resumed: bool
    execution_id: str | None = None

    def begin(self) -> None:
        self.journal.begin(self.plan, execution_id=self.execution_id)
        self.execution_id = self.journal.state["transaction"]["execution_id"]
        if not self.completed_child:
            self.journal.started("attachments")

    def execute(
        self,
        manager: Any,
        ssh: Any,
        local: dict,
        instances: tuple,
        child_journal: sg_apply.AttachmentJournal,
        *,
        verify_ha: Any,
        request_retry: dict | None = None,
        refresh_ssh_policy: Any = None,
    ) -> None:
        with self.journal:
            self.begin()
            if not self.completed_child:
                sg_apply.execute(
                    manager,
                    ssh,
                    self.attachments,
                    local,
                    instances,
                    child_journal,
                    scope=self.scope,
                    approval=self.approval,
                    verify_ha=verify_ha,
                    operation_id=self.execution_id,
                    request_retry=request_retry,
                    command_journal=self.journal,
                    refresh_ssh_policy=refresh_ssh_policy,
                )
            self.finish(
                child_journal.read_transaction(),
                sg_apply.inspect_plan(
                    manager,
                    tuple(self.attachments["members"]),
                    tuple(self.attachments["desired_ids"]),
                ),
            )

    def finish(self, child: dict | None, observed: dict) -> None:
        """Fresh cloud/HA verification belongs to the caller and child owner."""
        if (
            child is None
            or child["phase"] != "complete"
            or child["scope"] != self.scope
            or child["approval"] != self.approval
            or child["operation_id"] != self.journal.state["transaction"]["execution_id"]
            or child["plan"] != self.attachments
            or child["plan_digest"] != digest(self.attachments)
            or set(child["verified"]) != set(self.attachments["members"])
            or any(record["status"] != "settled" for record in child["operations"].values())
            or sg_apply.changed(observed)
        ):
            raise RuntimeError("Attachment command lacks exact terminal child evidence")
        sg_apply.resume_plan(observed, child, scope=self.scope)
        if CommandPlan.parse(self.journal.state["transaction"]["plan"]) != self.plan:
            raise RuntimeError("Attachment command authority changed before completion")
        self.journal.verified("attachments", {"child": digest(child), "observed": digest(observed)})
        self.journal.complete()


def inspect(
    journal: CommandJournal,
    *,
    observed: dict,
    child: dict | None,
    scope: str,
    artifact: str,
) -> AttachmentCommand | None:
    """Read-only selection; an unchanged command never creates an envelope."""
    pending = journal.pending()
    if (
        pending is None
        and (child is None or child["phase"] == "complete")
        and not sg_apply.changed(observed)
    ):
        return None
    original = CommandPlan.parse(pending["plan"]) if pending else None
    if original is not None and (
        original.configuration_sha256 != scope
        or original.artifact_sha256 != artifact
        or len(original.effects) != 1
        or original.effects[0].owner != "security-groups"
        or original.effects[0].key != "attachments"
    ):
        raise RuntimeError("Pending apply does not authorize an attachment-only command")
    selected, completed, resumed = observed, False, original is not None
    execution_id = pending["execution_id"] if pending else None
    if child is not None and (child["phase"] == "pending" or original is not None):
        if (
            child["phase"] == "pending"
            and execution_id is not None
            and child["operation_id"] != execution_id
        ):
            raise RuntimeError("Pending attachments belong to another command execution")
        expected_approval = digest({"scope": scope, "attachments": child["plan"]})
        candidate = _plan(journal, scope, artifact, child["plan"], child["approval"])
        if (
            child["approval"] == expected_approval
            and (original is None or candidate == original)
            and (execution_id is None or child["operation_id"] == execution_id)
        ):
            selected = sg_apply.resume_plan(observed, child, scope=scope)
            completed, resumed = child["phase"] == "complete", True
            execution_id = child["operation_id"]
        elif child["phase"] == "pending":
            raise RuntimeError("Pending attachments belong to a different composed apply")
    approval = digest({"scope": scope, "attachments": selected})
    plan = _plan(journal, scope, artifact, selected, approval)
    if original is not None and plan != original:
        raise RuntimeError("Attachment command scope changed; retain the original operation")
    return AttachmentCommand(
        journal, original or plan, selected, approval, scope, completed, resumed, execution_id
    )
