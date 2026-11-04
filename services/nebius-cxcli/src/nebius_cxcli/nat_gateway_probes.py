"""Sequential per-subnet data-path probes with durable, exact-resource cleanup."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from .nat_gateway import verification_enabled
from .nat_gateway_probe_resources import ProbeResources
from .nat_gateway_probe_ssh import PROBE_USER, ProbeSSH
from .nat_gateway_ssh import public_key_identity
from .nat_gateway_state import assert_quota_resolved
from .provider_options import ProviderOptionLookup
from .quota_checks import QuotaRequirement, assess_live_quota_requirements
from .ssh_host_identity import generate_host_identity


def endpoint_for(routes: list[dict[str, Any]], addresses: list[str]) -> str:
    exceptions = [
        ipaddress.IPv4Network(r["destination_cidr"])
        for r in routes
        if r["destination_cidr"] != "0.0.0.0/0"
    ]
    for address in sorted(set(addresses)):
        ip = ipaddress.IPv4Address(address)
        if ip.is_global and not any(ip in network for network in exceptions):
            return str(ip)
    raise RuntimeError(
        "No probe endpoint follows the new default route; retained more-specific routes cover every resolved endpoint"
    )


def endpoint_addresses() -> list[str]:
    # A child bounds system DNS resolution without changing the parent's socket defaults.
    script = 'import json,socket; print(json.dumps(sorted({r[4][0] for r in socket.getaddrinfo("api.ipify.org",443,socket.AF_INET,socket.SOCK_STREAM)})))'
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=15, check=True
    )
    return json.loads(result.stdout)


def probe_shape(sdk: Any, payload: dict[str, Any]) -> dict[str, Any]:
    lookup = ProviderOptionLookup()
    args = {"project_id": sdk.project_id, "cpu_only": True}
    platforms = lookup.resolve(
        provider="compute_platforms", args=args, payload=payload, field_path="nat.probe.platform"
    )
    if lookup.last_error():
        raise RuntimeError("Probe platform inventory is incomplete")
    candidates = []
    for platform_choice in platforms:
        presets = lookup.resolve(
            provider="compute_platform_presets",
            args={**args, "platform": platform_choice.value},
            payload=payload,
            field_path="nat.probe.preset",
        )
        if lookup.last_error():
            raise RuntimeError("Probe preset inventory is incomplete")
        for preset_choice in presets:
            resources = sdk.cpu_preset(platform_choice.value, preset_choice.value)
            candidates.append((resources["vcpu_count"], platform_choice.value, preset_choice.value))
    for vcpu, platform, preset in sorted(candidates):
        images = sdk.image_choices(payload["client_info"]["nebius"]["region_id"], platform, preset)
        if images:
            image = images[0]
            return {
                "vcpu": vcpu,
                "platform": platform,
                "preset": preset,
                "image": image["id"],
                "disk_gib": max(20, image["minimum_gib"]),
            }
    raise RuntimeError("No live CPU preset and compatible image is available for the private probe")


def cloud_init(config: dict[str, Any], *, public_key: str, host_identity: dict[str, str]) -> str:
    source = Path(__file__).with_name("nat_gateway_probe_guest.py").read_text()
    return "#cloud-config\n" + yaml.safe_dump(
        {
            "package_update": False,
            "package_upgrade": False,
            "ssh_pwauth": False,
            "disable_root": True,
            "ssh_keys": host_identity,
            "ssh_genkeytypes": ["ed25519"],
            "ssh_publish_hostkeys": {"enabled": False},
            "users": [
                {
                    "name": PROBE_USER,
                    "lock_passwd": True,
                    "shell": "/bin/sh",
                    "sudo": None,
                    "ssh_authorized_keys": [
                        "restrict " + " ".join(public_key_identity(public_key))
                    ],
                }
            ],
            "write_files": [
                {
                    "path": "/usr/local/sbin/cxcli-nat-probe.py",
                    "permissions": "0700",
                    "content": source,
                },
                {
                    "path": "/var/lib/cxcli-nat-probe.json",
                    "permissions": "0600",
                    "content": json.dumps(config),
                },
            ],
            "runcmd": [["timeout", "150", "python3", "/usr/local/sbin/cxcli-nat-probe.py"]],
        }
    )


def preflight(
    sdk: Any, row: dict[str, Any], record: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any] | None:
    if not row["routing"]["enabled"] or not verification_enabled(row):
        return None
    shape = probe_shape(sdk, payload)
    region = payload["client_info"]["nebius"]["region_id"]
    demands = {
        "compute.instance.count": 1,
        "compute.instance.non-gpu.vcpu": shape["vcpu"],
        "compute.disk.count": 1,
        "vpc.allocation.count": 1,
        "compute.disk.size.network-ssd": shape["disk_gib"] * 1024**3,
    }
    requirements = [
        QuotaRequirement(
            "nat-gateway",
            row["instance_id"],
            "Temporary private NAT probe",
            name,
            region,
            amount,
            "One sequential probe",
        )
        for name, amount in demands.items()
    ]
    scope = payload["client_info"]["nebius"]
    report = assess_live_quota_requirements(
        tenant_id=scope["tenant_id"],
        project_id=sdk.project_id,
        region_id=region,
        requirements=requirements,
        context="NAT probe",
    )
    assert_quota_resolved(report)
    addresses = endpoint_addresses()
    endpoints = {}
    for key, target in record["targets"].items():
        sdk.require_private_capacity(target["subnet_id"], record["network_id"])
        endpoints[key] = endpoint_for(
            row["routing"]["policies"][target["policy"]]["routes"], addresses
        )
    return {"shape": shape, "endpoints": endpoints}


def run(
    sdk: Any, record: dict[str, Any], prepared: dict[str, Any], *, save: Any, assert_held: Any
) -> None:
    activation = record["activation"]
    access: ProbeSSH = prepared["ssh"]
    for key, target in record["targets"].items():
        probe = activation.setdefault("probes", {}).get(key)
        if probe is not None:
            # Resumes clean unfinished resources; never re-use old success for a new activation.
            resources = ProbeResources(sdk, probe, save, assert_held)
            resources.cleanup()
            if probe.get("result", {}).get("ok") is not True:
                raise RuntimeError(
                    "Prior NAT probe is incomplete; activation must be restored before retry"
                )
            continue
        assert_held()
        host_identity = generate_host_identity()
        probe = {
            "subnet_id": target["subnet_id"],
            "shape": prepared["shape"],
            "started_at": time.time(),
            "resources": {},
            "cleanup": "pending",
            "host_public_key": host_identity["ed25519_public"],
            "config": {
                "run_id": uuid.uuid4().hex,
                "activation": activation["id"],
                "program_sha256": hashlib.sha256(
                    Path(__file__).with_name("nat_gateway_probe_guest.py").read_bytes()
                ).hexdigest(),
                "endpoint_ip": prepared["endpoints"][key],
                "expected_ip": str(ipaddress.IPv4Interface(record["outputs"]["public_ip"]).ip),
            },
        }
        activation["probes"][key] = probe
        save()  # Durable intent precedes every resource effect.
        resources = ProbeResources(sdk, probe, save, assert_held)
        try:
            sdk.require_private_capacity(target["subnet_id"], record["network_id"])
            resources.provision(
                cloud_init(
                    probe["config"], public_key=access.public_key, host_identity=host_identity
                )
            )
            host_identity.clear()
            probe["result"] = access.wait(resources, probe, save=save, assert_held=assert_held)
            save()
            if not probe["result"]["ok"]:
                raise RuntimeError(
                    "Private-subnet probe did not egress through the NAT gateway's public IP"
                )
        finally:
            host_identity.clear()
            resources.cleanup()


def cleanup(sdk: Any, record: dict[str, Any], *, save: Any, assert_held: Any) -> None:
    for probe in record.get("activation", {}).get("probes", {}).values():
        if probe.get("cleanup") != "complete":
            ProbeResources(sdk, probe, save, assert_held).cleanup()
