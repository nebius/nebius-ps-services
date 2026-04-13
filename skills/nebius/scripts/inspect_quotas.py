#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ASSET_IAM_DIR = Path(__file__).resolve().parents[1] / "assets" / "iam"
sys.path.insert(0, str(ASSET_IAM_DIR))

from iam_api import init_nebius_sdk  # type: ignore  # noqa: E402


def _as_text(value: object) -> str:
    return str(value or "").strip()


def _available(limit: int | None, usage: int) -> int | None:
    if limit is None:
        return None
    return max(limit - usage, 0)


@dataclass(frozen=True)
class QuotaRow:
    scope: str
    name: str
    region: str
    limit: int | None
    usage: int
    available: int | None
    service: str
    description: str
    unit: str
    state: str
    usage_state: str
    usage_percentage: str


def _list_quotas(client: Any, *, parent_id: str, scope: str) -> dict[tuple[str, str], QuotaRow]:
    from nebius.api.nebius.quotas.v1 import (
        ListQuotaAllowancesRequest,
        QuotaAllowanceServiceClient,
    )

    quota_client = QuotaAllowanceServiceClient(client)
    items: dict[tuple[str, str], QuotaRow] = {}
    page_token = ""
    while True:
        response = quota_client.list(
            ListQuotaAllowancesRequest(
                parent_id=parent_id,
                page_size=500,
                page_token=page_token,
            )
        ).wait()
        for item in list(getattr(response, "items", []) or []):
            metadata = getattr(item, "metadata", None)
            spec = getattr(item, "spec", None)
            status = getattr(item, "status", None)
            name = _as_text(getattr(metadata, "name", None))
            region = _as_text(getattr(spec, "region", None))
            if not name or not region:
                continue
            limit = getattr(spec, "limit", None)
            usage = int(getattr(status, "usage", 0) or 0)
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
                usage_state=_as_text(getattr(getattr(status, "usage_state", None), "name", None)),
                usage_percentage=_as_text(getattr(status, "usage_percentage", None)),
            )
        page_token = _as_text(getattr(response, "next_page_token", None))
        if not page_token:
            return items


def _match_filters(row: QuotaRow, *, regions: set[str], names: set[str], prefixes: tuple[str, ...]) -> bool:
    if regions and row.region not in regions:
        return False
    if names and row.name not in names:
        return False
    if prefixes and not any(row.name.startswith(prefix) for prefix in prefixes):
        return False
    return True


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
    for name, region in sorted(set(tenant_rows) | set(project_rows), key=lambda item: (item[1], item[0])):
        tenant = tenant_rows.get((name, region))
        project = project_rows.get((name, region))

        tenant_available = tenant.available if tenant is not None else None
        project_available = project.available if project is not None else None

        if tenant_available is not None and project_available is not None:
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
            }
        )
    return rows


def _format_value(value: Any) -> str:
    return "unresolved" if value is None else str(value)


def _format_raw_rows(rows: list[QuotaRow]) -> str:
    lines: list[str] = []
    for row in rows:
        lines.append(f"{row.scope}: {row.name} [{row.region}]")
        lines.append(f"  available: {_format_value(row.available)} {row.unit}".rstrip())
        lines.append(f"  limit: {_format_value(row.limit)}")
        lines.append(f"  usage: {row.usage}")
        if row.service:
            lines.append(f"  service: {row.service}")
        if row.description:
            lines.append(f"  description: {row.description}")
        if row.state:
            lines.append(f"  state: {row.state}")
        if row.usage_state:
            lines.append(f"  usage_state: {row.usage_state}")
        if row.usage_percentage:
            lines.append(f"  usage_percentage: {row.usage_percentage}")
    return "\n".join(lines)


def _format_effective_rows(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in rows:
        lines.append(f"{row['name']} [{row['region']}]")
        lines.append(
            f"  effective_available: {_format_value(row['effective_available'])} ({row['source_scope']})"
        )
        lines.append(
            "  tenant: "
            f"limit={_format_value(row['tenant_limit'])} "
            f"usage={_format_value(row['tenant_usage'])} "
            f"available={_format_value(row['tenant_available'])}"
        )
        lines.append(
            "  project: "
            f"limit={_format_value(row['project_limit'])} "
            f"usage={_format_value(row['project_usage'])} "
            f"available={_format_value(row['project_available'])}"
        )
        if row["unit"]:
            lines.append(f"  unit: {row['unit']}")
        if row["service"]:
            lines.append(f"  service: {row['service']}")
        if row["description"]:
            lines.append(f"  description: {row['description']}")
    return "\n".join(lines)


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
    parser.add_argument("--config-file", type=Path, help="Optional Nebius CLI config file path")
    args = parser.parse_args()

    if not args.tenant_id and not args.project_id:
        parser.error("at least one of --tenant-id or --project-id is required")

    config_file = args.config_file.expanduser().resolve() if args.config_file else None
    client = init_nebius_sdk(
        profile=args.profile,
        endpoint=args.endpoint,
        config_file=config_file,
        parent_id=args.project_id or args.tenant_id,
    )
    regions = {item for item in (_as_text(value) for value in args.region) if item}
    names = {item for item in (_as_text(value) for value in args.name) if item}
    prefixes = tuple(item for item in (_as_text(value) for value in args.name_prefix) if item)

    try:
        tenant_rows = (
            _filtered_rows(
                _list_quotas(client, parent_id=args.tenant_id, scope="tenant"),
                regions=regions,
                names=names,
                prefixes=prefixes,
            )
            if args.tenant_id
            else {}
        )
        project_rows = (
            _filtered_rows(
                _list_quotas(client, parent_id=args.project_id, scope="project"),
                regions=regions,
                names=names,
                prefixes=prefixes,
            )
            if args.project_id
            else {}
        )

        if args.raw or not tenant_rows or not project_rows:
            raw_rows = sorted(
                [*tenant_rows.values(), *project_rows.values()],
                key=lambda item: (item.region, item.name, item.scope),
            )
            if args.json:
                print(json.dumps([asdict(item) for item in raw_rows], indent=2))
            else:
                print(_format_raw_rows(raw_rows))
            return 0

        effective_rows = _effective_rows(tenant_rows, project_rows)
        if args.json:
            print(json.dumps(effective_rows, indent=2))
        else:
            print(_format_effective_rows(effective_rows))
        return 0
    finally:
        try:
            client.sync_close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
