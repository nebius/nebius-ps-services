"""Execute only the resources frozen by the ordinary provisioning owner.

The command envelope authorizes this child. It cannot create new intent, infer
creation ownership from a name, clear uncertain operations, or activate guests.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..apply_approval import digest
from .apply_command import CommandJournal
from .apply_plan import CommandPlan, Progress
from .provisioning_inventory import inventory, matches, read, snapshot
from .provisioning_journal import ProvisioningJournal, requests_from_plan
from .provisioning_plan import Request, request_types
from .replacement_cloud import Cloud


@dataclass(frozen=True)
class Bootstrap:
    template_sha256: str
    trust_sha256: str
    user_data: str = field(repr=False)


def _parent(command: CommandJournal, plan: dict, key: str) -> dict:
    if command._lock is None:
        raise RuntimeError("Provisioning requires the active command writer")
    current = command.snapshot()
    if current is None or current != command.state:
        raise RuntimeError("Provisioning command changed after admission")
    transaction = current["transaction"]
    approved = CommandPlan.parse(transaction["plan"])
    effect = next((item for item in approved.effects if item.key == key), None)
    if (
        effect is None
        or effect.owner != "ordinary-provisioning"
        or effect.request_sha256 != digest(plan)
        or current["bindings"].get(key) != plan
        or plan["project_id"] != command.project
        or plan["gateway_name"] != command.gateway
        or plan["artifact_sha256"] != approved.artifact_sha256
        or transaction["progress"][key] not in {Progress.STARTED.value, Progress.VERIFIED.value}
    ):
        raise RuntimeError("Provisioning differs from its approved command effect")
    return transaction


def execute(
    sdk: Any,
    journal: ProvisioningJournal,
    command: CommandJournal,
    plan: dict,
    *,
    bootstrap: Callable[[str], Bootstrap],
    key: str = "provisioning",
) -> dict:
    recipes = requests_from_plan(plan)
    parent = _parent(command, plan, key)

    def approved_bootstrap(guest: str) -> str:
        supplied = bootstrap(guest)
        expected = plan["bootstraps"][guest]
        if (
            supplied.template_sha256 != expected["template_sha256"]
            or supplied.trust_sha256 != expected["trust_sha256"]
            or not isinstance(supplied.user_data, str)
            or not supplied.user_data
        ):
            raise RuntimeError("Provisioning bootstrap or trust intent changed")
        return supplied.user_data

    # Reject a changed protected owner input before creating even the first
    # disk. Recheck it when resolving each exact Compute request on replay.
    for guest in plan["bootstraps"]:
        approved_bootstrap(guest)
    with journal:
        if parent["progress"][key] == Progress.VERIFIED.value and (
            not journal.state["transaction"] or journal.state["transaction"]["phase"] != "complete"
        ):
            raise RuntimeError("Completed provisioning has no terminal child receipt")
        journal.begin(plan, approval=parent["approval"], execution_id=parent["execution_id"])

        def resolve(recipe: Request) -> Any:
            value = None
            if recipe.guest is not None:
                value = approved_bootstrap(recipe.guest)
            return recipe.resolve(journal.state["bindings"], bootstrap=value)

        def restore(record: dict) -> Any:
            recipe = next((item for item in recipes if item.key == record["label"]), None)
            if recipe is None:
                raise RuntimeError("Cloud request has no approved provisioning recipe")
            request = resolve(recipe)
            journal.stage(recipe, request)
            return request

        cloud = Cloud(sdk, journal, operation_scope=parent["execution_id"], restore_request=restore)

        def admit(*, uncertain: bool = False) -> None:
            _parent(command, plan, key)
            by_label = {value["label"]: value for value in cloud.records.values()}
            allocation_owners: dict[str, tuple[str, str]] = {}
            uncertain_compute = False
            for recipe in recipes:
                record = by_label.get(recipe.key)
                if recipe.type != "CreateInstanceRequest" or record is None:
                    continue
                if record["status"] in {"intent", "accepted"}:
                    uncertain_compute = True
                if record["status"] != "settled":
                    continue
                request = resolve(recipe)
                for nic in request.spec.network_interfaces:
                    for address in (nic.ip_address, nic.public_ip_address):
                        if address.allocation_id:
                            owner = (record["resource_id"], nic.name)
                            previous = allocation_owners.setdefault(address.allocation_id, owner)
                            if previous != owner:
                                raise RuntimeError("Provisioning allocation has multiple owners")

            def allocation_admission(resource: Any) -> bool:
                """Return whether its assignment is a projected Compute effect."""
                owner = allocation_owners.get(resource.metadata.id)
                state = getattr(resource.status.state, "name", "")
                assignment = resource.status.assignment.network_interface
                if owner is not None:
                    if (
                        state != "ASSIGNED"
                        or (
                            getattr(assignment, "instance_id", None),
                            getattr(assignment, "name", None),
                        )
                        != owner
                    ):
                        raise RuntimeError("Provisioned allocation has a different Compute owner")
                    return True
                if uncertain and uncertain_compute:
                    return True  # Only exact operation settlement is admitted next.
                if state != "ALLOCATED" or resource.status.assignment.__pb2_message__.ByteSize():
                    raise RuntimeError("Provisioning allocation is not proven unassigned")
                return False

            updates = {}
            creations = []
            for recipe in recipes:
                record = by_label.get(recipe.key)
                if record is None:
                    continue
                request = resolve(recipe)
                _, _, method, kind = request_types()[recipe.type]
                if method == "update":
                    updates[request.metadata.id] = (record, request)
                else:
                    creations.append((kind, record, request))
                    if record["status"] == "settled":
                        resource = read(sdk, kind, record["resource_id"])
                        if not matches(resource, request):
                            raise RuntimeError("Created resource differs from its approved request")
                        if kind == "allocation":
                            allocation_admission(resource)
            for identifier, expected in plan["observed"].items():
                resource = read(sdk, expected["kind"], identifier)
                update = updates.get(identifier)
                if update is not None:
                    record, request = update
                    if record["status"] == "settled":
                        if not matches(resource, request):
                            raise RuntimeError("Updated resource differs from its approved request")
                        continue
                    if (
                        uncertain
                        and record["status"] in {"intent", "accepted"}
                        and matches(resource, request)
                    ):
                        # Exact visible state permits settling the original
                        # operation; it never substitutes for its receipt.
                        continue
                actual_snapshot = snapshot(expected["kind"], resource)
                if expected["kind"] == "allocation" and allocation_admission(resource):
                    actual_snapshot["status_sha256"] = expected["status_sha256"]
                if actual_snapshot != expected:
                    raise RuntimeError("Provisioning predecessor changed after approval")
            for scope in plan["inventories"]:
                parent_id = scope["parent"]
                if parent_id.startswith("@"):
                    binding = journal.state["bindings"].get(parent_id[1:])
                    if binding is None:
                        continue  # The earlier table has not acquired a verified creation receipt.
                    parent_id = binding["resource_id"]
                expected_ids = set(scope["ids"])
                unsettled = False
                for kind, record, request in creations:
                    if kind != scope["kind"]:
                        continue
                    resource_parent = (
                        request.spec.network_id if kind == "subnet" else request.metadata.parent_id
                    )
                    if resource_parent != parent_id:
                        continue
                    if record["status"] == "settled":
                        expected_ids.add(record["resource_id"])
                    elif record["status"] in {"intent", "accepted"}:
                        unsettled = True
                if uncertain and unsettled:
                    # Only settle the exact previously dispatched request.
                    # No next effect is admitted until this inventory matches.
                    continue
                actual = {
                    resource.metadata.id for resource in inventory(sdk, scope["kind"], parent_id)
                }
                if actual != expected_ids:
                    raise RuntimeError("Provisioning inventory changed outside approved effects")

        admit(uncertain=True)
        cloud.settle()
        admit()
        for recipe in recipes:
            request = resolve(recipe)
            journal.stage(recipe, request)
            admit()
            _, service, method, _ = request_types()[recipe.type]
            cloud.mutate(recipe.key, request, getattr(service(sdk), method))
            admit()
            journal.verified(recipe, request)
        admit()
        return journal.complete()
