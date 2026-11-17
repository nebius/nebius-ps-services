"""Typed diagnostic views of existing owners, never new recovery authority."""

from __future__ import annotations

import hashlib
import re

SCHEMAS = {"nebius-vpngw/vm-ha-lifecycle-v4", "nebius-vpngw.apply-command.v1"}


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def category(effect: str) -> str:
    for prefix, name in (
        ("release-", "release"),
        ("activate-", "activation"),
        ("prepare-", "preparation"),
        ("verify-", "verification"),
        ("provision-", "provisioning"),
        ("lock-", "locking"),
        ("stop-", "stopping"),
        ("fence-", "fencing"),
    ):
        if effect.startswith(prefix):
            return name
    return "unknown"


def project(value: dict, config: dict) -> dict | None:
    schema = value.get("schema")
    if schema not in SCHEMAS:
        return None
    result = {
        "schema": "nebius-vpngw.support-checkpoint.v1",
        "effects": [],
        "omitted_effects": 0,
    }
    if schema == "nebius-vpngw/vm-ha-lifecycle-v4":
        from ..deploy.vm_ha_lifecycle import VMHALifecycleState, vm_ha_has_unsettled_replacement

        state = VMHALifecycleState.from_dict(value)
        group = config["gateway_group"]
        if (
            state.project_id != config["project_id"]
            or state.gateway_name != group["name"]
            or state.cluster_id != (group.get("vm_ha") or {}).get("cluster_id")
        ):
            raise ValueError("Lifecycle diagnostic scope differs")
        result.update(source="lifecycle", phase=state.status.value)
        tx = state.transaction
        if tx is None:
            return result
        bindings = dict(tx.resource_bindings)
        initial, upgrade = (
            bindings.get("heartbeat-" + kind + "-artifact") for kind in ("initial", "upgrade")
        )
        result.update(
            origin="conflicting"
            if initial and upgrade
            else "initial"
            if initial
            else "upgrade"
            if upgrade
            else "unknown",
            operation_sha256=fingerprint(tx.operation_id),
            checkpoint_sha256=fingerprint(tx.checkpoint),
            checkpoint_category=category(
                tx.checkpoint.removeprefix("before-").removeprefix("after-")
            ),
            accepted_cloud_operation=tx.accepted_cloud_operation_id is not None,
            observation_guard=tx.observation_guard is not None,
            replacement_pending=vm_ha_has_unsettled_replacement(state),
            configuration_digest=tx.desired_state_digest,
            approval_sha256=tx.approval_digest,
        )
        for key, digest in (
            ("initial_artifact_sha256", initial),
            ("upgrade_artifact_sha256", upgrade),
        ):
            if digest is not None:
                if not re.fullmatch(r"[a-f0-9]{64}", digest):
                    raise ValueError("Invalid checkpoint artifact")
                result[key] = digest
        effects = [(effect, "verified") for effect in tx.completed_effects]
        if tx.pending_effect:
            effects.append((tx.pending_effect, "started"))
        result["effects"] = [
            {
                "effect_sha256": fingerprint(effect),
                "category": category(effect),
                "progress": progress,
            }
            for effect, progress in effects[:256]
        ]
        result["omitted_effects"] = max(0, len(effects) - 256)
        result["unknown_effects"] = sum(category(effect) == "unknown" for effect, _ in effects)
        result["settlement_present"] = "corrective-release-authority" in bindings
    else:
        from ..deploy.apply_command import CommandJournal
        from ..deploy.apply_plan import CommandPlan

        CommandJournal(config["project_id"], config["gateway_group"]["name"]).validate(value)
        tx = value["transaction"]
        result.update(source="command", phase=tx["phase"] if tx else "idle")
        if tx is None:
            return result
        plan = CommandPlan.parse(tx["plan"])
        result.update(
            selected_artifact_sha256=plan.artifact_sha256,
            configuration_digest=plan.configuration_sha256,
            approval_sha256=tx["approval"],
            operation_sha256=fingerprint(tx["execution_id"]),
        )
        owners = {
            "ha-lifecycle",
            "managed-credentials",
            "ssh-bootstrap",
            "security-groups",
            "ordinary",
            "ordinary-runtime",
            "ordinary-provisioning",
            "ordinary-routes",
            "ordinary-route-reservation",
            "ordinary-route-runtime",
            "replacement",
        }
        result["effects"] = [
            {
                "category": effect.owner if effect.owner in owners else "unknown",
                "effect_sha256": fingerprint(effect.key),
                "progress": tx["progress"][effect.key],
                "request_sha256": effect.request_sha256,
                "observation_sha256": effect.observation_sha256,
                "receipt_present": effect.key in tx["receipts"],
            }
            for effect in plan.effects[:256]
        ]
        result["omitted_effects"] = max(0, len(plan.effects) - 256)
        result["unknown_effects"] = sum(effect.owner not in owners for effect in plan.effects)
    return result
