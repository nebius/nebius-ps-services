"""Reciprocal ordinary deployment files with durable private pair assignments.

The manifest carries references and hashes. Shared credentials live only in the
private assignment file and generated private deployment configurations.
"""

from __future__ import annotations

import fcntl
import ipaddress
import json
import os
import re
import secrets
import stat
import tempfile
from copy import deepcopy
from pathlib import Path

import yaml
from pydantic import ValidationError

from .apply_approval import digest
from .deploy.operator_keys import (
    protected_directory,
    publish_new,
    read_private_file,
    safe_ancestors,
)
from .schema import VPNGatewayConfig

SCHEMA = "nebius-vpngw.pair.v1"
SIDES = ("a", "b")
MANIFEST = "pair.json"
ASSIGNMENTS = ".pair-assignments.json"


def endpoint(side: str, index: int) -> str:
    return "${VPNGW_PAIR_" + side.upper() + "_" + str(index) + "_PUBLIC_IP}"


def _validate(config: dict) -> None:
    # Only endpoint placeholders owned by this generator may be unresolved in
    # the schema view. They are never passed to a deployment or guest renderer.
    view = deepcopy(config)
    for connection in view["connections"]:
        for tunnel in connection["tunnels"]:
            if re.fullmatch(r"\$\{VPNGW_PAIR_[AB]_[01]_PUBLIC_IP\}", tunnel["remote_public_ip"]):
                tunnel["remote_public_ip"] = "192.0.2.1"
    try:
        VPNGatewayConfig.model_validate(view)
    except ValidationError as error:
        fields = sorted({".".join(map(str, item["loc"])) for item in error.errors()})
        # ValidationError includes input values; never propagate shared secrets.
        raise ValueError("Invalid paired configuration fields: " + ", ".join(fields)) from None


def _seed(value: dict, name: str, side: str) -> dict:
    value = deepcopy(value)
    value.pop("region", None)  # Derived loader context is not a configuration field.
    group = value["gateway_group"]
    enabled = bool((group.get("vm_ha") or {}).get("enabled"))
    count = 2 if enabled else 1
    if group.get("instance_count", 1) != count:
        raise ValueError("Pairs require Single-VM or explicit two-member VM-HA seeds")
    group["name"] = f"{name}-{side}"
    group["instance_count"] = count
    group["external_ips"] = []
    if int(group["vm_spec"].get("num_nics", 1)) != 1:
        raise ValueError("Pairs require one public endpoint per member")
    if enabled:
        group["vm_ha"] = {
            "enabled": True,
            "cluster_id": f"{name}-{side}-ha",
            "members": [
                {"node_id": f"{side}{i}", "instance_index": i, "role": role}
                for i, role in enumerate(("active", "passive"))
            ],
        }
    else:
        group["vm_ha"] = {"enabled": False}
    # Original tunnel identities are replaced, so their probes are not seed intent.
    value["gateway"].pop("tunnel_probes", None)
    value["connections"] = []
    return value


def _prefixes(configs: dict[str, dict]) -> None:
    networks = {}
    for side, config in configs.items():
        networks[side] = [
            ipaddress.ip_network(prefix, strict=True)
            for prefix in config["gateway"]["local_prefixes"]
        ]
        if not networks[side] or any(network.version != 4 for network in networks[side]):
            raise ValueError("Paired workload prefixes must be nonempty IPv4 networks")
        if any(
            network.overlaps(ipaddress.ip_network("169.254.0.0/16")) for network in networks[side]
        ):
            raise ValueError("Paired workload prefixes overlap tunnel addressing")
    if any(left.overlaps(right) for left in networks["a"] for right in networks["b"]):
        raise ValueError("Paired workload networks overlap")


def _profiles(value: object) -> dict[str, str | None]:
    if not isinstance(value, dict) or set(value) != set(SIDES):
        raise ValueError("Pair operator profiles are malformed")
    for profile in value.values():
        if profile is not None and (
            not isinstance(profile, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", profile)
        ):
            raise ValueError("Pair operator profile must be a name of at most 128 safe characters")
    return value


def _assign(name: str, configs: dict[str, dict], mode: str, profiles: dict) -> dict:
    identity = digest([name, configs, mode, profiles])
    first = 64512 + int(identity[:8], 16) % 1022
    base = int(identity[8:16], 16) % 16000
    paths: list[dict] = []
    for a in range(configs["a"]["gateway_group"]["instance_count"]):
        for b in range(configs["b"]["gateway_group"]["instance_count"]):
            address = ipaddress.ip_address("169.254.0.0") + 4 * (base + len(paths))
            paths.append(
                {
                    "id": f"{name}-a{a}-b{b}",
                    "a": a,
                    "b": b,
                    "inner_cidr": f"{address}/30",
                    "psk": secrets.token_urlsafe(48),
                }
            )
    return {
        "identity": identity,
        "operator_profiles": profiles,
        "asns": {"a": first, "b": first + 1},
        "paths": paths,
    }


def render(configs: dict[str, dict], assignment: dict, mode: str) -> dict[str, dict]:
    configs = deepcopy(configs)
    # Crypto is selected once, explicitly from side A's defaults, then mirrored.
    crypto = deepcopy(configs["a"]["defaults"].get("crypto", {}))
    ike_version = configs["a"]["defaults"].get("ike_version", 2)
    for side, peer in (("a", "b"), ("b", "a")):
        config = configs[side]
        config["gateway"]["local_asn"] = assignment["asns"][side]
        config["defaults"].update(
            crypto=deepcopy(crypto), ike_version=ike_version, ha_mode="active-passive"
        )
        config["defaults"].setdefault("routing", {})["mode"] = mode
        connection = {
            "name": "paired-region",
            "vendor": "generic",
            "routing_mode": mode,
            "remote_prefixes": deepcopy(configs[peer]["gateway"]["local_prefixes"]),
            "bgp": {
                "enabled": mode == "bgp",
                "remote_asn": assignment["asns"][peer],
                "advertise_local_prefixes": True,
            },
            "tunnels": [],
        }
        for path in assignment["paths"]:
            network = ipaddress.ip_network(path["inner_cidr"])
            local, remote = (1, 2) if side == "a" else (2, 1)
            connection["tunnels"].append(
                {
                    "name": path["id"],
                    "gateway_instance_index": path[side],
                    "local_public_ip_index": 0,
                    "ha_role": "active" if path[peer] == 0 else "passive",
                    "remote_public_ip": endpoint(peer, path[peer]),
                    "psk": path["psk"],
                    "inner_cidr": path["inner_cidr"],
                    "inner_local_ip": str(network[local]),
                    "inner_remote_ip": str(network[remote]),
                }
            )
        config["connections"] = [connection]
        _validate(config)
    return configs


def _bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _publish_exact(path: Path, content: bytes) -> None:
    old = read_private_file(path, required=False)
    if old is None:
        publish_new(path, content)
    elif old != content:
        raise ValueError("Existing pair output differs from its saved assignments")


def create(
    directory: Path,
    name: str,
    left: dict,
    right: dict,
    *,
    mode: str,
    profile_a: str | None = None,
    profile_b: str | None = None,
) -> Path:
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,28}[a-z0-9])?", name):
        raise ValueError("Pair name must be a lowercase gateway name of at most 30 characters")
    if mode not in {"static", "bgp"}:
        raise ValueError("Pair mode must be static or bgp")
    profiles = _profiles({"a": profile_a, "b": profile_b})
    configs = {
        side: _seed(seed, name, side) for side, seed in zip(SIDES, (left, right), strict=True)
    }
    _prefixes(configs)
    if configs["a"]["region_id"] == configs["b"]["region_id"]:
        raise ValueError("Paired deployments require distinct regional bindings")
    directory = directory.expanduser().absolute()
    protected_directory(directory)
    fd = os.open(directory / ".pair.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        saved = read_private_file(directory / ASSIGNMENTS, required=False)
        assignment = json.loads(saved) if saved else _assign(name, configs, mode, profiles)
        if assignment["identity"] != digest([name, configs, mode, profiles]):
            raise ValueError("Pair seed intent changed; use a new output directory")
        if _profiles(assignment.get("operator_profiles")) != profiles:
            raise ValueError("Pair operator profiles differ from saved assignments")
        if (directory / MANIFEST).exists():
            existing, _ = load(directory / MANIFEST)
            if existing["identity"] != assignment["identity"]:
                raise ValueError("Pair manifest identity changed")
            return directory / MANIFEST
        generated = render(configs, assignment, mode)
        # Persist secrets before any output so interrupted creation cannot
        # regenerate path credentials or choose new identities on retry.
        _publish_exact(directory / ASSIGNMENTS, _bytes(assignment))
        deployments = {}
        for side in SIDES:
            filename = f"{side}.config.yaml"
            content = yaml.safe_dump(generated[side], sort_keys=False).encode()
            _publish_exact(directory / filename, content)
            deployments[side] = {
                "config": filename,
                "intent": digest(generated[side]),
                "project": generated[side]["project_id"],
                "region": generated[side]["region_id"],
                "gateway": generated[side]["gateway_group"]["name"],
                "members": generated[side]["gateway_group"]["instance_count"],
                "asn": assignment["asns"][side],
            }
        manifest = {
            "schema": SCHEMA,
            "name": name,
            "identity": assignment["identity"],
            "mode": mode,
            "operator_profiles": profiles,
            "deployments": deployments,
            "paths": [
                {
                    **{key: value for key, value in path.items() if key != "psk"},
                    "credential_ref": ASSIGNMENTS + "#" + path["id"],
                }
                for path in assignment["paths"]
            ],
        }
        _publish_exact(directory / MANIFEST, _bytes(manifest))
        return directory / MANIFEST
    finally:
        os.close(fd)


def load(path: Path, *, resolved: bool = False) -> tuple[dict, dict[str, dict]]:
    """Read-only admission; never resolve cloud resources or repair partial files."""
    path = path.expanduser().absolute()
    safe_ancestors(path)
    metadata = path.parent.lstat()
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise ValueError("Pair directory must be owned by the operator with mode 0700")
    manifest = json.loads(read_private_file(path) or b"null")
    if (
        not isinstance(manifest, dict)
        or set(manifest)
        != {"schema", "name", "identity", "mode", "operator_profiles", "deployments", "paths"}
        or manifest["schema"] != SCHEMA
        or manifest["mode"] not in {"static", "bgp"}
        or set(manifest["deployments"]) != set(SIDES)
    ):
        raise ValueError("Pair manifest is malformed")
    profiles = _profiles(manifest["operator_profiles"])
    configs = {}
    for side in SIDES:
        record = manifest["deployments"][side]
        if (
            set(record) != {"config", "intent", "project", "region", "gateway", "members", "asn"}
            or record["config"] != f"{side}.config.yaml"
            or type(record["members"]) is not int
            or record["members"] not in {1, 2}
        ):
            raise ValueError("Pair deployment reference is malformed")
        raw = read_private_file(path.parent / record["config"])
        try:
            config = yaml.safe_load(raw or b"")
        except yaml.YAMLError:
            raise ValueError("Paired configuration contains invalid YAML") from None
        if not isinstance(config, dict):
            raise ValueError("Paired configuration is malformed")
        _validate(config)
        configs[side] = config
    expected_pairs = {
        (a, b)
        for a in range(manifest["deployments"]["a"]["members"])
        for b in range(manifest["deployments"]["b"]["members"])
    }
    if {(p["a"], p["b"]) for p in manifest["paths"]} != expected_pairs or len(
        manifest["paths"]
    ) != len(expected_pairs):
        raise ValueError("Pair manifest does not cover every owner combination")
    assignment = json.loads(read_private_file(path.parent / ASSIGNMENTS) or b"null")
    if _profiles(assignment.get("operator_profiles")) != profiles:
        raise ValueError("Pair operator profiles differ from saved assignments")
    if assignment["identity"] != manifest["identity"] or [
        {key: value for key, value in item.items() if key != "psk"} for item in assignment["paths"]
    ] != [
        {key: value for key, value in item.items() if key != "credential_ref"}
        for item in manifest["paths"]
    ]:
        raise ValueError("Pair assignments differ from the manifest")
    for side, peer in (("a", "b"), ("b", "a")):
        config = configs[side]
        record = manifest["deployments"][side]
        if (
            config["project_id"] != record["project"]
            or config["region_id"] != record["region"]
            or config["gateway_group"]["name"] != record["gateway"]
            or config["gateway_group"]["instance_count"] != record["members"]
            or config["gateway"]["local_asn"] != record["asn"]
            or len(config["connections"]) != 1
        ):
            raise ValueError("Pair deployment identity changed")
        normalized = deepcopy(config)
        normalized["gateway_group"]["external_ips"] = []
        tunnels = normalized["connections"][0]["tunnels"]
        if len(tunnels) != len(manifest["paths"]):
            raise ValueError("Pair path inventory changed")
        for tunnel, link, stored in zip(
            tunnels, manifest["paths"], assignment["paths"], strict=True
        ):
            if (
                set(link) != {"id", "a", "b", "inner_cidr", "credential_ref"}
                or link["credential_ref"] != ASSIGNMENTS + "#" + link["id"]
            ):
                raise ValueError("Pair path reference is malformed")
            if (
                tunnel["name"] != link["id"]
                or tunnel["gateway_instance_index"] != link[side]
                or tunnel["inner_cidr"] != link["inner_cidr"]
            ):
                raise ValueError("Pair path identity changed")
            if tunnel["psk"] != stored["psk"]:
                raise ValueError("Pair credential differs from its saved assignment")
            ips = configs[peer]["gateway_group"]["external_ips"]
            address = (
                ips[link[peer]][0] if len(ips) > link[peer] and len(ips[link[peer]]) == 1 else None
            )
            placeholder = endpoint(peer, link[peer])
            if tunnel["remote_public_ip"] != address and (
                resolved or tunnel["remote_public_ip"] != placeholder
            ):
                raise ValueError("Pair peer endpoints are unresolved or inconsistent")
            tunnel["remote_public_ip"] = placeholder
        if digest(normalized) != record["intent"]:
            raise ValueError("Existing pair output differs from its saved assignments")
    _prefixes(configs)
    return manifest, configs


def bind_endpoints(path: Path) -> None:
    """Publish reciprocal addresses after BOTH existing network owners prepare.

    The caller owns .pair.lock. A partial publication is resumable: admission
    accepts only an original placeholder or the exact reciprocal allocation.
    """
    manifest, configs = load(path)
    for side in SIDES:
        ips = configs[side]["gateway_group"]["external_ips"]
        if len(ips) != manifest["deployments"][side]["members"] or any(
            len(row) != 1 for row in ips
        ):
            raise ValueError("Prepare every member endpoint in both regions before binding")
        for row in ips:
            address = ipaddress.ip_address(row[0])
            if address.version != 4 or address.is_unspecified or address.is_multicast:
                raise ValueError("Invalid allocated pair endpoint")
    all_addresses = [
        row[0] for config in configs.values() for row in config["gateway_group"]["external_ips"]
    ]
    if len(set(all_addresses)) != len(all_addresses):
        raise ValueError("Pair members must have distinct public endpoints")
    for side, peer in (("a", "b"), ("b", "a")):
        for tunnel, link in zip(
            configs[side]["connections"][0]["tunnels"], manifest["paths"], strict=True
        ):
            tunnel["remote_public_ip"] = configs[peer]["gateway_group"]["external_ips"][link[peer]][
                0
            ]
        target = path.parent / manifest["deployments"][side]["config"]
        content = yaml.safe_dump(configs[side], sort_keys=False).encode()
        old = read_private_file(target)
        if old == content:
            continue
        fd, name = tempfile.mkstemp(prefix=".pair-config-", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if read_private_file(target) != old:
                raise RuntimeError("Pair configuration changed during endpoint binding")
            os.replace(temporary, target)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)
    load(path, resolved=True)
