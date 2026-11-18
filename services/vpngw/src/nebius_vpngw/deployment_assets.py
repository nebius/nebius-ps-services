"""Canonical deployed service assets shared by normal apply and full repair."""

from __future__ import annotations

HA = (
    ("nebius-vpngw-agent.service", "/etc/systemd/system/nebius-vpngw-agent.service", 0o644),
    (
        "nebius-vpngw-agent-ordering.conf",
        "/etc/systemd/system/nebius-vpngw-agent.service.d/override.conf",
        0o644,
    ),
    (
        "nebius-vpngw-fix-routes.service",
        "/etc/systemd/system/nebius-vpngw-fix-routes.service",
        0o644,
    ),
    (
        "nebius-vpngw-fix-routes.timer",
        "/etc/systemd/system/nebius-vpngw-fix-routes.timer",
        0o644,
    ),
    (
        "nebius-vpngw-health-monitor.service",
        "/etc/systemd/system/nebius-vpngw-health-monitor.service",
        0o644,
    ),
    ("nebius-vpngw-ingress.service", "/etc/systemd/system/nebius-vpngw-ingress.service", 0o644),
    (
        "nebius-vpngw-strongswan.conf",
        "/etc/systemd/system/strongswan.service.d/override.conf",
        0o644,
    ),
    (
        "nebius-vpngw-ufw-ordering.conf",
        "/etc/systemd/system/ufw.service.d/override.conf",
        0o644,
    ),
    ("nebius-vpngw-ingress.py", "/usr/local/lib/nebius-vpngw-ingress.py", 0o644),
    ("setup-vpngw-firewall.sh", "/usr/local/bin/setup-vpngw-firewall.sh", 0o755),
    (
        "nebius-vpngw-vm-ha-peer-firewall.sh",
        "/usr/local/bin/nebius-vpngw-vm-ha-peer-firewall.sh",
        0o755,
    ),
    (
        "nebius-vpngw-esp4-preflight.sh",
        "/usr/local/bin/nebius-vpngw-esp4-preflight.sh",
        0o755,
    ),
    (
        "nebius-vpngw-esp4-preflight.service",
        "/etc/systemd/system/nebius-vpngw-esp4-preflight.service",
        0o644,
    ),
    (
        "nebius-vpngw-vm-ha-guard.service",
        "/etc/systemd/system/nebius-vpngw-vm-ha-guard.service",
        0o644,
    ),
    (
        "nebius-vpngw-vm-ha.service",
        "/etc/systemd/system/nebius-vpngw-vm-ha.service",
        0o644,
    ),
    (
        "nebius-vpngw-vm-ha-rearm.service",
        "/etc/systemd/system/nebius-vpngw-vm-ha-rearm.service",
        0o644,
    ),
    (
        "nebius-vpngw-vm-ha-ordering.conf",
        "/etc/systemd/system/strongswan.service.d/30-vm-ha.conf",
        0o644,
    ),
    (
        "nebius-vpngw-vm-ha-ordering.conf",
        "/etc/systemd/system/frr.service.d/30-vm-ha.conf",
        0o644,
    ),
    (
        "nebius-vpngw-vm-ha-ordering.conf",
        "/etc/systemd/system/nebius-vpngw-agent.service.d/30-vm-ha.conf",
        0o644,
    ),
    (
        "nebius-vpngw-ufw-lock.conf",
        "/usr/lib/tmpfiles.d/nebius-vpngw-ufw-lock.conf",
        0o644,
    ),
)

ORDINARY = (
    (
        "nebius-vpngw-ordinary-agent.service",
        "/etc/systemd/system/nebius-vpngw-agent.service",
        0o644,
    ),
    (
        "nebius-vpngw-ordinary-agent-ordering.conf",
        "/etc/systemd/system/nebius-vpngw-agent.service.d/override.conf",
        0o644,
    ),
    (
        "nebius-vpngw-ordinary-fix-routes.service",
        "/etc/systemd/system/nebius-vpngw-fix-routes.service",
        0o644,
    ),
    ("nebius-vpngw-fix-routes.timer", "/etc/systemd/system/nebius-vpngw-fix-routes.timer", 0o644),
    (
        "nebius-vpngw-ordinary-health-monitor.service",
        "/etc/systemd/system/nebius-vpngw-health-monitor.service",
        0o644,
    ),
    ("nebius-vpngw-ingress.service", "/etc/systemd/system/nebius-vpngw-ingress.service", 0o644),
    (
        "nebius-vpngw-strongswan.conf",
        "/etc/systemd/system/strongswan.service.d/override.conf",
        0o644,
    ),
    ("nebius-vpngw-ufw-ordering.conf", "/etc/systemd/system/ufw.service.d/override.conf", 0o644),
    ("setup-vpngw-firewall.sh", "/usr/local/bin/setup-vpngw-firewall.sh", 0o755),
    ("nebius-vpngw-esp4-preflight.sh", "/usr/local/bin/nebius-vpngw-esp4-preflight.sh", 0o755),
    (
        "nebius-vpngw-esp4-preflight.service",
        "/etc/systemd/system/nebius-vpngw-esp4-preflight.service",
        0o644,
    ),
)


def entries(*, ha: bool) -> tuple[tuple[str, str, int], ...]:
    result = tuple(
        ("nebius_vpngw/systemd/" + name, path, mode)
        for name, path, mode in (HA if ha else ORDINARY)
    )
    result += (
        (
            "nebius_vpngw/systemd/nebius-vpngw-underlay.service",
            "/etc/systemd/system/nebius-vpngw-underlay.service",
            0o644,
        ),
    )
    result += tuple(
        (
            "nebius_vpngw/systemd/nebius-vpngw-underlay-ordering.conf",
            f"/etc/systemd/system/{unit}.service.d/20-underlay.conf",
            0o644,
        )
        for unit in (
            "strongswan",
            "frr",
            "nebius-vpngw-agent",
            *(("nebius-vpngw-vm-ha",) if ha else ()),
        )
    )
    if not ha:
        result += (
            ("nebius_vpngw/ingress_policy.py", "/usr/local/lib/nebius-vpngw-ingress.py", 0o644),
        )
    return result
