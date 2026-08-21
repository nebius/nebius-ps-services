#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${1:-/etc/nebius-vpngw/config-resolved.yaml}"

if [[ $# -gt 1 ]]; then
  printf 'Usage: %s [CONFIG_PATH]\n' "${0##*/}" >&2
  exit 2
fi

if [[ ! -r "$CONFIG_PATH" ]]; then
  printf 'VM-HA peer firewall config is not readable: %s\n' "$CONFIG_PATH" >&2
  exit 1
fi

rule="$(python3 - "$CONFIG_PATH" <<'PY'
import ipaddress
import re
import sys

import yaml


def endpoint(value: object) -> tuple[str, int]:
    if not isinstance(value, str) or ":" not in value:
        raise ValueError("peer endpoint is invalid")
    host_text, port_text = value.rsplit(":", 1)
    host = ipaddress.ip_address(host_text)
    if not isinstance(host, ipaddress.IPv4Address):
        raise ValueError("peer endpoint must use IPv4")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise ValueError("peer endpoint port is invalid")
    return str(host), port


with open(sys.argv[1], encoding="utf-8") as stream:
    payload = yaml.safe_load(stream)
if not isinstance(payload, dict):
    raise ValueError("resolved config is invalid")
vm_ha = payload.get("vm_ha")
if vm_ha is None:
    raise SystemExit(0)
if not isinstance(vm_ha, dict):
    raise ValueError("VM-HA config is invalid")
node = vm_ha.get("node")
binding = vm_ha.get("runtime_binding")
nodes = binding.get("nodes") if isinstance(binding, dict) else None
if not isinstance(node, dict) or not isinstance(nodes, list) or len(nodes) != 2:
    raise ValueError("VM-HA runtime binding is incomplete")
node_id = node.get("node_id")
local = [item for item in nodes if isinstance(item, dict) and item.get("node_id") == node_id]
peer = [item for item in nodes if isinstance(item, dict) and item.get("node_id") != node_id]
if len(local) != 1 or len(peer) != 1:
    raise ValueError("VM-HA runtime binding node identity is invalid")
local_host, local_port = endpoint(local[0].get("peer_endpoint"))
peer_host, peer_port = endpoint(peer[0].get("peer_endpoint"))
if local_port != peer_port:
    raise ValueError("VM-HA peer ports do not match")
interface = local[0].get("network_interface_name")
if not isinstance(interface, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", interface) is None:
    raise ValueError("VM-HA network interface is invalid")
print("\t".join((interface, peer_host, local_host, str(local_port))))
PY
)"

# Ordinary configs have no VM-HA runtime binding and need no peer rule.
if [[ -z "$rule" ]]; then
  exit 0
fi

IFS=$'\t' read -r interface peer_host local_host local_port <<<"$rule"
if [[ -z "$interface" || -z "$peer_host" || -z "$local_host" || -z "$local_port" ]]; then
  printf 'VM-HA peer firewall rule is incomplete\n' >&2
  exit 1
fi

ufw allow in on "$interface" proto tcp from "$peer_host" to "$local_host" \
  port "$local_port" comment "VM-HA peer mTLS"
