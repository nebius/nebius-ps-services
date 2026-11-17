"""Explicit topology accounting shared by the native and package harnesses."""

from __future__ import annotations

SHARED = (
    "test_ssh_bootstrap.py",
    "test_strongswan_sessions.py",
    "test_tunnel_host_packets.py",
    "test_ingress_packets.py",
    "test_runtime_modernization.py",
    "test_release_asset_retirement_native.py",
)
ORDINARY = ("test_ordinary_systemd_jobs.py", "test_ordinary_transport.py")
HA = (
    "test_ha_tunnel_native.py", "test_ha_daemon_reload_native.py",
    "test_repair_idle_handoff_native.py",
)


def topologies(selection: str) -> tuple[str, ...]:
    if selection not in {"all", "ha", "ordinary"}:
        raise ValueError("Unknown qualification topology")
    return ("ordinary", "ha") if selection == "all" else (selection,)


def native_files(selection: str) -> tuple[str, ...]:
    selected = topologies(selection)
    # Preserve the original full-lane ordering for comparable failure evidence.
    return (
        SHARED[0],
        *(ORDINARY if "ordinary" in selected else ()),
        SHARED[1],
        *(HA if "ha" in selected else ()),
        *SHARED[2:],
    )


def package_cases(versions: list[str], latest: str, selection: str) -> list[str]:
    return [
        f"{version}-{mode}-{topology}" + (f"-interrupt-{fault}" if fault else "")
        for version in versions
        for mode in ("static", "bgp")
        for fault in (
            (None, "native-unpack" if mode == "static" else "product-wheel")
            if version == latest
            else (None,)
        )
        for topology in topologies(selection)
    ]
