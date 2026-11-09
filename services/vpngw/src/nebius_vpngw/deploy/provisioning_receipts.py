"""Derive resource identities only from this command's successful child receipts."""

from __future__ import annotations

from typing import Any

from ..apply_approval import digest
from .apply_command import CommandJournal
from .apply_plan import CommandPlan, Progress
from .provisioning_inventory import matches, read
from .provisioning_journal import ProvisioningJournal, requests_from_plan
from .replacement_cloud import message_digest


def completed(command: CommandJournal, child: ProvisioningJournal) -> dict:
    parent, state = command.snapshot(), child.snapshot()
    if (
        parent is None
        or state is None
        or command._lock is None
        or parent != command.state
        or child.project != command.project
        or child.gateway != command.gateway
    ):
        raise RuntimeError("Derived effect requires the active exact command writer")
    root, transaction = parent["transaction"], state["transaction"]
    if root is None or transaction is None:
        raise RuntimeError("Derived effect has no provisioning authority")
    plan = CommandPlan.parse(root["plan"])
    effect = next((item for item in plan.effects if item.key == "provisioning"), None)
    receipt = {"plan": digest(transaction["plan"]), "bindings": digest(state["bindings"])}
    if (
        effect is None
        or effect.owner != "ordinary-provisioning"
        or root["progress"]["provisioning"] != Progress.VERIFIED.value
        or transaction["phase"] != "complete"
        or transaction["approval"] != root["approval"]
        or transaction["execution_id"] != root["execution_id"]
        or parent["bindings"].get("provisioning") != transaction["plan"]
        or effect.request_sha256 != receipt["plan"]
        or root["receipts"]["provisioning"] != {"owner": effect.owner, "sha256": digest(receipt)}
    ):
        raise RuntimeError("Derived effect differs from successful provisioning receipts")
    return state


def compute(sdk: Any, command: CommandJournal, child: ProvisioningJournal, guest: str) -> Any:
    """Prove current Compute specification and original private bootstrap by hash."""
    state = completed(command, child)
    recipes = requests_from_plan(state["transaction"]["plan"])
    recipe = next((item for item in recipes if item.guest == guest), None)
    if recipe is None:
        raise RuntimeError("Derived guest has no approved Compute creation")
    binding = state["bindings"][recipe.key]
    resource = read(sdk, "compute", binding["resource_id"])
    request = recipe.resolve(state["bindings"], bootstrap=resource.spec.cloud_init_user_data)
    if message_digest(request) != binding["request_sha256"] or not matches(resource, request):
        raise RuntimeError("Created Compute changed after its successful provisioning receipt")
    return resource
