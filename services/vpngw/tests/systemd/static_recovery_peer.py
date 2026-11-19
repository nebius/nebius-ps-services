"""Plain site-B IPsec responder. This fixture imports no gateway implementation.

Two endpoint addresses model independent primary and backup tunnels to one site.
Native strongSwan manages return routes. There is no peer-side selection agent.
"""

from __future__ import annotations

import json

import yaml


def peer_interfaces(cfg: dict) -> list[dict]:
    interfaces = []
    for connection in cfg["connections"]:
        for tunnel in connection["tunnels"]:
            index = len(interfaces)
            interfaces.append(
                {
                    "name": f"xfrm{index}",
                    "if_id": 100 + index,
                    "mode": "static",
                    "remote_public_ip": tunnel["remote_public_ip"],
                    "local_inner_ip": tunnel["inner_local_ip"],
                    "cidr": tunnel["inner_cidr"],
                }
            )
    return interfaces


def peer_config(cfg: dict) -> str:
    """Render only standard swanctl syntax, independently of product rendering."""
    connections = []
    secrets = []
    interfaces = peer_interfaces(cfg)
    index = 0
    for connection in cfg["connections"]:
        for tunnel in connection["tunnels"]:
            interface = interfaces[index]
            local_ts = [tunnel["inner_cidr"], *cfg["gateway"]["local_prefixes"]]
            name = tunnel["name"]
            local, remote = tunnel["local_public_ip"], tunnel["remote_public_ip"]
            connections.append(f"""  {name} {{
    version = 2
    local_addrs = {local}
    remote_addrs = {remote}
    proposals = aes256-sha256-modp2048
    unique = no
    keyingtries = 1
    dpd_delay = 1s
    local {{
      auth = psk
      id = {local}
    }}
    remote {{
      auth = psk
      id = {remote}
    }}
    children {{
      {name} {{
        local_ts = {", ".join(local_ts)}
        remote_ts = {", ".join(cfg["_fixture_remote_ts"])}
        esp_proposals = aes256-sha256-modp2048
        if_id_in = {interface["if_id"]}
        if_id_out = {interface["if_id"]}
        start_action = none
        dpd_action = clear
        close_action = none
      }}
    }}
  }}""")
            secrets.append(f"""  ike-{index} {{
    id-1 = {local}
    id-2 = {remote}
    secret = {json.dumps(tunnel["psk"])}
  }}""")
            index += 1
    return (
        "connections {\n"
        + "\n".join(connections)
        + "\n}\nsecrets {\n"
        + "\n".join(secrets)
        + "\n}\n"
    )


def install_peer(endpoint) -> None:
    cfg = yaml.safe_load(endpoint.config.read_text())
    root = endpoint.config.parent / "plain-peer.conf"
    root.write_text(peer_config(cfg))
    root.chmod(0o600)
    endpoint.command("cp", str(root), "/etc/swanctl/swanctl.conf")
    endpoint.command("swanctl", "--load-all", "--noprompt")
