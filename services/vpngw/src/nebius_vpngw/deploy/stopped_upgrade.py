"""Read back a completed same-operation stop before resuming package observation."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any


def admitted(state: Any, node: str, artifact: str, *, resumed: bool) -> bool:
    """A completed stop receipt selects readback; an uncertain stop never does."""
    from .vm_ha_lifecycle import VMHALifecycleStatus
    from .vm_ha_upgrade_recovery import release_started

    tx = state.transaction
    if not resumed or state.status is not VMHALifecycleStatus.ACTIVATING or tx is None:
        return False
    members = {member.node_id for member in state.members}
    return bool(
        len(members) == 2
        and node in members
        and dict(tx.resource_bindings).get("heartbeat-upgrade-artifact") == artifact
        and tx.accepted_cloud_operation_id is None
        and tx.accepted_cloud_operation_effect is None
        and tx.observation_guard is None
        and tx.replacement_host_recovery is None
        and not release_started(state)
        and {f"install-apply-lock-{member}" for member in members} <= set(tx.completed_effects)
        and f"stop-upgrade-writers-{node}" in tx.completed_effects
    )


def observe(request: dict, repair: Any, upgrade: Any, manager: Any) -> dict:
    """Called under existing guest writer locks; grants no package/effect receipt."""
    import yaml

    expected = request["expected"]
    lock = repair.exact_lock(expected, state_dir=repair.STATE)
    boot = repair.BOOT.read_text().strip()
    guard = repair.read_json(repair.STATE / "guard.json")
    if (
        boot != request["boot_id"]
        or guard.get("schema") != repair.STATUS_SCHEMA
        or guard.get("guard_boot_id") != boot
        or guard.get("data_plane_mode") not in {"blocked", "passive"}
        or repair.FORWARDING.read_text().strip() != "0"
    ):
        raise RuntimeError("stopped upgrade current-boot fence changed")
    installed = yaml.safe_load(
        repair.verified_file(Path(request["config_path"]), request["config_sha256"])
    )["vm_ha"]
    staged = yaml.safe_load(
        repair.verified_file(Path(request["staged_path"]), request["staged_sha256"])
    )["vm_ha"]
    for value in (installed, staged):
        binding = value["runtime_binding"]
        if (
            value["cluster_id"] != expected["cluster_id"]
            or value["node"]["node_id"] != expected["node_id"]
            or binding["shared_allocation_id"] != request["allocation_id"]
            or {item["node_id"]: item["compute_id"] for item in binding["nodes"]}
            != request["members"]
        ):
            raise RuntimeError("stopped upgrade member identity changed")
    if staged["generation"]["generation_id"] != expected["generation_id"]:
        raise RuntimeError("stopped upgrade staged generation changed")
    for name in (
        "accepted-cloud-operation.json",
        "rearm-cloud-operation.json",
        "standby-restoration-authorization.json",
        "standby-auto-healing-recovery.json",
        "transfer-lineage.json",
        "transfer-progress.json",
        "mtls/inhibition.json",
    ):
        path = repair.STATE / name
        if path.exists() or path.is_symlink():
            raise RuntimeError("stopped upgrade has competing writer authority")
    checkpoint = repair.read_json(repair.STATE / "controller-checkpoint.json")
    upgrade.inspect_checkpoint(checkpoint)
    if (
        checkpoint.get("transfer_continuity") is not None
        or checkpoint.get("repair_attempt") is not None
    ):
        raise RuntimeError("stopped upgrade has competing controller continuity")
    action = checkpoint.get("pending_action")
    if action is not None and not (
        isinstance(action, dict)
        and action.get("kind") == "enter-passive"
        and action.get("boot_id") == boot
        and action.get("target_node_id") == expected["node_id"]
        and action.get("allocation_id") == request["allocation_id"]
        and action.get("generation_id") == installed["generation"]["generation_id"]
        and action.get("digests") == installed["generation"]["digests"]
        and type(checkpoint.get("sequence")) is int
        and checkpoint["sequence"] > 0
        and action.get("ownership_incarnation") == checkpoint.get("ownership_incarnation")
        and action.get("operation_id")
        == f"{boot}:{checkpoint['sequence']}:enter-passive:{expected['node_id']}"
        and action.get("takeover_fence_required") is False
    ):
        raise RuntimeError("stopped upgrade has an unresolved controller effect")
    for unit in request["units"]:
        if unit in request["required_units"] and manager.unit(unit).get("missing"):
            raise RuntimeError("stopped upgrade controller unit is missing")
        if not manager.stopped(unit):
            raise RuntimeError("stopped upgrade native writer is not quiescent")
    return {"state": "stopped", "lock": lock, "boot_id": boot}


def verify(
    ssh: Any,
    instance: Any,
    target: str,
    local: dict,
    *,
    artifact: Any,
    receipt: Any,
    binding: Any,
    operation_id: str,
) -> None:
    from ..ordinary_operations import streamed_bootstrap
    from .apply_diagnostics import ObservationUnavailable
    from .checkpoint_conversion import source
    from .vm_ha_upgrade_recovery import UNITS

    package = dict(artifact.dependency_plans)[instance.hostname]
    request = {
        "expected": {
            "cluster_id": binding.cluster_id,
            "node_id": instance.vm_ha_node.node_id,
            "generation_id": receipt.generation_id,
            "operation_id": operation_id,
        },
        "boot_id": package.observation["boot_id"],
        "config_path": "/etc/nebius-vpngw/config-resolved.yaml",
        "config_sha256": package.observation["config_sha256"],
        "staged_path": f"/etc/nebius-vpngw/vm-ha-staged/{receipt.generation_id}.yaml",
        "staged_sha256": receipt.staged_file_sha256,
        "allocation_id": binding.shared_allocation_id,
        "members": {member.node_id: member.compute_id for member in binding.nodes},
        "units": UNITS,
        "required_units": UNITS[:2],
    }
    encoded = base64.b64encode(Path(__file__).read_bytes()).decode()
    script = (
        source()
        + streamed_bootstrap()
        + f"_stopped={{}};exec(compile(base64.b64decode({encoded!r}),'<stopped-upgrade>','exec'),_stopped)\n"
        + f"request={request!r}\n"
        + """
import json,time
from pathlib import Path
import _vpngw_ordinary_operations as ops
repair=checkpoint_repair
for path in (repair.STATE/'rearm.lock',repair.STATE/'mtls/writer.lock',Path('/run/nebius-vpngw/fix-routes.lock')):
    if not path.is_file() or path.is_symlink():
        raise RuntimeError('stopped upgrade existing writer lock is unavailable')
try:
    with repair.writer_locks(deadline=time.monotonic()+5):
        result=_stopped['observe'](request,repair,checkpoint_upgrade,ops.Manager(time.monotonic()+15))
except RuntimeError as error:
    if str(error)!='HA repair writer did not settle':
        raise
    result={'state':'waiting-writer'}
print(json.dumps(result))
"""
    )
    result = ssh._inspect_vm_ha_host(target, instance.hostname, local, script=script)
    if result == {"state": "waiting-writer"}:
        raise ObservationUnavailable("package-stopped-barrier", "guest-writer-unsettled")
    if (
        set(result) != {"state", "lock", "boot_id"}
        or result["state"] != "stopped"
        or result["boot_id"] != request["boot_id"]
        or any(result["lock"].get(key) != value for key, value in request["expected"].items())
    ):
        raise RuntimeError("stopped upgrade readback differs")
