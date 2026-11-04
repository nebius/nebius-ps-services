"""Restore an activation's attachment preimages and clean its temporary resources."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from . import nat_gateway_probes
from .nat_gateway_sdk import NatOperationFailed
from .nat_gateway_state import activation_pending


def restore_activations(manager: Any, sdk_factory: Any) -> None:
    state = manager.state
    assert state is not None
    current_names = {row["instance_id"] for row in manager.rows}
    records = [
        (name, record)
        for name, record in state.value["gateways"].items()
        if (name in current_names and record.get("phase") != "retired")
        or activation_pending(record)
    ]
    failures: dict[str, set[str]] = {name: set() for name, _ in records}
    unresolved: set[tuple[str, str]] = set()
    desired, external = {}, []
    terraform_changed = False
    for name, record in records:
        activation = record.get("activation", {})
        restoring = activation_pending(record)
        if restoring:
            activation["phase"] = "restoration_pending"
            state.save()
        try:
            with sdk_factory(record["project_id"]) as sdk:
                for key, target in record["targets"].items():
                    if not target.get("subnet_id"):
                        continue
                    try:
                        current = (
                            sdk.subnet(
                                target["subnet_id"], record["network_id"]
                            ).spec.route_table_id
                            or ""
                        )
                        transition = (
                            activation.get("attachments", {}).get(key) if restoring else None
                        )
                        route = current
                        if transition:
                            pending = target.get("pending")
                            if target["owner"] == "external" and pending:
                                if not pending.get("operation_id"):
                                    raise RuntimeError("unresolved external submission")
                                # A terminal failure permits restoration too; a timeout does not.
                                with suppress(NatOperationFailed):
                                    sdk.wait_operation(pending["operation_id"])
                                current = (
                                    sdk.subnet(
                                        target["subnet_id"], record["network_id"]
                                    ).spec.route_table_id
                                    or ""
                                )
                            if current not in {transition["before"], transition["desired"]}:
                                raise RuntimeError("attachment drift")
                            route = transition["before"]
                            target["installed"] = current if current != target["original"] else ""
                            target["pending"] = None
                        if target["owner"] == "terraform":
                            manager.overrides[key] = route or None
                            desired[target["subnet_id"]] = route
                            if transition and current != route:
                                terraform_changed = True
                                target["pending"] = {
                                    "desired": route,
                                    "expected": current,
                                    "operation_id": "terraform",
                                }
                        elif transition and current != route:
                            external.append((name, record, target, route))
                    except Exception:
                        unresolved.add((name, key))
                        failures[name].add("attachment read, operation or ownership unresolved")
                state.save()
        except Exception:
            unresolved.update((name, key) for key in record["targets"])
            failures[name].add("attachment inventory unavailable")
    if terraform_changed and not any(failures.values()):
        try:
            manager._apply_routes(desired, "Restore failed NAT activation")
        except Exception:
            for name, _ in records:
                failures[name].add("Terraform restoration pending")
    for name, record, target, route in external:
        try:
            with sdk_factory(record["project_id"]) as sdk:
                manager._attach(sdk, record, target, route)
        except Exception:
            failures[name].add("external restoration pending")
    # Every relevant activation gets a cleanup attempt, even after read/wait/apply failures.
    for name, record in records:
        activation = record.get("activation", {})
        if activation.get("phase") != "restoration_pending":
            continue
        try:
            with sdk_factory(record["project_id"]) as sdk:
                try:
                    nat_gateway_probes.cleanup(
                        sdk, record, save=state.save, assert_held=manager.assert_held
                    )
                except Exception:
                    failures[name].add("probe cleanup pending")
                for key, transition in activation.get("attachments", {}).items():
                    if (name, key) in unresolved:
                        continue  # Never forget a pending request just because its preimage is visible.
                    target = record["targets"][key]
                    try:
                        observed = sdk.subnet(target["subnet_id"], record["network_id"])
                        if (observed.spec.route_table_id or "") != transition[
                            "before"
                        ] or observed.status.route_table.id != transition["effective_before"]:
                            raise RuntimeError(
                                "restoration not converged or inherited routing drifted"
                            )
                        if target["owner"] == "external" and target.get("pending"):
                            raise RuntimeError("restoration request unresolved")
                        target["installed"] = (
                            transition["before"]
                            if transition["before"] != target["original"]
                            else ""
                        )
                        target["pending"] = None
                    except Exception:
                        failures[name].add("restoration not converged")
        except Exception:
            failures[name].add("probe cleanup or restoration read unavailable")
        if not failures[name]:
            activation["phase"] = "restored"
            record["phase"] = activation["previous_phase"]
        state.save()
    messages = sorted({message for errors in failures.values() for message in errors})
    if messages:
        raise RuntimeError("NAT recovery incomplete: " + "; ".join(messages))
