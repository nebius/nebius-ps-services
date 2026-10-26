"""Bounded, read-only deployed VPC identity; never expose raw Terraform state."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .component_sources import component_output_root_name
from .generated_manifest import load_generated_manifest, manifest_path_for_generated_dir
from .object_storage_errors import ObjectStorageError
from .object_storage_transport import object_storage_transport
from .paths import ProjectPaths
from .terraform_backend import TerraformBackendSettings, backend_settings_from_config


@dataclass(frozen=True)
class VpcResourceIdentity:
    id: str
    parent_id: str
    network_id: str


@dataclass(frozen=True)
class VpcIdentitySnapshot:
    status: str = "fresh"
    networks: Mapping[str, VpcResourceIdentity] = field(default_factory=dict)
    subnets: Mapping[tuple[str, str], VpcResourceIdentity] = field(default_factory=dict)
    diagnostic: str = ""


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _identity(values: Mapping[str, Any], *, network: bool = False) -> VpcResourceIdentity:
    identity = VpcResourceIdentity(
        _text(values.get("id")),
        _text(values.get("parent_id")),
        _text(values.get("id" if network else "network_id")),
    )
    if not all((identity.id, identity.parent_id, identity.network_id)):
        raise ValueError("Incomplete VPC identity in Terraform state")
    return identity


def parse_vpc_state(
    state: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    project_id: str,
) -> VpcIdentitySnapshot:
    """Project raw state v4 through exact recorded module/resource addresses."""
    if state.get("version") != 4 or not isinstance(state.get("resources"), list):
        raise ValueError("Unsupported Terraform state format")
    render = manifest.get("render")
    if not isinstance(render, Mapping):
        raise ValueError("Invalid recorded render inventory")
    sources = render.get("module_sources")
    outputs = state.get("outputs", {})
    if not isinstance(sources, list) or not isinstance(outputs, Mapping):
        raise ValueError("Invalid VPC state or module inventory")
    modules: dict[str, str] = {}
    for row in sources:
        if not isinstance(row, Mapping) or any(
            not _text(row.get(key)).strip()
            for key in ("component_id", "instance_id", "module_name")
        ):
            raise ValueError("Invalid recorded module inventory")
        if row["component_id"] != "vpc":
            continue
        instance = _text(row.get("instance_id"))
        module = _text(row.get("module_name"))
        if not instance or not module or module in modules or instance in modules.values():
            raise ValueError("Ambiguous recorded VPC module identity")
        modules[module] = instance
    networks: dict[str, VpcResourceIdentity] = {}
    subnets: dict[tuple[str, str], VpcResourceIdentity] = {}
    for resource in state["resources"]:
        if not isinstance(resource, Mapping):
            raise ValueError("Malformed Terraform resource inventory")
        module = _text(resource.get("module"))
        instance = next((v for k, v in modules.items() if module == f"module.{k}"), "")
        kind = resource.get("type")
        if (
            not instance
            or resource.get("mode") != "managed"
            or kind not in {"nebius_vpc_v1_network", "nebius_vpc_v1_subnet"}
        ):
            continue
        if resource.get("name") != "this" or not isinstance(resource.get("instances"), list):
            raise ValueError("Unsupported VPC resource address")
        for item in resource["instances"]:
            if not isinstance(item, Mapping) or item.get("deposed"):
                raise ValueError("VPC replacement identity is unresolved")
            values = item.get("attributes")
            if not isinstance(values, Mapping):
                raise ValueError("VPC resource attributes are unresolved")
            identity = _identity(values, network=kind == "nebius_vpc_v1_network")
            if identity.parent_id != project_id:
                raise ValueError("VPC state project mismatch")
            if kind == "nebius_vpc_v1_network":
                if instance in networks:
                    raise ValueError("Ambiguous VPC network identity")
                networks[instance] = identity
            else:
                key = _text(item.get("index_key"))
                if not key or (instance, key) in subnets:
                    raise ValueError("Ambiguous VPC subnet identity")
                subnets[(instance, key)] = identity

    def output(instance: str, name: str) -> Any:
        row = outputs.get(component_output_root_name(instance, name), {})
        if not isinstance(row, Mapping):
            raise ValueError("Invalid VPC output")
        return row.get("value")

    for instance in modules.values():
        network_value = output(instance, "network")
        network_id = output(instance, "network_id")
        network_context = networks.get(instance)
        if network_value is not None:
            if not isinstance(network_value, Mapping):
                raise ValueError("Invalid VPC network output")
            identity = _identity(network_value, network=True)
            if identity.parent_id != project_id:
                raise ValueError("VPC output project mismatch")
            if instance in networks and networks[instance] != identity:
                raise ValueError("VPC network output disagrees with resource state")
            # Adopted networks also have outputs. Only managed resources prove ownership.
            network_context = identity
        if network_id is not None and (
            not isinstance(network_id, str)
            or network_context is None
            or network_context.id != network_id
        ):
            raise ValueError("VPC network output identity is unresolved")
        metadata = output(instance, "subnets")
        ids = output(instance, "subnet_ids")
        for values in (metadata, ids):
            if values is not None and not isinstance(values, Mapping):
                raise ValueError("Invalid VPC subnet output")
        for key, values in (metadata or {}).items():
            if not isinstance(key, str) or not isinstance(values, Mapping):
                raise ValueError("Invalid keyed subnet output")
            identity = _identity(values)
            if identity.parent_id != project_id:
                raise ValueError("VPC subnet output project mismatch")
            if subnets.get((instance, key)) != identity:
                raise ValueError("VPC subnet output lacks agreeing managed resource state")
        for key, identifier in (ids or {}).items():
            recorded_subnet = subnets.get((instance, key))
            if recorded_subnet is None or recorded_subnet.id != identifier:
                raise ValueError("VPC subnet ID output is unresolved")
        for (owner, _key), identity in subnets.items():
            if (
                owner == instance
                and network_context is not None
                and (identity.network_id != network_context.id)
            ):
                raise ValueError("VPC subnet/network state disagreement")
    return VpcIdentitySnapshot("available", networks, subnets)


class VpcIdentityReader:
    """One lazy backend snapshot per wizard scope; no recovery or auth setup."""

    def __init__(self, paths: ProjectPaths) -> None:
        self.paths = paths
        self._cache: dict[tuple[str, ...], VpcIdentitySnapshot] = {}

    def read(self, payload: Mapping[str, Any]) -> VpcIdentitySnapshot:
        client = payload.get("client_info", {})
        if not isinstance(client, Mapping) or not isinstance(client.get("nebius"), Mapping):
            return VpcIdentitySnapshot("unavailable", diagnostic="Invalid VPC project scope.")
        nebius = client.get("nebius", {})
        scope = tuple(
            _text(v)
            for v in (
                client.get("client_name"),
                nebius.get("tenant_id"),
                nebius.get("project_id"),
                nebius.get("region_id"),
                os.environ.get("NEBIUS_CXCLI_TFSTATE_BUCKET_NAME"),
                os.environ.get("NEBIUS_CXCLI_TFSTATE_OBJECT_KEY"),
            )
        )
        if scope not in self._cache:
            try:
                self._cache[scope] = self._read(payload)
            except ObjectStorageError as exc:
                self._cache[scope] = VpcIdentitySnapshot(
                    "unavailable", diagnostic=f"VPC state identity unavailable ({exc.code})."
                )
            except (ValueError, TypeError, KeyError, OSError, RuntimeError):
                self._cache[scope] = VpcIdentitySnapshot(
                    "unavailable",
                    diagnostic="VPC recorded identity is unavailable or inconsistent.",
                )
        return self._cache[scope]

    def _has_deployment_evidence(self) -> bool:
        journals = self.paths.project_dir / ".nebius-cxcli" / "deployment-journals"
        return (self.paths.reports_dir / "deploy-report.md").exists() or (
            journals.exists() and any(journals.iterdir())
        )

    def _read(self, payload: Mapping[str, Any]) -> VpcIdentitySnapshot:
        if not manifest_path_for_generated_dir(self.paths.generated_dir).exists():
            if self._has_deployment_evidence():
                raise ValueError("Deployment identity manifest is missing")
            return VpcIdentitySnapshot()
        manifest = load_generated_manifest(self.paths.generated_dir, read_only=True)
        render = manifest.get("render")
        if not isinstance(render, Mapping) or not isinstance(render.get("module_sources"), list):
            raise ValueError("Invalid recorded render inventory")
        settings = TerraformBackendSettings(**manifest["execution"]["backend"])
        expected = backend_settings_from_config(payload)
        if any(
            getattr(settings, k) != getattr(expected, k)
            for k in ("project_id", "client_name", "region_id", "endpoint")
        ):
            raise ValueError("Recorded backend scope mismatch")
        for env, field_name in (
            ("NEBIUS_CXCLI_TFSTATE_BUCKET_NAME", "bucket"),
            ("NEBIUS_CXCLI_TFSTATE_OBJECT_KEY", "key"),
        ):
            if os.environ.get(env) and getattr(settings, field_name) != getattr(
                expected, field_name
            ):
                raise ValueError("Recorded backend selection changed")
        project = manifest.get("project", {})
        if not isinstance(project, Mapping):
            raise ValueError("Invalid recorded project scope")
        if project.get("project_id") != expected.project_id or project.get("tenant_id") != (
            payload.get("client_info", {}).get("nebius", {}).get("tenant_id")
        ):
            raise ValueError("Recorded project scope mismatch")
        try:
            with object_storage_transport(settings) as transport:
                response = transport.request(
                    "get_object",
                    settings.key,
                    deadline=time.monotonic() + 30,
                )
        except ObjectStorageError as exc:
            if exc.code == "NoSuchKey" and not self._has_deployment_evidence():
                return VpcIdentitySnapshot()
            raise
        state = json.loads(response.body)
        if not isinstance(state, Mapping):
            raise ValueError("Invalid Terraform state")
        return parse_vpc_state(state, manifest=manifest, project_id=expected.project_id)
