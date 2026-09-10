"""Bounded source bundle for remote admission before installed-package imports."""

from __future__ import annotations

import base64
from pathlib import Path

# Exact canonical readers, loaded in dependency order into a fresh guest process.
SOURCES = (
    ("_vpngw_ordinary_operations", "ordinary_operations.py"),
    ("_vpngw_tunnel_state", "tunnel_state.py"),
    ("_vpngw_ordinary_routes", "ordinary_routes.py"),
    ("nebius_vpngw.agent.vm_ha.models", "agent/vm_ha/models.py"),
    ("nebius_vpngw.agent.vm_ha_controller", "agent/vm_ha_controller.py"),
    ("nebius_vpngw.agent.vm_ha_checkpoint", "agent/vm_ha_checkpoint.py"),
    ("nebius_vpngw.agent.vm_ha.mtls", "agent/vm_ha/mtls.py"),
    ("_vpngw_ha_repair", "ha_repair.py"),
    ("_vpngw_ordinary_remote", "deploy/ordinary_remote.py"),
    ("_vpngw_handoff_remote", "deploy/handoff_remote.py"),
)


def streamed_source() -> str:
    root = Path(__file__).parent
    code = "import base64,sys,types\n"
    for package in ("nebius_vpngw", "nebius_vpngw.agent", "nebius_vpngw.agent.vm_ha"):
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
            "exec(compile(_m._SOURCE,'<ha-handoff>','exec'),_m.__dict__)\n"
        )
    return code
