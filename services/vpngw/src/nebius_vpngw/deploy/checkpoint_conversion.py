"""Operator-side transport for guarded, deployment-only checkpoint conversion."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any


def source() -> str:
    """Load canonical readers privately without replacing installed modules."""
    root = Path(__file__).parents[1]
    prefix = "_vpngw_checkpoint"
    result = "import base64,sys,types\n"
    for name in (prefix, prefix + ".agent", prefix + ".agent.vm_ha"):
        result += f"_p=types.ModuleType({name!r});_p.__path__=[];sys.modules[{name!r}]=_p\n"
    for relative in (
        "faults.py",
        "agent/vm_ha/models.py",
        "agent/vm_ha_controller.py",
        "agent/vm_ha_checkpoint.py",
        "checkpoint_upgrade.py",
        "ha_repair.py",
    ):
        name = prefix + "." + relative[:-3].replace("/", ".")
        raw = (root / relative).read_text().replace("from nebius_vpngw.", f"from {prefix}.")
        encoded = base64.b64encode(raw.encode()).decode()
        result += (
            f"_m=types.ModuleType({name!r});_m.__package__={name.rpartition('.')[0]!r};"
            f"sys.modules[{name!r}]=_m;exec(compile(base64.b64decode({encoded!r}),"
            "'<checkpoint-source>','exec'),_m.__dict__)\n"
        )
    result += (
        f"from {prefix} import checkpoint_upgrade as checkpoint_upgrade\n"
        f"from {prefix} import ha_repair as checkpoint_repair\n"
    )
    return result


def context(
    instance: Any, operation_id: str, *, cluster_id: str, generation_id: str | None = None
) -> dict[str, str]:
    return {
        "cluster_id": cluster_id,
        "node_id": instance.vm_ha_node.node_id,
        "generation_id": generation_id or instance.vm_ha_generation.generation_id,
        "operation_id": operation_id,
        "host_id": instance.hostname,
    }


def first_start_authorized(state: Any, instance: Any, binding: Any, artifact: str) -> bool:
    """Authorize seeding only the exact Compute created by this activation."""
    from .vm_ha_lifecycle import (
        VMHALifecycleStatus,
        vm_ha_effective_resource_bindings,
        vm_ha_missing_standby_replacement_effect,
        vm_ha_passive_replacement_binding_key,
        vm_ha_passive_replacement_cycles,
        vm_ha_passive_replacement_effect,
    )

    tx = state.transaction
    node_id, name = instance.vm_ha_node.node_id, instance.hostname
    if (
        state.status is not VMHALifecycleStatus.ACTIVATING
        or tx is None
        or state.cluster_id != binding.cluster_id
        or binding.generation_id != instance.vm_ha_generation.generation_id
        or f"activate-{node_id}" in tx.completed_effects
    ):
        return False
    resources = dict(tx.resource_bindings)
    if (
        resources.get("heartbeat-initial-artifact") != artifact
        or "heartbeat-upgrade-artifact" in resources
    ):
        return False
    member = next((node for node in binding.nodes if node.node_id == node_id), None)
    if (
        member is None
        or not any(node.node_id == node_id and node.instance_name == name for node in state.members)
        or vm_ha_effective_resource_bindings(resources).get(f"compute:{name}") != member.compute_id
    ):
        return False
    cycles = vm_ha_passive_replacement_cycles(resources, name)
    if cycles:
        cycle = cycles[-1]
        key = vm_ha_passive_replacement_binding_key("compute", name, cycle)
        effects = {
            vm_ha_passive_replacement_effect(name, cycle, "create-compute"),
            vm_ha_missing_standby_replacement_effect(name, cycle, "create-compute"),
        }
    else:
        key, effects = f"compute:{name}", {f"provision-{name}-compute"}
    return bool(
        resources.get(key) == member.compute_id and effects.intersection(tx.completed_effects)
    )


def guard_package(
    ssh: Any,
    target: str,
    instance: Any,
    local: dict,
    *,
    artifact: str,
    boot_id: str,
    expected: dict[str, str] | None,
    publish: bool,
) -> None:
    """Preflight before install, then convert before returning package success."""
    from ..ordinary_operations import streamed_bootstrap

    request = {"artifact": artifact, "boot_id": boot_id, "expected": expected, "publish": publish}
    script = (
        source()
        + streamed_bootstrap()
        + "request="
        + repr(request)
        + "\n"
        + r"""
from pathlib import Path
import json,time
import _vpngw_ordinary_operations as ops
repair=checkpoint_repair
upgrade=checkpoint_upgrade
path=repair.STATE/'controller-checkpoint.json'
if repair.BOOT.read_text().strip()!=request['boot_id']:
    raise RuntimeError('checkpoint package boot changed')
if not path.exists() and not path.is_symlink():
    if (repair.STATE/'status.json').exists() or Path('/etc/nebius-vpngw/vm-ha-enabled').exists():
        raise RuntimeError('started HA host checkpoint missing')
    # Package preparation does not initialize state; first-start owner must do so.
    print(json.dumps({'checkpoint':'unstarted'}))
else:
    needed=upgrade.conversion_preflight(expected=request['expected'],artifact=request['artifact'],
        state_dir=repair.STATE,boot_id=request['boot_id'],repair=repair)
    if needed:
        with repair.writer_locks(deadline=time.monotonic()+5):
            manager=ops.Manager(time.monotonic()+15)
            if not all(manager.stopped(unit) for unit in (*ops.MANAGEMENT,*ops.HA_WRITERS)):
                raise RuntimeError('checkpoint conversion writers are active')
            upgrade.conversion_preflight(expected=request['expected'],artifact=request['artifact'],
                state_dir=repair.STATE,boot_id=request['boot_id'],repair=repair)
            if request['publish']:
                upgrade.convert_checkpoint(expected=request['expected'],artifact=request['artifact'],
                    state_dir=repair.STATE,boot_id=request['boot_id'],repair=repair)
    print(json.dumps({'checkpoint':'converted' if needed and request['publish'] else 'admitted'}))
"""
    )
    ssh._inspect_vm_ha_host(target, instance.hostname, local, script=script)
