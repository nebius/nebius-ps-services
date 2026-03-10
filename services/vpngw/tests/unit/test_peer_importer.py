from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from nebius_vpngw.cli import app
from nebius_vpngw.config_loader import build_config_from_peer_files
from nebius_vpngw.config_template import DEFAULT_CONFIG_TEMPLATE
from nebius_vpngw.peer_parsers import parse_peer_source


def test_parse_peer_source_supports_csv_keyword_matching(tmp_path: Path) -> None:
    peer_file = tmp_path / "gcp-peer.csv"
    peer_file.write_text(
        "\n".join(
            [
                "connection_name,vendor,routing_mode,peer_asn,tunnel_name,remote_public_ip,psk,inner_local_ip,inner_remote_ip,remote_prefixes",
                "gcp-ha-vpn,gcp,bgp,65014,tunnel-primary,34.157.15.187,csv-psk-1,169.254.10.1,169.254.10.2,10.0.0.0/8",
                "gcp-ha-vpn,gcp,bgp,65014,tunnel-secondary,34.157.140.153,csv-psk-2,169.254.11.1,169.254.11.2,10.0.0.0/8",
            ]
        ),
        encoding="utf-8",
    )

    parsed = parse_peer_source(peer_file)

    assert len(parsed) == 1
    spec = parsed[0]
    assert spec["vendor"] == "gcp"
    assert spec["routing_mode"] == "bgp"
    assert spec["remote_asn"] == 65014
    assert spec["remote_prefixes"] == ["10.0.0.0/8"]
    assert len(spec["tunnels"]) == 2
    assert spec["tunnels"][0]["inner_cidr"] == "169.254.10.0/30"
    assert spec["tunnels"][1]["inner_cidr"] == "169.254.11.0/30"


def test_parse_peer_source_supports_json_keyword_matching(tmp_path: Path) -> None:
    peer_file = tmp_path / "aws-peer.json"
    peer_file.write_text(
        json.dumps(
            {
                "vpnName": "aws-s2s",
                "provider": "aws",
                "routeMode": "static",
                "remoteNetworks": ["192.168.0.0/16"],
                "tunnels": [
                    {
                        "tunnelName": "Tunnel A",
                        "remoteGatewayIp": "198.51.100.10",
                        "preSharedKey": "json-psk",
                        "customerGatewayInsideAddress": "169.254.20.1",
                        "vpnGatewayInsideAddress": "169.254.20.2",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    parsed = parse_peer_source(peer_file)

    assert len(parsed) == 1
    spec = parsed[0]
    assert spec["name"] == "aws-s2s"
    assert spec["vendor"] == "aws"
    assert spec["routing_mode"] == "static"
    assert spec["remote_prefixes"] == ["192.168.0.0/16"]
    tunnel = spec["tunnels"][0]
    assert tunnel["remote_public_ip"] == "198.51.100.10"
    assert tunnel["psk"] == "json-psk"
    assert tunnel["inner_cidr"] == "169.254.20.0/30"


def test_build_config_from_peer_files_fills_schema_defaults_for_missing_fields(tmp_path: Path) -> None:
    peer_file = tmp_path / "branch-office.yaml"
    peer_file.write_text(
        yaml.safe_dump(
            {
                "provider": "cisco",
                "tunnels": [
                    {
                        "peer_public_ip": "198.51.100.50",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    base_cfg = yaml.safe_load(DEFAULT_CONFIG_TEMPLATE)
    built = build_config_from_peer_files(base_cfg, [peer_file])

    assert len(built["connections"]) == 1
    connection = built["connections"][0]
    assert connection["name"] == "branch-office"
    assert connection["vendor"] == "cisco"
    assert connection["routing_mode"] == "static"
    assert connection["remote_prefixes"] == ["192.0.2.0/24"]
    assert connection["bgp"]["enabled"] is False

    tunnel = connection["tunnels"][0]
    assert tunnel["remote_public_ip"] == "198.51.100.50"
    assert tunnel["psk"].startswith("${")
    assert tunnel["inner_cidr"] == "169.254.10.0/30"
    assert tunnel["inner_local_ip"] == "169.254.10.1"
    assert tunnel["inner_remote_ip"] == "169.254.10.2"


def test_create_from_peer_config_generates_bgp_config_from_csv(tmp_path: Path) -> None:
    peer_file = tmp_path / "gcp-peer.csv"
    peer_file.write_text(
        "\n".join(
            [
                "connection_name,vendor,remote_asn,tunnel_name,remote_public_ip,psk,inner_local_ip,inner_remote_ip",
                "gcp-ha-vpn,gcp,65014,nebius-primary,34.157.15.187,cli-psk-1,169.254.10.1,169.254.10.2",
                "gcp-ha-vpn,gcp,65014,nebius-secondary,34.157.140.153,cli-psk-2,169.254.11.1,169.254.11.2",
            ]
        ),
        encoding="utf-8",
    )
    output_file = tmp_path / "generated.config.yaml"

    result = CliRunner().invoke(
        app,
        [
            "create-from-peer-config",
            str(output_file),
            "--peer-config-file",
            str(peer_file),
        ],
    )

    assert result.exit_code == 0, result.stdout

    generated = yaml.safe_load(output_file.read_text(encoding="utf-8"))
    assert len(generated["connections"]) == 1
    connection = generated["connections"][0]
    assert connection["name"] == "gcp-ha-vpn"
    assert connection["vendor"] == "gcp"
    assert connection["routing_mode"] == "bgp"
    assert connection["bgp"]["enabled"] is True
    assert connection["bgp"]["remote_asn"] == 65014
    assert [tunnel["ha_role"] for tunnel in connection["tunnels"]] == ["active", "passive"]
    assert [tunnel["name"] for tunnel in connection["tunnels"]] == [
        "nebius-primary",
        "nebius-secondary",
    ]
