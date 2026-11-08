"""Durable command scope; child adapters retain ownership of every effect.

This metadata envelope is written under the existing gateway writer lock before
the first child effect. It cannot settle a cloud operation or override a guest
guard. Read-only discovery and an unchanged command never publish a revision.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from ..apply_approval import digest
from .apply_plan import CommandPlan, Progress
from .replacement import Journal, metadata_only


class CommandJournal(Journal):
    schema = "nebius-vpngw.apply-command.v1"

    def __init__(self, project: str, gateway: str, *, root: Path | None = None) -> None:
        super().__init__(
            project,
            gateway,
            root=root or Path.home() / ".local/state/nebius-vpngw/apply" / project / gateway,
        )

    def validate(self, state: dict) -> None:
        metadata_only(state)
        try:
            if (
                set(state)
                != {"schema", "project_id", "gateway_name", "revision", "bindings", "transaction"}
                or state["schema"] != self.schema
                or state["project_id"] != self.project
                or state["gateway_name"] != self.gateway
                or type(state["revision"]) is not int
                or state["revision"] < 0
                or not isinstance(state["bindings"], dict)
            ):
                raise ValueError("Invalid command identity")
            transaction = state["transaction"]
            if transaction is None:
                if state["bindings"]:
                    raise ValueError("Child intent has no command authority")
                return
            if set(transaction) != {
                "plan",
                "approval",
                "execution_id",
                "phase",
                "progress",
                "receipts",
            }:
                raise ValueError("Invalid command envelope")
            plan = CommandPlan.parse(transaction["plan"])
            if (
                plan.project_id != self.project
                or plan.gateway_name != self.gateway
                or plan.approval != transaction["approval"]
                or not isinstance(transaction["execution_id"], str)
                or re.fullmatch(r"[a-f0-9]{32}", transaction["execution_id"]) is None
                or transaction["phase"] not in {"pending", "complete"}
                or set(transaction["progress"]) != {effect.key for effect in plan.effects}
                or not set(transaction["receipts"]) <= set(transaction["progress"])
            ):
                raise ValueError("Invalid command authority")
            progress = {key: Progress(value) for key, value in transaction["progress"].items()}
            if not set(state["bindings"]) <= set(progress):
                raise ValueError("Child intent belongs to an unplanned effect")
            for key, intent in state["bindings"].items():
                if not isinstance(intent, dict) or digest(intent) != next(
                    effect.request_sha256 for effect in plan.effects if effect.key == key
                ):
                    raise ValueError("Child intent differs from its approved request")
            if set(transaction["receipts"]) != {
                key for key, value in progress.items() if value is Progress.VERIFIED
            }:
                raise ValueError("Missing child receipt reference")
            for effect in plan.effects:
                if progress[effect.key] is not Progress.PLANNED and any(
                    progress[key] is not Progress.VERIFIED for key in effect.requires
                ):
                    raise ValueError("Command prerequisites are unverified")
            if transaction["phase"] == "complete" and any(
                value is not Progress.VERIFIED for value in progress.values()
            ):
                raise ValueError("Command has outstanding effects")
            for key, receipt in transaction["receipts"].items():
                if (
                    progress[key] is not Progress.VERIFIED
                    or set(receipt) != {"owner", "sha256"}
                    or receipt["owner"] != next(e.owner for e in plan.effects if e.key == key)
                    or len(receipt["sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in receipt["sha256"])
                ):
                    raise ValueError("Invalid child receipt reference")
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise ValueError("Apply command journal is invalid") from error

    def pending(self) -> dict | None:
        state = self.snapshot()
        transaction = state["transaction"] if state else None
        return transaction if transaction and transaction["phase"] == "pending" else None

    def begin(
        self,
        plan: CommandPlan,
        *,
        intents: dict[str, dict] | None = None,
        execution_id: str | None = None,
    ) -> None:
        """Retain optional owner intent whose exact digest is in the approved plan."""
        bindings = intents or {}
        metadata_only(bindings)
        bindings = json.loads(json.dumps(bindings))
        current = self.state["transaction"]
        if current and current["phase"] == "pending":
            if (
                current["approval"] != plan.approval
                or self.state["bindings"] != bindings
                or execution_id is not None
                and execution_id != current["execution_id"]
            ):
                raise RuntimeError("Another apply command is pending; review a successor")
            return
        if not plan.effects:
            if bindings:
                raise ValueError("Unchanged apply cannot bind child intent")
            return
        if current and execution_id == current["execution_id"]:
            raise RuntimeError("Completed command execution cannot be restarted")
        transaction = {
            "plan": json.loads(json.dumps(plan.mapping())),
            "approval": plan.approval,
            "execution_id": execution_id or uuid.uuid4().hex,
            "phase": "pending",
            "progress": {effect.key: Progress.PLANNED.value for effect in plan.effects},
            "receipts": {},
        }
        candidate = {**self.state, "transaction": transaction, "bindings": bindings}
        self.validate(candidate)
        if current is not None:
            self.archive()
        self.state = candidate
        self.save()

    def started(self, key: str) -> None:
        transaction = self.state["transaction"]
        state = Progress(transaction["progress"][key])
        if state is Progress.VERIFIED:
            raise RuntimeError("Completed command effect cannot run again")
        if state is Progress.PLANNED:
            transaction["progress"][key] = Progress.STARTED.value
            self.save()

    def supersede(self, plan: CommandPlan, *, intents: dict[str, dict], predecessor: str) -> None:
        """Publish an owner-admitted reviewed successor; retain exact old bytes."""
        current = self.state["transaction"]
        if (
            current is None
            or current["phase"] != "pending"
            or digest(self.state) != predecessor
            or current["approval"] == plan.approval
            or intents.get("runtime", {}).get("successor", {}).get("predecessor") != predecessor
        ):
            raise RuntimeError("Apply successor no longer matches its admitted predecessor")
        candidate = {
            **self.state,
            "bindings": json.loads(json.dumps(intents)),
            "transaction": {
                "plan": json.loads(json.dumps(plan.mapping())),
                "approval": plan.approval,
                "execution_id": uuid.uuid4().hex,
                "phase": "pending",
                "progress": {effect.key: Progress.PLANNED.value for effect in plan.effects},
                "receipts": {},
            },
        }
        self.validate(candidate)
        self.archive()
        self.state = candidate
        self.save()

    def verified(self, key: str, receipt: dict) -> None:
        metadata_only(receipt)
        transaction = self.state["transaction"]
        if transaction["progress"][key] == Progress.VERIFIED.value:
            return
        plan = CommandPlan.parse(transaction["plan"])
        owner = next(effect.owner for effect in plan.effects if effect.key == key)
        transaction["progress"][key] = Progress.VERIFIED.value
        transaction["receipts"][key] = {"owner": owner, "sha256": digest(receipt)}
        self.save()

    def complete(self) -> None:
        transaction = self.state["transaction"]
        if transaction and transaction["phase"] != "complete":
            if any(value != Progress.VERIFIED.value for value in transaction["progress"].values()):
                raise RuntimeError("Apply still has unverified child effects")
            transaction["phase"] = "complete"
            self.save()


def replacement_plan(child: dict) -> CommandPlan:
    """The existing replacement approval authorizes this complete child scope."""
    from .apply_plan import Effect

    return CommandPlan(
        child["project_id"],
        child["gateway_name"],
        child["configuration_sha256"],
        child["artifact_sha256"],
        (
            Effect(
                "replacement",
                "gateway-replacement",
                "replace",
                tuple(member["compute_id"] for member in child["members"]),
                digest(child),
                digest(child["members"]),
                True,
            ),
        ),
    )


def begin_replacement(journal: CommandJournal, child: dict) -> None:
    """Resume only the approved child, including loss of its final response."""
    plan = replacement_plan(child["plan"])
    if child["approval"] != digest(child["plan"]):
        raise RuntimeError("Replacement child has different approved authority")
    current = journal.state["transaction"]
    if current and current["phase"] == "pending":
        if CommandPlan.parse(current["plan"]) != plan:
            raise RuntimeError("Pending apply does not authorize this replacement")
        if current["progress"]["replacement"] == Progress.VERIFIED.value:
            if child["phase"] != "complete":
                raise RuntimeError("Verified replacement cannot run again")
            return
    elif current and CommandPlan.parse(current["plan"]) == plan:
        if child["phase"] != "complete":
            raise RuntimeError("Completed apply cannot restart its replacement")
        return
    journal.begin(plan)
    journal.started("replacement")


def require_replacement_scope(journal: CommandJournal, child_plan: dict) -> None:
    current = journal.pending()
    if (
        current
        and current["phase"] == "pending"
        and CommandPlan.parse(current["plan"]) != replacement_plan(child_plan)
    ):
        raise RuntimeError("Resume the pending apply before starting another replacement")
