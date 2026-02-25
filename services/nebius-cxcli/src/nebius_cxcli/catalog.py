"""Catalog definitions for selectable infra and app components."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CatalogScope = Literal["infra", "apps"]


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    scope: CatalogScope
    schema_path: str
    description: str
    name: str | None = None
    default_enabled: bool = True
    selectable: bool = True
    enabled_path: tuple[str, ...] | None = None
    engine_type: str = "builtin"
    source: str | None = None
    version: str | None = None
    origin: str = "built-in"


INFRA_CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        id="mk8s",
        scope="infra",
        schema_path="infra.mk8s",
        description="Managed Kubernetes baseline cluster",
        default_enabled=True,
        selectable=False,
        enabled_path=None,
    ),
    CatalogEntry(
        id="managed-postgresql",
        scope="infra",
        schema_path="infra.managed_postgresql",
        description="Managed PostgreSQL database",
        default_enabled=True,
        enabled_path=("infra", "managed_postgresql", "enabled"),
    ),
    CatalogEntry(
        id="sfs",
        scope="infra",
        schema_path="infra.sfs",
        description="Shared filesystem and CSI integration",
        default_enabled=True,
        enabled_path=("infra", "sfs", "enabled"),
    ),
    CatalogEntry(
        id="object-storage",
        scope="infra",
        schema_path="infra.object_storage",
        description="State and inventory Object Storage buckets",
        default_enabled=True,
        selectable=False,
        enabled_path=None,
    ),
    CatalogEntry(
        id="mysterybox",
        scope="infra",
        schema_path="infra.mysterybox",
        description="MysteryBox secrets catalog",
        default_enabled=False,
        enabled_path=("infra", "mysterybox", "enabled"),
    ),
    CatalogEntry(
        id="wireguard-jumphost",
        scope="infra",
        schema_path="infra.wireguard-jumphost",
        description="WireGuard jump host",
        default_enabled=True,
        enabled_path=("infra", "wireguard-jumphost", "enabled"),
    ),
    CatalogEntry(
        id="ssh-jumphost",
        scope="infra",
        schema_path="infra.ssh-jumphost",
        description="SSH-only hardened jump host",
        default_enabled=False,
        enabled_path=("infra", "ssh-jumphost", "enabled"),
    ),
)

APPS_CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        id="envoy-gateway",
        scope="apps",
        schema_path="apps.platform.envoy_gateway",
        description="Envoy Gateway control plane",
        default_enabled=True,
        enabled_path=("apps", "platform", "envoy_gateway", "enabled"),
    ),
    CatalogEntry(
        id="cert-manager",
        scope="apps",
        schema_path="apps.platform.cert_manager",
        description="cert-manager for certificate automation",
        default_enabled=True,
        enabled_path=("apps", "platform", "cert_manager", "enabled"),
    ),
    CatalogEntry(
        id="external-dns",
        scope="apps",
        schema_path="apps.platform.external_dns",
        description="ExternalDNS controller",
        default_enabled=True,
        enabled_path=("apps", "platform", "external_dns", "enabled"),
    ),
    CatalogEntry(
        id="observability",
        scope="apps",
        schema_path="apps.platform.observability",
        description="Nebius observability stack",
        default_enabled=True,
        enabled_path=("apps", "platform", "observability", "enabled"),
    ),
    CatalogEntry(
        id="external-secrets",
        scope="apps",
        schema_path="apps.platform.external_secrets",
        description="External Secrets Operator",
        default_enabled=True,
        enabled_path=("apps", "platform", "external_secrets", "enabled"),
    ),
    CatalogEntry(
        id="n8n",
        scope="apps",
        schema_path="apps.workloads.n8n",
        description="n8n workload and HTTPRoute",
        default_enabled=True,
        enabled_path=("apps", "workloads", "n8n", "enabled"),
    ),
)

_CATALOGS: dict[CatalogScope, tuple[CatalogEntry, ...]] = {
    "infra": INFRA_CATALOG,
    "apps": APPS_CATALOG,
}


def catalog_entries(scope: CatalogScope) -> tuple[CatalogEntry, ...]:
    return _CATALOGS[scope]


def catalog_lookup(scope: CatalogScope) -> dict[str, CatalogEntry]:
    return {entry.id: entry for entry in catalog_entries(scope)}


def default_catalog_ids(scope: CatalogScope) -> list[str]:
    return [entry.id for entry in catalog_entries(scope) if entry.default_enabled]


def all_catalog_ids(scope: CatalogScope) -> list[str]:
    return [entry.id for entry in catalog_entries(scope)]
