"""Project-scoped, metadata-only inventories for the bundled inspection scripts."""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path

from sdk.inspection import Report
from sdk.runtime import collect_pages, owned_sdk, rpc, verify_identity

# These are public SDK identifiers, not guessed pluralization rules.
RESOURCES = {
    "instance": ("compute.v1", "InstanceServiceClient", "ListInstancesRequest"),
    "disk": ("compute.v1", "DiskServiceClient", "ListDisksRequest"),
    "filesystem": ("compute.v1", "FilesystemServiceClient", "ListFilesystemsRequest"),
    "bucket": ("storage.v1", "BucketServiceClient", "ListBucketsRequest"),
    "registry": ("registry.v1", "RegistryServiceClient", "ListRegistriesRequest"),
    "postgresql": (
        "msp.postgresql.v1alpha1",
        "ClusterServiceClient",
        "ListClustersRequest",
    ),
    "cluster": ("mk8s.v1", "ClusterServiceClient", "ListClustersRequest"),
    "node-group": ("mk8s.v1", "NodeGroupServiceClient", "ListNodeGroupsRequest"),
    "secret": ("mysterybox.v1", "SecretServiceClient", "ListSecretsRequest"),
    "symmetric-key": (
        "kms.v1",
        "SymmetricKeyServiceClient",
        "ListSymmetricKeysRequest",
    ),
    "asymmetric-key": (
        "kms.v1",
        "AsymmetricKeyServiceClient",
        "ListAsymmetricKeysRequest",
    ),
}


def resource_classes(kind):
    module, client, request = RESOURCES[kind]
    api = importlib.import_module(f"nebius.api.nebius.{module}")
    return getattr(api, client), getattr(api, request)


def metadata_row(item, *, kind: str, parent_id: str) -> dict:
    rid = verify_identity(item, parent_id=parent_id)
    state = getattr(getattr(item, "status", None), "state", None)
    return {
        "kind": kind,
        "id": rid,
        "parent_id": item.metadata.parent_id,
        "name": item.metadata.name,
        "resource_version": item.metadata.resource_version,
        "state": getattr(state, "name", "unknown"),
    }


def metadata_inventory(sdk, *, kind: str, parent_id: str) -> list[dict]:
    client_type, request_type = resource_classes(kind)
    items = collect_pages(
        client_type(sdk).list,
        lambda token: request_type(parent_id=parent_id, page_token=token),
    )
    return [metadata_row(item, kind=kind, parent_id=parent_id) for item in items]


def inspector_main(domain: str) -> int:
    parser = argparse.ArgumentParser(
        description=f"Read Nebius {domain} metadata; no mutations."
    )
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--config-file", type=Path)
    parser.add_argument("--endpoint")
    parser.add_argument(
        "--json", action="store_true", help="Complete structured report"
    )
    if domain == "storage":
        parser.add_argument(
            "--kind",
            action="append",
            choices=["disk", "filesystem", "bucket", "registry", "postgresql"],
            help="Repeat to select kinds; default: disk, filesystem, bucket",
        )
    if domain == "kubernetes":
        parser.add_argument(
            "--cluster-id", help="Inspect this project's cluster and its node groups"
        )
    args = parser.parse_args()
    report = Report({"project_id": args.project_id, "domain": domain})
    try:
        with owned_sdk(
            parent_id=args.project_id,
            profile=args.profile,
            config_file=args.config_file,
            endpoint=args.endpoint,
        ) as sdk:
            if domain == "kubernetes" and args.cluster_id:
                from nebius.api.nebius.mk8s.v1 import (
                    ClusterServiceClient,
                    GetClusterRequest,
                )

                cluster = rpc(
                    ClusterServiceClient(sdk).get, GetClusterRequest(id=args.cluster_id)
                )
                verify_identity(
                    cluster, parent_id=args.project_id, resource_id=args.cluster_id
                )
                report.scope["cluster_id"] = args.cluster_id
                report.data.append(
                    metadata_row(cluster, kind="cluster", parent_id=args.project_id)
                )
                report.collect(
                    "node-group",
                    lambda: metadata_inventory(
                        sdk, kind="node-group", parent_id=args.cluster_id
                    ),
                )
            else:
                kinds = {
                    "compute": ["instance"],
                    "kubernetes": ["cluster"],
                    "storage": getattr(args, "kind", None)
                    or ["disk", "filesystem", "bucket"],
                }[domain]
                report.scope["kinds"] = kinds
                for kind in dict.fromkeys(kinds):
                    report.collect(
                        kind,
                        lambda kind=kind: metadata_inventory(
                            sdk, kind=kind, parent_id=args.project_id
                        ),
                    )
    except Exception as exc:  # noqa: BLE001 - Sanitize errors and retain reconciliation IDs.
        from sdk.runtime import error_code

        report.errors.append({"component": "context", "code": error_code(exc)})
    return report.emit(as_json=args.json)
