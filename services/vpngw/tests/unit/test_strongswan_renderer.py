from __future__ import annotations

import os

from nebius_vpngw.agent import strongswan_renderer
from nebius_vpngw.agent.strongswan_renderer import (
    StrongSwanRenderer,
    _wait_for_vici_socket,
    _write_secret_file,
)


def test_collect_tunnel_state_uses_defaults_and_local_prefixes(sample_config: dict) -> None:
    sample_config["connections"][0]["tunnels"].append(
        {
            "name": "tunnel-no-remote-ip",
            "remote_public_ip": None,
            "inner_cidr": "169.254.19.0/30",
            "inner_local_ip": "169.254.19.1",
            "inner_remote_ip": "169.254.19.2",
        }
    )
    sample_config["connections"][0]["tunnels"].append(
        {
            "name": "tunnel-ikev1-disabled",
            "ike_version": 1,
            "remote_public_ip": "198.51.100.11",
            "inner_cidr": "169.254.20.0/30",
            "inner_local_ip": "169.254.20.1",
            "inner_remote_ip": "169.254.20.2",
            "psk": "psk-2",
        }
    )

    tunnels, secrets, interfaces = StrongSwanRenderer()._collect_tunnel_state(sample_config)

    assert [tunnel["name"] for tunnel in tunnels] == ["tunnel-1"]
    assert tunnels[0]["if_id"] == 100
    assert tunnels[0]["local_ts"] == ["169.254.18.224/30", "10.0.0.0/16"]
    assert secrets == [
        {
            "local_id": "%any",
            "remote_id": "198.51.100.10",
            "secret": "test-only-static-routing-validation-psk",
        }
    ]
    assert interfaces == [
        {
            "name": "xfrm0",
            "mode": "static",
            "local_inner_ip": "169.254.18.225",
            "remote_inner_ip": "169.254.18.226",
            "cidr": "169.254.18.224/30",
            "local_public_ip": None,
            "remote_public_ip": "198.51.100.10",
            "remote_prefixes": ["203.0.113.0/24"],
            "if_id": 100,
        }
    ]


def test_write_secret_file_uses_secure_permissions(tmp_path) -> None:
    output_path = tmp_path / "nested" / "swanctl.conf"

    _write_secret_file(output_path, "secret = test\n")

    assert output_path.read_text(encoding="utf-8") == "secret = test\n"
    assert os.stat(output_path).st_mode & 0o777 == 0o600


def test_wait_for_vici_socket_uses_configured_path(tmp_path, monkeypatch) -> None:
    socket_path = tmp_path / "charon.vici"
    monkeypatch.setattr(strongswan_renderer, "VICI_SOCKET", socket_path)

    assert not _wait_for_vici_socket(timeout_seconds=0.0, interval_seconds=0.0)

    socket_path.write_text("", encoding="utf-8")

    assert _wait_for_vici_socket(timeout_seconds=0.01, interval_seconds=0.0)
