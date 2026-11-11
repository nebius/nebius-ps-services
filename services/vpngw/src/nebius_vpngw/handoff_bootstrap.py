"""Bounded source bundle for remote admission before installed-package imports."""

from __future__ import annotations

import base64
from pathlib import Path

# Exact canonical readers, loaded in dependency order into a fresh guest process.
SOURCES = (
    ("_vpngw_ingress", "ingress_policy.py"),
    ("_vpngw_ordinary_operations", "ordinary_operations.py"),
    ("_vpngw_runtime_migration", "runtime_migration.py"),
    ("nebius_vpngw.faults", "faults.py"),
    ("nebius_vpngw.apply_lock", "apply_lock.py"),
    ("_vpngw_ha_tunnel_policy", "ha_tunnel_policy.py"),
    ("_vpngw_tunnel_state", "tunnel_state.py"),
    ("_vpngw_ordinary_routes", "ordinary_routes.py"),
    ("nebius_vpngw.agent.vm_ha.models", "agent/vm_ha/models.py"),
    ("nebius_vpngw.agent.vm_ha.store", "agent/vm_ha/store.py"),
    ("nebius_vpngw.apply_approval", "apply_approval.py"),
    ("nebius_vpngw.deploy.operator_guest", "deploy/operator_guest.py"),
    ("nebius_vpngw.deploy.operator_keys", "deploy/operator_keys.py"),
    ("nebius_vpngw.deploy.replacement", "deploy/replacement.py"),
    ("nebius_vpngw.deploy.vm_ha_cloud", "deploy/vm_ha_cloud.py"),
    ("nebius_vpngw.deploy.replacement_cloud", "deploy/replacement_cloud.py"),
    ("nebius_vpngw.deploy.ordinary_route_policy", "deploy/ordinary_route_policy.py"),
    ("nebius_vpngw.deploy.ordinary_route_ownership", "deploy/ordinary_route_ownership.py"),
    ("nebius_vpngw.deploy.ordinary_route_plan", "deploy/ordinary_route_plan.py"),
    ("nebius_vpngw.deploy.ordinary_route_conversion", "deploy/ordinary_route_conversion.py"),
    ("nebius_vpngw.ordinary_route_binding", "ordinary_route_binding.py"),
    ("nebius_vpngw.agent.ordinary_route_authority", "agent/ordinary_route_authority.py"),
    ("nebius_vpngw.agent.vm_ha_controller", "agent/vm_ha_controller.py"),
    ("nebius_vpngw.agent.vm_ha_checkpoint", "agent/vm_ha_checkpoint.py"),
    ("nebius_vpngw.agent.vm_ha.mtls", "agent/vm_ha/mtls.py"),
    ("nebius_vpngw.checkpoint_upgrade", "checkpoint_upgrade.py"),
    ("_vpngw_ha_repair", "ha_repair.py"),
    ("nebius_vpngw.package_worker", "package_worker.py"),
    ("_vpngw_python_package_recovery", "python_package_recovery.py"),
    ("_vpngw_ordinary_remote", "deploy/ordinary_remote.py"),
    ("_vpngw_handoff_remote", "deploy/handoff_remote.py"),
)


def streamed_source() -> str:
    root = Path(__file__).parent
    code = "import base64,sys,types\n"
    for package in (
        "nebius_vpngw",
        "nebius_vpngw.agent",
        "nebius_vpngw.agent.vm_ha",
        "nebius_vpngw.deploy",
    ):
        code += (
            f"_pkg=types.ModuleType({package!r}); _pkg.__path__=[]; sys.modules[{package!r}]=_pkg\n"
        )
    for name, relative in SOURCES:
        source = (root / relative).read_bytes()
        if len(source) > 1024 * 1024:
            raise RuntimeError("HA source exceeds bundle limit")
        encoded = base64.b64encode(source).decode()
        package = name.rpartition(".")[0]
        code += (
            f"_m=types.ModuleType({name!r}); _m.__file__={name!r}; _m.__package__={package!r}; "
            f"_m._SOURCE=base64.b64decode({encoded!r}); sys.modules[{name!r}]=_m; "
            f"exec(compile(_m._SOURCE,{relative!r},'exec'),_m.__dict__)\n"
        )
    return code
