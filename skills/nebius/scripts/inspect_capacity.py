#!/usr/bin/env python3
"""Read timestamped GPU capacity advice for one tenant using the Nebius Python SDK.

Counts describe alternative VM shapes, not additive regional capacity. CPU-only
capacity and project eligibility are not assessed. Import/help perform no I/O.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets"))

from sdk.inspection import Report
from sdk.runtime import CloudError, collect_pages, owned_sdk


def dimension(value, *, optional=False):
    if not isinstance(value, str) or value != value.strip() or len(value) > 256:
        raise CloudError("CAPACITY_DIMENSION_INVALID")
    if (not value and not optional) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise CloudError("CAPACITY_DIMENSION_INVALID")
    return value


def advice_key(item):
    spec = item.spec
    compute = spec.compute_instance
    return (
        dimension(spec.region),
        dimension(spec.fabric, optional=True),
        dimension(compute.platform),
        dimension(compute.preset.name),
    )


def enum_name(value):
    return getattr(value, "name", "UNRECOGNIZED")


def timestamp(value):
    if value is None:
        return None
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise CloudError("CAPACITY_TIMESTAMP_INVALID")
    return value.astimezone(timezone.utc).isoformat()


def count(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= 2**32 - 1
    ):
        raise CloudError("CAPACITY_COUNT_INVALID")
    return value


def advice_rows(items, *, tenant_id, regions):
    rows = []
    for item in items:
        region, fabric, platform, preset = advice_key(item)
        # Advice may omit metadata, but a returned parent must match the request.
        parent = getattr(getattr(item, "metadata", None), "parent_id", "")
        if parent and parent != tenant_id:
            raise CloudError("RESOURCE_IDENTITY_MISMATCH")
        if regions and region not in regions:
            continue
        for allocation in ("on_demand", "preemptible", "reserved"):
            evidence = getattr(item.status, allocation)
            state = enum_name(evidence.data_state)
            effective_at = timestamp(evidence.effective_at)
            known = state == "DATA_STATE_FRESH" and effective_at is not None
            rows.append(
                {
                    "region": region,
                    "fabric": fabric,
                    "platform": platform,
                    "preset": preset,
                    "allocation_type": allocation,
                    "data_state": state,
                    "effective_at": effective_at,
                    "availability_level": enum_name(evidence.availability_level),
                    "available_vm_count": count(evidence.available) if known else None,
                    "limit_vm_count": count(evidence.limit) if known else None,
                    "measurement": "fresh" if known else "unresolved",
                }
            )
    return rows


def inspect_capacity(client, *, tenant_id, regions):
    from nebius.api.nebius.capacity.v1 import (
        ListResourceAdviceRequest,
        ResourceAdviceServiceClient,
    )

    service = ResourceAdviceServiceClient(client)
    items = collect_pages(
        service.list,
        lambda token: ListResourceAdviceRequest(
            parent_id=tenant_id, page_size=500, page_token=token
        ),
        key=advice_key,
    )
    return advice_rows(items, tenant_id=tenant_id, regions=regions)


def selector(value):
    try:
        return dimension(value)
    except CloudError:
        raise argparse.ArgumentTypeError(
            "expected nonempty selector without control characters"
        ) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--tenant-id", required=True, type=selector)
    parser.add_argument(
        "--region",
        action="append",
        default=[],
        type=selector,
        help="Exact region filter, repeatable; omit for all regions",
    )
    parser.add_argument(
        "--profile", type=selector, help="Existing SDK CLI-config profile"
    )
    parser.add_argument("--config-file", type=Path)
    parser.add_argument("--endpoint", type=selector)
    parser.add_argument(
        "--json", action="store_true", help="Emit the complete sanitized report"
    )
    args = parser.parse_args(argv)
    if "all" in args.region:
        parser.error("omit --region to inspect all regions")
    report = Report(
        {
            "tenant_id": args.tenant_id,
            "regions": args.region,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "coverage": "gpu-advice-only",
            "project_eligibility": "not_inspected",
            "cpu_physical_capacity": "not_inspected",
            "counts_additive": False,
        }
    )

    def collect():
        with owned_sdk(
            parent_id=args.tenant_id,
            profile=args.profile,
            config_file=args.config_file.expanduser().resolve()
            if args.config_file
            else None,
            endpoint=args.endpoint,
        ) as client:
            return inspect_capacity(
                client, tenant_id=args.tenant_id, regions=set(args.region)
            )

    report.collect("capacity-advice", collect)
    return report.emit(as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
