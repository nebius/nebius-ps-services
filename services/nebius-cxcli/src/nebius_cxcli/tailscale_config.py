"""Customer-neutral Tailscale intent; secret values are never configuration fields."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

VERSION = "1.104.4"
FIELDS = {
    "stage",
    "tailnet",
    "tags",
    "routes",
    "auth",
    "approval",
    "policy",
    "verification",
    "onboarding",
}


def rows(config: Any) -> list[dict[str, Any]]:
    from .runtime_config import to_plain_data

    data = to_plain_data(config)
    return [
        r
        for r in data.get("infra", {}).get("components", [])
        if r.get("id") == "tailscale-gw" and r.get("enabled")
    ]


def members(row: Mapping[str, Any]) -> tuple[str, ...]:
    count = row.get("inputs", {}).get("replicas", 2)
    if type(count) is not int or count not in {1, 2}:
        raise ValueError("tailscale-gw replicas must be 1 or 2")
    return ("a", "b")[:count]


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def networks(value: Any) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(v, str) for v in value):
        raise ValueError("Tailscale routes must be a nonempty list of private IPv4 prefixes")
    result = []
    private = [ipaddress.IPv4Network(v) for v in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
    for raw in value:
        net = ipaddress.IPv4Network(raw, strict=True)
        if not any(net.subnet_of(parent) for parent in private):
            raise ValueError("Tailscale routes must be inside RFC1918 private networks")
        if str(net) in result:
            raise ValueError("Duplicate Tailscale route")
        result.append(str(net))
    return sorted(result)


def access_rules(rules: Any, routes: list[str]) -> list[dict[str, Any]]:
    if not isinstance(rules, list) or len(rules) > 32:
        raise ValueError("Access rules must be a bounded list")
    approved = [ipaddress.IPv4Network(v) for v in routes]
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {
            "sources",
            "destinations",
            "protocol",
            "ports",
        }:
            raise ValueError("Each access rule requires sources, destinations, protocol and ports")
        for field in ("sources", "destinations"):
            if (
                not isinstance(rule[field], list)
                or not rule[field]
                or any(
                    not isinstance(v, str) or not v or "*" in v or len(v) > 253 for v in rule[field]
                )
            ):
                raise ValueError("Access rules require explicit sources and destinations")
        for dst in rule["destinations"]:
            net = ipaddress.IPv4Network(dst, strict=True)
            if net.version != 4 or not any(net.subnet_of(v) for v in approved):
                raise ValueError("Access destinations must be within advertised routes")
        if rule["protocol"] not in {"tcp", "udp", "icmp"} or not isinstance(rule["ports"], list):
            raise ValueError("Access protocol must be tcp, udp or icmp")
        if rule["protocol"] == "icmp":
            if rule["ports"]:
                raise ValueError("ICMP rules have no ports")
        elif not rule["ports"]:
            raise ValueError("TCP/UDP rules require explicit ports")
        for port in rule["ports"]:
            if not isinstance(port, str) or not re.fullmatch(r"[0-9]{1,5}(?:-[0-9]{1,5})?", port):
                raise ValueError("Ports must be explicit decimal ports or ranges")
            bounds = [int(v) for v in port.split("-")]
            if not all(1 <= v <= 65535 for v in bounds) or bounds[0] > bounds[-1]:
                raise ValueError("Ports must be within 1..65535")
    return rules


def grants(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "src": r["sources"],
            "dst": r["destinations"],
            "ip": ["icmp:*"]
            if r["protocol"] == "icmp"
            else [r["protocol"] + ":" + p for p in r["ports"]],
        }
        for r in access_rules(config.get("policy", {}).get("rules", []), networks(config["routes"]))
    ]


def validate(row: Mapping[str, Any], *, complete: bool = True) -> dict[str, Any]:
    value = row.get("tailscale", {})
    if not isinstance(value, dict) or set(value) - FIELDS:
        raise ValueError("Unsupported tailscale configuration fields")
    members(row)
    if not complete and not value:
        return {}
    if value.get("stage") not in {"infrastructure", "active"}:
        raise ValueError("tailscale.stage must be infrastructure or active")
    active = value["stage"] == "active"
    if (active or value.get("tailnet")) and (
        not isinstance(value.get("tailnet"), str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.@_-]{0,252}", value["tailnet"])
    ):
        raise ValueError("tailscale.tailnet must be the exact expected tailnet name")
    tags = value.get("tags", [])
    if (
        not isinstance(tags, list)
        or (active and not tags)
        or any(
            not isinstance(t, str) or not re.fullmatch(r"tag:[A-Za-z][A-Za-z0-9-]*", t)
            for t in tags
        )
        or len(tags) != len(set(tags))
    ):
        raise ValueError("tailscale.tags requires distinct server tags")
    networks(value.get("routes"))
    auth = value.get("auth", {})
    if not isinstance(auth, dict) or set(auth) - {
        "mode",
        "client_id",
        "client_secret_ref",
        "member_key_refs",
        "member_key_ids",
        "ssh_private_key_file",
        "secretstash",
    }:
        raise ValueError("Tailscale auth accepts references only, never inline keys")
    mode = auth.get("mode", "oauth")
    if mode not in {"oauth", "auth-key"}:
        raise ValueError("tailscale.auth.mode must be oauth or auth-key")
    refs = auth.get("member_key_refs", {})
    if not isinstance(refs, dict) or set(refs) - set(members(row)):
        raise ValueError("Enrollment key references must identify configured members")
    ids = auth.get("member_key_ids", {})
    if (
        not isinstance(ids, dict)
        or set(ids) != set(refs)
        or any(
            not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", v)
            for v in ids.values()
        )
        or len(set(ids.values())) != len(ids)
        or (mode == "auth-key" and refs and set(refs) != set(members(row)))
    ):
        raise ValueError(
            "Administrator keys require distinct member_key_ids beside each configured member_key_ref"
        )
    for reference in [auth.get("client_secret_ref"), *refs.values()]:
        if reference is not None and (
            not isinstance(reference, str)
            or not re.fullmatch(r"(?:env:[A-Z][A-Z0-9_]*|file:.+)", reference)
        ):
            raise ValueError("Secret reference must be env:NAME or file:PATH")
    if value.get("approval", "external") not in {"external", "api"}:
        raise ValueError("tailscale.approval must be external or api")
    stash = auth.get("secretstash")
    if stash is not None:
        from .secretstash import validate_auth

        if (
            not isinstance(stash, dict)
            or set(stash)
            - {
                "project_id",
                "secret_id",
                "version_id",
                "component_instance",
                "secret_name",
                "nebius_auth",
            }
            or not isinstance(stash.get("project_id"), str)
            or not stash["project_id"]
        ):
            raise ValueError(
                "SecretStash requires project_id and an exact secret or component reference"
            )
        external = bool(stash.get("secret_id"))
        linked = bool(stash.get("component_instance") and stash.get("secret_name"))
        if (
            not external
            and not linked
            or bool(stash.get("component_instance")) != bool(stash.get("secret_name"))
        ):
            raise ValueError(
                "SecretStash requires secret_id or component_instance plus secret_name"
            )
        if auth.get("client_id") or auth.get("client_secret_ref"):
            raise ValueError("Select SecretStash or an OAuth environment/file reference")
        for key, val in stash.items():
            if key != "nebius_auth" and (not isinstance(val, str) or not val):
                raise ValueError("SecretStash references must be nonempty strings")
        validate_auth(stash.get("nebius_auth", {}))
    policy = value.get("policy", {})
    if not isinstance(policy, dict) or set(policy) - {"rules", "tag_owners", "auto_approve"}:
        raise ValueError(
            "Tailscale policy requires explicit rules; legacy sources/ports are unsupported"
        )
    if "auto_approve" in policy and type(policy["auto_approve"]) is not bool:
        raise ValueError("tailscale.policy.auto_approve must be boolean")
    owners = policy.get("tag_owners", [])
    if not isinstance(owners, list) or any(
        not isinstance(v, str) or not v or "*" in v for v in owners
    ):
        raise ValueError("Tag owners must list explicit identities")
    access_rules(policy.get("rules", []), networks(value.get("routes")))
    onboarding = value.get("onboarding", {})
    if not isinstance(onboarding, dict) or set(onboarding) - {
        "owner",
        "purpose",
        "environment",
        "admin_requirements",
        "accepted",
    }:
        raise ValueError("Unsupported onboarding settings")
    for field in ("owner", "purpose", "environment"):
        if field in onboarding and (
            not isinstance(onboarding[field], str) or not 1 <= len(onboarding[field]) <= 512
        ):
            raise ValueError("Onboarding owner, purpose and environment must be bounded text")
    requirements = onboarding.get("admin_requirements", [])
    if (
        not isinstance(requirements, list)
        or len(requirements) > 16
        or any(not isinstance(v, str) or not v or len(v) > 512 for v in requirements)
    ):
        raise ValueError("Administrator requirements must be a bounded list")
    accepted = onboarding.get("accepted")
    if accepted is not None and (
        not isinstance(accepted, dict)
        or set(accepted) != {"fingerprint", "reference"}
        or not re.fullmatch(r"[a-f0-9]{64}", str(accepted.get("fingerprint", "")))
        or not isinstance(accepted.get("reference"), str)
        or not 1 <= len(accepted["reference"]) <= 512
    ):
        raise ValueError(
            "Administrator handoff requires an exact fingerprint and approval reference"
        )
    if active and not accepted:
        raise ValueError("Active gateways require an administrator handoff")
    verification = value.get("verification", {})
    if not isinstance(verification, dict) or set(verification) - {
        "targets",
        "required",
        "timeout_seconds",
    }:
        raise ValueError("Unsupported verification settings")
    if (
        type(verification.get("required", False)) is not bool
        or type(verification.get("timeout_seconds", 15)) is not int
        or not 1 <= verification.get("timeout_seconds", 15) <= 30
    ):
        raise ValueError("Verification timeout must be 1..30 seconds and required must be boolean")
    if (
        not isinstance(verification.get("targets", []), list)
        or len(verification.get("targets", [])) > 16
    ):
        raise ValueError("Verification requires a bounded target list")
    if verification.get("required") and not verification.get("targets"):
        raise ValueError("Required client verification needs at least one HTTPS target")
    for target in verification.get("targets", []):
        from urllib.parse import urlsplit

        if (
            not isinstance(target, dict)
            or set(target) - {"url", "ca_file", "min_bytes"}
            or urlsplit(target.get("url", "")).scheme != "https"
        ):
            raise ValueError("Verification targets require HTTPS URLs and optional CA/min_bytes")
        url = urlsplit(target["url"])
        if (
            not url.hostname
            or url.username
            or url.password
            or url.fragment
            or url.query
            or type(target.get("min_bytes", 1)) is not int
            or not 0 <= target.get("min_bytes", 1) <= 1048576
        ):
            raise ValueError(
                "Verification URLs cannot contain credentials, queries or fragments; min_bytes must be bounded"
            )
    return {**value, "tailnet": value.get("tailnet", ""), "tags": tags}


def secret(reference: str, *, base: Path) -> str:
    if reference.startswith("env:"):
        value = os.environ.get(reference[4:], "")
    elif reference.startswith("file:"):
        path = Path(reference[5:]).expanduser()
        if not path.is_absolute():
            path = base / path
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
                or info.st_size > 16384
            ):
                raise RuntimeError("Credential file must be an owner-only regular file")
            with os.fdopen(fd, "r", closefd=False) as stream:
                value = stream.read(16385).strip()
        finally:
            os.close(fd)
    else:
        raise ValueError("Unsupported credential reference")
    if not value or len(value) > 16384:
        raise RuntimeError("Credential reference is empty or oversized")
    return value


def desired(row: Mapping[str, Any], member: str) -> dict[str, Any]:
    cfg = validate(row)
    inputs = row["inputs"]
    return {
        "tailnet": cfg["tailnet"],
        "tags": cfg["tags"],
        "routes": networks(cfg["routes"]),
        "version": inputs.get("tailscale_version", VERSION),
        "hostname": inputs["name"] + "-" + member,
    }
