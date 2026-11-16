"""Reconcile only submitted predecessor route requests under separate approval.

The old executor also submits its unstarted suffix. Settlement deliberately uses
only the cloud receipt owner, verifies independent readback, and publishes the
missing prefix receipts. It never completes the obsolete route plan.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from ..apply_approval import digest
from .apply_plan import CommandPlan
from .ordinary_route_conversion import resolve
from .ordinary_route_execute import _admit
from .ordinary_route_ownership import snapshot
from .recovery import ReplanRejected
from .replacement_cloud import Cloud


def require_original(parent: dict, original: dict, current: dict) -> None:
    """Only the original operation set may advance through its own receipts."""
    before, after = original["transaction"], current["transaction"]
    if (
        before is None
        or after is None
        or before["phase"] != "pending"
        or after["phase"] != "pending"
        or before["approval"] != parent["approval"]
        or not any(
            effect.owner == "ordinary-routes" and effect.request_sha256 == digest(before["intent"])
            for effect in CommandPlan.parse(parent["plan"]).effects
        )
        or {key: value for key, value in before.items() if key not in {"operations", "verified"}}
        != {key: value for key, value in after.items() if key not in {"operations", "verified"}}
        or set(before["operations"]) != set(after["operations"])
        or not set(before["verified"]) <= set(after["verified"])
    ):
        raise ReplanRejected("Route settlement has different predecessor authority")
    ranks = {"intent": 0, "accepted": 1, "settled": 2}
    for key, earlier in before["operations"].items():
        later = after["operations"][key]
        wanted = before["plan"]["requests"][earlier["label"]]
        if wanted["type"] != "DeleteRouteRequest" and not (
            wanted["type"] == "CreateRouteRequest" and "instance_index" in wanted
        ):
            raise ReplanRejected(
                "Partial route-table conversion needs its infrastructure owner",
                code="predecessor-not-settled",
            )
        mutable = {"status", "cloud_operation_id", "resource_id", "submission_count"}
        if (
            earlier["status"] not in ranks
            or later["status"] not in ranks
            or ranks[later["status"]] < ranks[earlier["status"]]
            or {name: value for name, value in earlier.items() if name not in mutable}
            != {name: value for name, value in later.items() if name not in mutable}
            or any(
                earlier[name] and earlier[name] != later[name]
                for name in ("cloud_operation_id", "resource_id")
            )
            or later.get("submission_count", 1) < earlier.get("submission_count", 1)
        ):
            raise ReplanRejected("Route settlement changed an original request or outcome")


def settle_submitted(sdk: Any, journal, *, observe) -> None:
    """Requires the locked route owner and approval from its coordinating caller."""
    if journal._lock is None:
        raise RuntimeError("Route settlement requires its active owner")
    transaction = journal.state["transaction"]
    plan = transaction["plan"]
    cloud = Cloud(sdk, journal)
    cloud.restore_request = lambda operation: resolve(
        plan["requests"][operation["label"]], plan, cloud.records
    )
    current = _admit(cloud, plan, observe, uncertain=True)
    for operation in cloud.records.values():
        if operation["status"] == "intent" and operation["request_type"] == "DeleteRouteRequest":
            wanted = plan["requests"][operation["label"]]
            route = current.get(wanted["request"]["id"])
            if route is not None and snapshot(route) != wanted["before"]:
                raise ReplanRejected("Route settlement lost exact deletion ownership")
    cloud.settle()
    actual = _admit(cloud, plan, observe)
    for operation in cloud.records.values():
        label = operation["label"]
        wanted = plan["requests"][label]
        if wanted["type"] == "CreateRouteRequest" and "instance_index" in wanted:
            journal.created(
                cloud, label, actual[operation["resource_id"]], index=wanted["instance_index"]
            )
        elif wanted["type"] == "DeleteRouteRequest":
            journal.state["bindings"].pop(wanted["request"]["id"], None)
        if label not in transaction["verified"]:
            transaction["verified"].append(label)
            journal.save()
    _admit(cloud, plan, observe)


@dataclass
class Pending:
    journal: Any
    sdk: Any
    observe: Any
    path: Any
    original: dict
    current: dict
    saved: dict | None

    def execute(self, proof: dict) -> None:
        from .operator_guest import publish, read_file
        from .operator_keys import protected_directory

        if self.journal._lock is None or self.journal.state != self.current:
            raise ReplanRejected("Route settlement owner changed after review")
        if self.saved is not None and self.saved["proof"] != proof:
            raise ReplanRejected("Route settlement approval changed")
        expected = read_file(self.path)
        if (
            (expected is None) != (self.saved is None)
            or expected is not None
            and (
                expected["uid"] != os.getuid()
                or expected["mode"] != 0o600
                or json.loads(expected["content"]) != self.saved
            )
        ):
            raise ReplanRejected("Route settlement approval record changed after review")
        protected_directory(self.path.parent)
        record = {
            "schema": "nebius-vpngw.route-settlement.v1",
            "proof": proof,
            "original": self.original,
            "phase": "settling",
        }

        def save():
            nonlocal expected
            value = {
                "content": json.dumps(record, sort_keys=True),
                "uid": os.getuid(),
                "gid": os.getgid(),
                "mode": 0o600,
            }
            publish(self.path, expected, value)
            expected = value

        save()
        self.journal.archive()
        settle_submitted(self.sdk, self.journal, observe=self.observe)
        record["phase"] = "complete"
        save()


def inspect(command, journal, parent: dict, current: dict, *, sdk, observe) -> Pending | None:
    from .operator_guest import read_file

    transaction = current["transaction"]
    if transaction is None or transaction["phase"] == "complete" or not transaction["operations"]:
        return None
    path = (
        command.root
        / "route-settlements"
        / (
            digest([parent["execution_id"], transaction["approval"], transaction["intent"]])
            + ".json"
        )
    )
    raw = read_file(path)
    saved = None
    if raw is not None:
        if raw["uid"] != os.getuid() or raw["mode"] != 0o600:
            raise ReplanRejected("Route settlement record is not protected")
        saved = json.loads(raw["content"])
        if (
            not isinstance(saved, dict)
            or set(saved) != {"schema", "proof", "original", "phase"}
            or saved["schema"] != "nebius-vpngw.route-settlement.v1"
            or saved["phase"] not in {"settling", "complete"}
        ):
            raise ReplanRejected("Route settlement record is malformed")
        journal.validate(saved["original"])
        require_original(parent, saved["original"], current)
    needs = any(record["status"] != "settled" for record in transaction["operations"].values()) or {
        record["label"] for record in transaction["operations"].values()
    } != set(transaction["verified"])
    if saved is not None and saved["phase"] == "complete":
        if needs:
            raise ReplanRejected("Completed route settlement lost its receipts")
        return None
    if not needs and saved is None:
        return None
    original = saved["original"] if saved is not None else current
    require_original(parent, original, current)
    journal.state = current
    _admit(Cloud(sdk, journal), transaction["plan"], observe, uncertain=True)
    return Pending(journal, sdk, observe, path, original, current, saved)
