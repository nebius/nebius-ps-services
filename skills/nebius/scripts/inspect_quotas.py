#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ASSET_DIR = Path(__file__).resolve().parents[1] / "assets"
sys.path.insert(0, str(ASSET_DIR))

from sdk.inspection import Report
from sdk.runtime import (
    CloudError,
    collect_pages,
    init_nebius_sdk,
    verify_identity,
)


def _as_text(value: object) -> str:
    return str(value or "").strip()


def _available(limit: int | None, usage: int | None) -> int | None:
    if limit is None or usage is None or limit < 0 or usage < 0:
        return None
    return max(limit - usage, 0)


@dataclass(frozen=True)
class QuotaRow:
    scope: str
    name: str
    region: str
    limit: int | None
    usage: int | None
    available: int | None
    service: str
    description: str
    unit: str
    state: str
    usage_state: str
    usage_percentage: str


def _list_quotas(
    client: Any, *, parent_id: str, scope: str
) -> dict[tuple[str, str], QuotaRow]:
    from nebius.api.nebius.quotas.v1 import (
        ListQuotaAllowancesRequest,
        QuotaAllowanceServiceClient,
        QuotaAllowanceStatus,
    )

    quota_client = QuotaAllowanceServiceClient(client)
    items: dict[tuple[str, str], QuotaRow] = {}
    for item in collect_pages(
        quota_client.list,
        lambda token: ListQuotaAllowancesRequest(
            parent_id=parent_id, page_size=500, page_token=token
        ),
    ):
        verify_identity(item, parent_id=parent_id)
        metadata = getattr(item, "metadata", None)
        spec = getattr(item, "spec", None)
        status = getattr(item, "status", None)
        name = _as_text(getattr(metadata, "name", None))
        region = _as_text(getattr(spec, "region", None))
        if not name or not region:
            raise CloudError("QUOTA_DIMENSION_MISSING")
        if (name, region) in items:
            raise CloudError("DUPLICATE_QUOTA_DIMENSION")
        limit = getattr(spec, "limit", None)
        usage = getattr(status, "usage", None)
        # Proto scalar zero is not evidence of a measured zero.
        measured = getattr(status, "usage_state", None) in (
            QuotaAllowanceStatus.UsageState.USAGE_STATE_USED,
            QuotaAllowanceStatus.UsageState.USAGE_STATE_NOT_USED,
        )
        active = (
            getattr(status, "state", None) == QuotaAllowanceStatus.State.STATE_ACTIVE
        )
        if not measured or not active:
            usage = None
        items[(name, region)] = QuotaRow(
            scope=scope,
            name=name,
            region=region,
            limit=limit,
            usage=usage,
            available=_available(limit, usage),
            service=_as_text(getattr(status, "service", None)),
            description=_as_text(getattr(status, "description", None)),
            unit=_as_text(getattr(status, "unit", None)),
            state=_as_text(getattr(getattr(status, "state", None), "name", None)),
            usage_state=_as_text(
                getattr(getattr(status, "usage_state", None), "name", None)
            ),
            usage_percentage=_as_text(getattr(status, "usage_percentage", None)),
        )
    return items


def _match_filters(
    row: QuotaRow, *, regions: set[str], names: set[str], prefixes: tuple[str, ...]
) -> bool:
    if regions and row.region not in regions:
        return False
    if names and row.name not in names:
        return False
    return not prefixes or any(row.name.startswith(prefix) for prefix in prefixes)


def _filtered_rows(
    rows: dict[tuple[str, str], QuotaRow],
    *,
    regions: set[str],
    names: set[str],
    prefixes: tuple[str, ...],
) -> dict[tuple[str, str], QuotaRow]:
    return {
        key: row
        for key, row in rows.items()
        if _match_filters(row, regions=regions, names=names, prefixes=prefixes)
    }


def _effective_rows(
    tenant_rows: dict[tuple[str, str], QuotaRow],
    project_rows: dict[tuple[str, str], QuotaRow],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, region in sorted(
        set(tenant_rows) | set(project_rows), key=lambda item: (item[1], item[0])
    ):
        tenant = tenant_rows.get((name, region))
        project = project_rows.get((name, region))

        tenant_available = tenant.available if tenant is not None else None
        project_available = project.available if project is not None else None

        unresolved = any(
            row is not None
            and (
                row.state != "STATE_ACTIVE"
                or (row.limit is not None and row.usage is None)
            )
            for row in (tenant, project)
        )
        if unresolved:
            available = None
            source_scope = "unresolved"
        elif tenant_available is not None and project_available is not None:
            available = min(tenant_available, project_available)
            source_scope = "tenant+project"
        elif project_available is not None:
            available = project_available
            source_scope = "project"
        elif tenant_available is not None:
            available = tenant_available
            source_scope = "tenant"
        else:
            available = None
            source_scope = "unresolved"

        record = project or tenant
        rows.append(
            {
                "name": name,
                "region": region,
                "effective_available": available,
                "source_scope": source_scope,
                "unit": record.unit if record is not None else "",
                "service": record.service if record is not None else "",
                "description": record.description if record is not None else "",
                "tenant_limit": tenant.limit if tenant is not None else None,
                "tenant_usage": tenant.usage if tenant is not None else None,
                "tenant_available": tenant_available,
                "project_limit": project.limit if project is not None else None,
                "project_usage": project.usage if project is not None else None,
                "project_available": project_available,
                "tenant_state": tenant.state if tenant else None,
                "tenant_usage_state": tenant.usage_state if tenant else None,
                "project_state": project.state if project else None,
                "project_usage_state": project.usage_state if project else None,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect Nebius quota allowances live at tenant and/or project scope and compute "
            "effective available quota by (name, region)."
        )
    )
    parser.add_argument("--tenant-id", help="Nebius tenant ID")
    parser.add_argument("--project-id", help="Nebius project ID")
    parser.add_argument(
        "--region",
        action="append",
        default=[],
        help="Optional region filter. Repeat for multiple regions.",
    )
    parser.add_argument(
        "--name",
        action="append",
        default=[],
        help="Optional exact quota name filter. Repeat for multiple names.",
    )
    parser.add_argument(
        "--name-prefix",
        action="append",
        default=[],
        help="Optional quota-name prefix filter. Repeat for multiple prefixes.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Emit raw tenant/project allowance rows instead of the merged effective view.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable text.",
    )
    parser.add_argument("--profile", help="Optional Nebius CLI profile")
    parser.add_argument("--endpoint", help="Optional Nebius API endpoint override")
    parser.add_argument(
        "--config-file", type=Path, help="Optional Nebius CLI config file path"
    )
    args = parser.parse_args()

    if not args.tenant_id and not args.project_id:
        parser.error("at least one of --tenant-id or --project-id is required")

    config_file = args.config_file.expanduser().resolve() if args.config_file else None
    report = Report(
        {
            "tenant_id": args.tenant_id,
            "project_id": args.project_id,
            "regions": args.region,
            "names": args.name,
            "prefixes": args.name_prefix,
        },
        mode="raw" if args.raw else "effective",
    )

    def build():
        client = init_nebius_sdk(
            profile=args.profile,
            endpoint=args.endpoint,
            config_file=config_file,
            parent_id=args.project_id or args.tenant_id,
        )
        try:
            scopes = {}
            for label, parent in [
                ("tenant", args.tenant_id),
                ("project", args.project_id),
            ]:
                scopes[label] = (
                    _filtered_rows(
                        _list_quotas(client, parent_id=parent, scope=label),
                        regions=set(args.region),
                        names=set(args.name),
                        prefixes=tuple(args.name_prefix),
                    )
                    if parent
                    else {}
                )
            if args.raw:
                return [
                    asdict(row) for rows in scopes.values() for row in rows.values()
                ]
            return _effective_rows(scopes["tenant"], scopes["project"])
        finally:
            client.sync_close(timeout=10.0)

    report.collect("quotas", build)
    return report.emit(as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
