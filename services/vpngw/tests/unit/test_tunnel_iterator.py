from __future__ import annotations

from nebius_vpngw.agent.tunnel_iterator import get_tunnel_interface_mapping, iter_active_tunnels


def test_iter_active_tunnels_skips_disabled_tunnels(sample_config: dict) -> None:
    sample_config["connections"][0]["tunnels"].append(
        {
            "name": "tunnel-disabled",
            "ha_role": "disable",
            "remote_public_ip": "198.51.100.11",
        }
    )
    sample_config["connections"][0]["tunnels"].append(
        {
            "name": "tunnel-passive",
            "ha_role": "passive",
            "remote_public_ip": "198.51.100.12",
        }
    )

    active = list(iter_active_tunnels(sample_config))

    assert [(idx, iface, tun["name"]) for idx, iface, _conn, tun in active] == [
        (0, "xfrm0", "tunnel-1"),
        (1, "xfrm1", "tunnel-passive"),
    ]


def test_get_tunnel_interface_mapping_uses_generated_name_when_missing(sample_config: dict) -> None:
    sample_config["connections"][0]["tunnels"] = [
        {"remote_public_ip": "198.51.100.10"},
        {"name": "named-tunnel", "remote_public_ip": "198.51.100.11"},
    ]

    mapping = get_tunnel_interface_mapping(sample_config)

    assert mapping["tunnel0"] == (0, "xfrm0")
    assert mapping["named-tunnel"] == (1, "xfrm1")
