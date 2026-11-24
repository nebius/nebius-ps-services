"""Transfer the ordinary enforced-routing envelope under its existing handoff.

The kernel rules and packet guard stay installed. Only the handoff may move their
ownership to the staged HA generation, and every publication is replayable from
the handoff binding written before its first effect.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .agent.enforced_routing import SCHEMA, PolicyRouting
from .ha_tunnel_policy import connection_policies

KEY = "enforced_transfer"
STAGED_ROOT = Path("/etc/nebius-vpngw/vm-ha-staged")


def receipt_bytes(value):
    """Keep published receipts within the canonical private reader's bound."""
    raw = json.dumps(value, sort_keys=True).encode()
    if len(raw) > 1024 * 1024:
        raise RuntimeError("Enforced ownership transfer receipt exceeds its safe size limit")
    return raw


def source_path(repair):
    return repair.STATE.parent / "ordinary/enforced-routing.json"


def policy(path, cluster, config):
    return PolicyRouting(
        path, cluster=cluster, policies=connection_policies(config), run=subprocess.run
    )


def evidence(repair, config, *, binding=None):
    """Read-only admission; a pending handoff retains its original evidence."""
    receipt = (binding or {}).get(KEY)
    path = source_path(repair)
    if receipt is None and not path.exists() and not path.is_symlink():
        return {}
    owner = policy(path, "ordinary", config)
    current = owner.read()
    if receipt is not None:
        if current not in (receipt["before"], receipt["prepared"]):
            if current or receipt["phase"] != "transferred":
                raise RuntimeError("Enforced handoff source changed outside its receipt")
        return receipt["before"]
    if current:
        owner.verify()
    return current


def prepared(holder, payload, repair, ops, *, install):
    """Called inside the holder's writer locks, before any cloud attachment."""
    import yaml

    from . import checkpoint_upgrade
    from .deploy.stopped_upgrade import verify_continuity

    holder.check()
    if not holder.prepared or holder.repairing:
        raise RuntimeError("Enforced conversion requires the prepared ordinary handoff")
    receipt, operation = payload["receipt"], payload["operation"]
    expected = dict(holder.request["ha"], operation_id=operation)
    lock = repair.exact_lock(expected)
    staged = STAGED_ROOT / (expected["generation_id"] + ".yaml")
    config = yaml.safe_load(repair.verified_file(staged, receipt["staged_file_sha256"]))
    ha = config["vm_ha"]
    if (
        ha["cluster_id"] != expected["cluster_id"]
        or ha["node"]["node_id"] != expected["node_id"]
        or ha["generation"]["generation_id"] != expected["generation_id"]
        or ha["runtime_binding"]["route_runtime_id"] != payload["route_runtime_id"]
        or ha["runtime_binding"]["shared_allocation_id"] != payload["allocation_id"]
    ):
        raise RuntimeError("Enforced handoff staged authority differs")
    binding_path = holder.directory.parent / (holder.operation.value["id"] + ".json")
    binding = repair.read_json(binding_path)
    before = holder.request["observed"].get("enforced_routing", {})
    source = policy(source_path(repair), "ordinary", config)
    merged = source.merge(before, source.desired())
    intent = {
        "operation": operation,
        "ha": holder.request["ha"],
        "staged_sha256": receipt["staged_file_sha256"],
        "route_runtime_id": payload["route_runtime_id"],
        "allocation_id": payload["allocation_id"],
        "before": before,
        "prepared": merged,
    }
    saved = binding.get(KEY)
    if saved is not None and {k: v for k, v in saved.items() if k != "phase"} != intent:
        raise RuntimeError("Enforced handoff intent changed")
    if saved is None:
        if not install or source.read() != before:
            raise RuntimeError("Enforced handoff has no unchanged source authority")
        # Check both durable phases before the first publication or guest effect.
        receipt_bytes({**binding, KEY: {**intent, "phase": "transferred"}})
        binding[KEY] = {**intent, "phase": "prepared"}
        repair.publish(binding_path, receipt_bytes(binding))
    if source.read() not in (before, merged):
        raise RuntimeError("Enforced handoff source is not its original or prepared result")
    boot = ops.BOOT.read_text().strip()
    if install:
        repair.cold_guard(state_dir=repair.STATE, boot_id=boot)
        for unit in payload["units"]:
            if not holder.operation.manager.stopped(unit):
                holder.operation.service("stop", unit, timeout=45)
    guard = repair.read_json(repair.STATE / "guard.json")
    if (
        guard.get("guard_boot_id") != boot
        or guard.get("data_plane_mode") not in {"blocked", "passive"}
        or repair.FORWARDING.read_text().strip() != "0"
    ):
        raise RuntimeError("Enforced handoff forwarding fence is absent")
    verify_continuity(
        repair,
        checkpoint_upgrade,
        boot=boot,
        expected=expected,
        allocation_id=payload["allocation_id"],
        installed={},
        unstarted=True,
    )
    if not all(holder.operation.manager.stopped(unit) for unit in payload["units"]):
        raise RuntimeError("Enforced handoff has a running native writer")
    if install:
        source.install()
    source.verify()
    if source.read() != merged:
        raise RuntimeError("Enforced handoff prepared scope differs")
    return {"boot_id": boot, "lock": lock}


def transfer(holder, binding, binding_path, config, repair):
    """Publish HA ownership before prepare_ha_locked; retain source for replay."""
    receipt = binding.get(KEY)
    if receipt is None:
        if policy(source_path(repair), "ordinary", {}).read():
            raise RuntimeError("HA activation lacks its enforced ownership transfer")
        return
    source = policy(source_path(repair), "ordinary", config)
    retired = not source.path.exists() and not source.path.is_symlink()
    if retired and receipt["phase"] != "transferred":
        raise RuntimeError("Enforced conversion lost its source before transfer")
    if not retired and source.read() != receipt["prepared"]:
        raise RuntimeError("Enforced conversion source differs before transfer")
    target = policy(repair.STATE / "enforced-routing.json", config["vm_ha"]["cluster_id"], config)
    if target.path.exists() or target.path.is_symlink():
        if target.read() != receipt["prepared"]:
            raise RuntimeError("Enforced conversion destination already has another owner")
    else:
        if retired:
            raise RuntimeError("Enforced conversion lost its transferred destination")
        repair.publish(
            target.path,
            json.dumps(
                {"schema": SCHEMA, "cluster": target.cluster, "scopes": receipt["prepared"]},
                sort_keys=True,
            ).encode(),
        )
    target.verify()
    binding[KEY] = {**receipt, "phase": "transferred"}
    repair.publish(binding_path, receipt_bytes(binding))


def complete(holder, repair):
    binding_path = holder.directory.parent / (holder.operation.value["id"] + ".json")
    binding = repair.read_json(binding_path)
    receipt = binding.get(KEY)
    if receipt is None:
        return
    if receipt["phase"] != "transferred":
        raise RuntimeError("Enforced conversion ownership has not transferred")
    target = policy(repair.STATE / "enforced-routing.json", receipt["ha"]["cluster_id"], {})
    if target.read() != receipt["prepared"]:
        raise RuntimeError("Enforced conversion destination changed before completion")
    source = policy(source_path(repair), "ordinary", {})
    if source.path.exists() or source.path.is_symlink():
        if source.read() != receipt["prepared"]:
            raise RuntimeError("Enforced conversion source changed before retirement")
        source.path.unlink()
        repair.sync_directory(source.path.parent)
    reverse_receipt = source.path.with_name("enforced-ha-removal.json")
    if reverse_receipt.exists() or reverse_receipt.is_symlink():
        repair.read_json(reverse_receipt)
        reverse_receipt.unlink()
        repair.sync_directory(reverse_receipt.parent)


def preserve_for_ordinary(repair, config, manager, units):
    """Deactivation holds the rearm lock and stops writers before this transfer.

    Keep the old kernel protection and copy its exact ownership before the HA
    state directory is removed. Ordinary apply later retires obsolete scopes
    only after its cloud route transaction commits.
    """
    ha = config["vm_ha"]
    source = policy(repair.STATE / "enforced-routing.json", ha["cluster_id"], config)
    scopes = source.read()
    if not scopes:
        return
    lock = repair.exact_lock(
        {
            "cluster_id": ha["cluster_id"],
            "node_id": ha["node"]["node_id"],
            "generation_id": ha["generation"]["generation_id"],
        }
    )
    if not all(manager.stopped(unit) for unit in units):
        raise RuntimeError("HA deactivation has a running enforcement writer")
    source.verify()
    target = policy(source_path(repair), "ordinary", config)
    receipt_path = target.path.with_name("enforced-ha-removal.json")
    receipt = {"lock": lock, "scopes": scopes}
    if receipt_path.exists() or receipt_path.is_symlink():
        if repair.read_json(receipt_path) != receipt:
            raise RuntimeError("HA deactivation enforcement transfer changed")
    else:
        if target.read():
            raise RuntimeError("HA deactivation has another ordinary enforcement owner")
        repair.publish(receipt_path, receipt_bytes(receipt))
    if target.read() not in ({}, scopes):
        raise RuntimeError("HA deactivation ordinary enforcement destination changed")
    repair.cold_guard(state_dir=repair.STATE, boot_id=repair.BOOT.read_text().strip())
    repair.publish(
        target.path,
        json.dumps(
            {"schema": SCHEMA, "cluster": "ordinary", "scopes": scopes}, sort_keys=True
        ).encode(),
    )
    target.verify()
