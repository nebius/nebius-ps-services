"""Command-bound publication receipts for the existing SSH trust owner."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path

from ..apply_approval import digest
from .apply_command import CommandJournal
from .apply_plan import CommandPlan, Progress
from .replacement import Journal, metadata_only
from .ssh_policy import SSHTrustPolicy, persist_planned_host_keys, publish_vm_ha_ssh_trust


def policy_intent(policy: SSHTrustPolicy) -> str:
    return (
        policy.fresh_identity_approval_sha256
        or policy.managed_receipt_sha256
        or policy.known_hosts_sha256
    )


def selection(policy: SSHTrustPolicy, members: tuple[str, ...]) -> dict:
    policy.assert_current()
    return {
        "receipt_sha256": policy.managed_receipt_sha256,
        "projection_sha256": policy.known_hosts_sha256,
        "identities": {
            name: hashlib.sha256(policy.identity_for(name).private_key).hexdigest()
            for name in members
        },
    }


def intent(policy: SSHTrustPolicy, members: tuple[str, ...]) -> dict:
    if not members or members != tuple(sorted(set(members))):
        raise ValueError("SSH bootstrap requires exact new members")
    if not set(members) <= {name for name, _ in policy.transport_targets}:
        raise ValueError("SSH bootstrap members have no exact transport policy")
    return {
        "policy_sha256": policy_intent(policy),
        "members": list(members),
        "targets": dict(policy.transport_targets),
        "pins": dict(policy.pin_targets),
    }


def _sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _validate_intent(value: dict) -> None:
    if (
        set(value) != {"policy_sha256", "members", "targets", "pins"}
        or not _sha(value["policy_sha256"])
        or not isinstance(value["members"], list)
        or not value["members"]
        or any(not isinstance(name, str) or not name for name in value["members"])
        or value["members"] != sorted(set(value["members"]))
        or not isinstance(value["targets"], dict)
        or not isinstance(value["pins"], dict)
        or not set(value["members"]) <= set(value["targets"])
        or set(value["targets"]) != set(value["pins"])
        or any(
            not isinstance(name, str) or not name or not isinstance(target, str) or not target
            for mapping in (value["targets"], value["pins"])
            for name, target in mapping.items()
        )
    ):
        raise ValueError("Invalid SSH bootstrap intent")


class TrustJournal(Journal):
    schema = "nebius-vpngw.provisioning-trust.v1"

    def __init__(self, project: str, gateway: str, *, root: Path | None = None) -> None:
        super().__init__(
            project,
            gateway,
            root=root
            or Path.home() / ".local/state/nebius-vpngw/provisioning-trust" / project / gateway,
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
                or state["bindings"]
                or type(state["revision"]) is not int
                or state["revision"] < 0
            ):
                raise ValueError("Invalid SSH bootstrap identity")
            current = state["transaction"]
            if current is None:
                return
            if (
                set(current) != {"intent", "approval", "execution_id", "selected", "phase"}
                or not _sha(current["approval"])
                or re.fullmatch(r"[a-f0-9]{32}", current["execution_id"]) is None
                or current["phase"] not in {"pending", "complete"}
            ):
                raise ValueError("Invalid SSH bootstrap authority")
            _validate_intent(current["intent"])
            chosen = current["selected"]
            if chosen is None:
                if current["phase"] == "complete":
                    raise ValueError("SSH bootstrap lacks selected identity proof")
            elif (
                set(chosen) != {"receipt_sha256", "projection_sha256", "identities"}
                or chosen["receipt_sha256"] is not None
                and not _sha(chosen["receipt_sha256"])
                or not _sha(chosen["projection_sha256"])
                or set(chosen["identities"]) != set(current["intent"]["members"])
                or any(not _sha(value) for value in chosen["identities"].values())
            ):
                raise ValueError("Invalid SSH bootstrap selection")
        except (KeyError, TypeError, AttributeError, ValueError) as error:
            raise ValueError("SSH bootstrap journal is invalid") from error

    def begin(self, desired: dict, *, approval: str, execution_id: str) -> None:
        _validate_intent(desired)
        current = self.state["transaction"]
        if current is not None:
            if (
                current["intent"] == desired
                and current["approval"] == approval
                and current["execution_id"] == execution_id
            ):
                return
            if current["phase"] != "complete" or current["execution_id"] == execution_id:
                raise RuntimeError("SSH bootstrap requires its original command")
            self.archive()
        self.state["transaction"] = {
            "intent": json.loads(json.dumps(desired)),
            "approval": approval,
            "execution_id": execution_id,
            "selected": None,
            "phase": "pending",
        }
        self.save()

    def select(self, chosen: dict) -> None:
        current = self.state["transaction"]
        if current["selected"] is not None:
            if current["selected"] != chosen:
                raise RuntimeError("Selected SSH bootstrap identity changed")
            return
        candidate = {**self.state, "transaction": {**current, "selected": chosen}}
        self.validate(candidate)
        self.state = candidate
        self.save()

    def complete(self) -> dict:
        current = self.state["transaction"]
        if current["selected"] is None:
            raise RuntimeError("SSH bootstrap identity is unverified")
        if current["phase"] != "complete":
            current["phase"] = "complete"
            self.save()
        return self.receipt()

    def receipt(self) -> dict:
        current = self.state["transaction"]
        if current is None or current["phase"] != "complete":
            raise RuntimeError("SSH bootstrap has no terminal receipt")
        return {
            "intent": digest(current["intent"]),
            "selection": digest(current["selected"]),
            "execution_id": current["execution_id"],
        }


def publish(
    command: CommandJournal,
    child: TrustJournal,
    desired: dict,
    observe: Callable[[], SSHTrustPolicy],
    *,
    key: str = "ssh",
) -> dict:
    """Resume publication, never replacing a selected or conflicting private key."""
    current = command.snapshot()
    if command._lock is None or current is None or current != command.state:
        raise RuntimeError("SSH bootstrap requires the active exact command writer")
    parent = current["transaction"]
    if parent is None:
        raise RuntimeError("SSH bootstrap has no approved command")
    plan = CommandPlan.parse(parent["plan"])
    effect = next((item for item in plan.effects if item.key == key), None)
    if (
        effect is None
        or effect.owner != "ssh-bootstrap"
        or effect.request_sha256 != digest(desired)
        or current["bindings"].get(key) != desired
        or set(effect.targets) != set(desired["targets"])
        or parent["progress"][key] not in {Progress.STARTED.value, Progress.VERIFIED.value}
        or child.project != command.project
        or child.gateway != command.gateway
    ):
        raise RuntimeError("SSH bootstrap differs from its approved command")

    def admit() -> None:
        if command.snapshot() != current or command.state != current:
            raise RuntimeError("SSH bootstrap command changed during publication")

    with child:
        if parent["progress"][key] == Progress.VERIFIED.value and (
            child.state["transaction"] is None or child.state["transaction"]["phase"] != "complete"
        ):
            raise RuntimeError("Completed SSH bootstrap lacks its exact child receipt")
        child.begin(desired, approval=parent["approval"], execution_id=parent["execution_id"])
        if parent["progress"][key] == Progress.VERIFIED.value and parent["receipts"][key] != {
            "owner": effect.owner,
            "sha256": digest(child.receipt()),
        }:
            raise RuntimeError("Completed SSH bootstrap has a different child receipt")
        policy = observe()
        admit()
        if (
            dict(policy.transport_targets) != desired["targets"]
            or dict(policy.pin_targets) != desired["pins"]
        ):
            raise RuntimeError("SSH bootstrap transport scope changed")
        if child.state["transaction"]["selected"] is None:
            if policy_intent(policy) != desired["policy_sha256"]:
                raise RuntimeError("SSH bootstrap policy changed before key publication")
            persist_planned_host_keys(policy)
        child.select(selection(policy, tuple(desired["members"])))
        admit()
        if child.state["transaction"]["phase"] != "complete":
            publish_vm_ha_ssh_trust(policy)
        if (
            selection(observe(), tuple(desired["members"]))
            != child.state["transaction"]["selected"]
        ):
            raise RuntimeError("Published SSH bootstrap differs from selected identities")
        admit()
        return child.complete()
