"""Generated Single-VM runtime authority; contains credential references only."""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .apply_approval import digest
from .deploy.operator_guest import read_file
from .deploy.ordinary_route_policy import Policy
from .deploy.replacement import metadata_only

SCHEMA = "nebius-vpngw.ordinary-route-binding.v1"
PATH = Path("/etc/nebius-vpngw/ordinary-route-binding.json")


def credential_path(*, generation: str, node: str, sha256: str) -> str:
    if not all(
        re.fullmatch(r"[a-f0-9]{64}", value) for value in (generation, sha256)
    ) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}", node):
        raise ValueError("Invalid ordinary route credential reference")
    return f"/etc/nebius-vpngw/ordinary-route-credentials/{generation}/{node}/{sha256}/nebius-credentials.json"


def validate_intent(value: dict) -> dict:
    """Validate route scope independently of credential enrollment completion."""
    metadata_only(value)
    try:
        if (
            set(value)
            != {
                "schema",
                "project",
                "gateway",
                "node",
                "generation",
                "infrastructure",
                "policy",
                "gateway_subnet_name",
            }
            or value["schema"] != SCHEMA
        ):
            raise ValueError
        if any(
            not isinstance(value[key], str) or not value[key]
            for key in ("project", "gateway", "node", "gateway_subnet_name")
        ):
            raise ValueError
        if re.fullmatch(r"[a-f0-9]{64}", value["generation"]) is None:
            raise ValueError
        infrastructure = value["infrastructure"]
        if set(infrastructure) != {
            "project",
            "network_id",
            "network_spec_sha256",
            "table_ids",
            "tables",
            "targets",
            "gateways",
            "excluded_prefixes",
        }:
            raise ValueError
        if infrastructure["project"] != value["project"] or set(infrastructure["gateways"]) != {
            "0"
        }:
            raise ValueError
        guest = infrastructure["gateways"]["0"]
        if (
            set(guest) != {"name", "compute_id", "nic", "subnet_id", "allocation_id"}
            or guest["name"] != value["node"]
            or any(
                not isinstance(item, str) or not item or item.startswith("@")
                for item in guest.values()
            )
        ):
            raise ValueError
        if (
            not infrastructure["network_id"]
            or re.fullmatch(r"[a-f0-9]{64}", infrastructure["network_spec_sha256"]) is None
        ):
            raise ValueError
        if not infrastructure["targets"] or set(infrastructure["table_ids"]) != set(
            infrastructure["tables"]
        ):
            raise ValueError
        subnets = set()
        for target in infrastructure["targets"]:
            if (
                set(target) != {"subnet_id", "table_id", "spec_sha256"}
                or not target["subnet_id"]
                or target["subnet_id"] in subnets
                or target["table_id"] not in infrastructure["tables"]
                or re.fullmatch(r"[a-f0-9]{64}", target["spec_sha256"]) is None
            ):
                raise ValueError
            subnets.add(target["subnet_id"])
        # A runtime writer cannot acquire default/shared tables or add consumers.
        for table in infrastructure["tables"].values():
            if (
                set(table) != {"spec_sha256", "default", "subnets"}
                or type(table["default"]) is not bool
                or table["default"]
                or re.fullmatch(r"[a-f0-9]{64}", table["spec_sha256"]) is None
                or not set(table["subnets"]) <= subnets
            ):
                raise ValueError
        policy = value["policy"]
        Policy.parse(policy)
        if (
            set(policy) != {"connections", "excluded_prefixes", "summarize"}
            or policy["excluded_prefixes"] != infrastructure["excluded_prefixes"]
        ):
            raise ValueError
        if any(item["instance_index"] != 0 for item in policy["connections"]):
            raise ValueError
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError(
            "Ordinary runtime route binding is incomplete or exceeds its authority"
        ) from None
    return deepcopy(value)


def validate(value: dict) -> dict:
    """Reject expanded authority, missing identities and unresolved creations."""
    metadata_only(value)
    try:
        validate_intent({key: item for key, item in value.items() if key != "credentials"})
        credentials = value["credentials"]
        if (
            set(credentials) != {"path", "sha256", "service_account", "authorized_key"}
            or not credentials["service_account"]
            or not credentials["authorized_key"]
            or credentials["path"]
            != credential_path(
                generation=value["generation"], node=value["node"], sha256=credentials["sha256"]
            )
        ):
            raise ValueError
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError(
            "Ordinary runtime route binding is incomplete or exceeds its authority"
        ) from None
    return deepcopy(value)


def intent(
    *,
    project: str,
    gateway: str,
    node: str,
    config: bytes,
    observation: dict,
    gateway_subnet_name: str,
) -> dict:
    return validate_intent(
        {
            "schema": SCHEMA,
            "project": project,
            "gateway": gateway,
            "node": node,
            "generation": hashlib.sha256(config).hexdigest(),
            "infrastructure": observation["infrastructure"],
            "policy": json.loads(json.dumps(observation["policy"].mapping())),
            "gateway_subnet_name": gateway_subnet_name,
        }
    )


def build(
    *,
    project: str,
    gateway: str,
    node: str,
    config: bytes,
    observation: dict,
    credentials: Any,
    gateway_subnet_name: str,
) -> dict:
    if (
        credentials.purpose != "ordinary-route-writer"
        or tuple(credentials.by_node()) != (node,)
        or credentials.project_id != project
    ):
        raise ValueError("Ordinary route credentials do not match the selected node and project")
    identity = credentials.nodes[0]
    generation = hashlib.sha256(config).hexdigest()
    return validate(
        {
            **intent(
                project=project,
                gateway=gateway,
                node=node,
                config=config,
                observation=observation,
                gateway_subnet_name=gateway_subnet_name,
            ),
            "credentials": {
                "path": credential_path(
                    generation=generation, node=node, sha256=identity.credential_sha256
                ),
                "sha256": identity.credential_sha256,
                "service_account": identity.service_account_id,
                "authorized_key": identity.authorized_key_id,
            },
        }
    )


def read(path: Path = PATH) -> dict | None:
    record = read_file(path)
    if record is None:
        return None
    if record["uid"] != os.geteuid() or record["mode"] != 0o600:
        raise ValueError("Ordinary route binding is not protected")
    return validate(json.loads(record["content"]))


def authority(value: dict, raw_config: bytes, boot_id: str) -> str:
    validate(value)
    if not boot_id or hashlib.sha256(raw_config).hexdigest() != value["generation"]:
        raise RuntimeError("Ordinary route configuration authority changed")
    return digest([value, boot_id])
