#!/usr/bin/env python3
"""Read-only Nebius Control Plane Audit Logs query helper."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


DEFAULT_EVENT_TYPE = "control_plane"
DEFAULT_HOURS = 24.0
DEFAULT_PAGE_SIZE = 100
DEFAULT_REGION = "eu-north1"
SAFE_FILTER_VALUE_RE = re.compile(r"^[A-Za-z0-9._:/@+=,~-]{1,512}$")
REGION_RE = re.compile(r"^[a-z]+-[a-z]+[0-9]$")


class AuditLogError(RuntimeError):
    """Raised for expected user-facing failures."""


@dataclass(frozen=True)
class QueryPlan:
    tenant_id: str
    region: str
    start: str
    end: str
    filter_expr: str
    command: list[str]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Query Nebius Control Plane Audit Logs through the Nebius CLI. "
            "The helper is read-only and sanitizes output unless --raw is set."
        )
    )
    parser.add_argument("--resource-id", help="Nebius resource ID to query.")
    parser.add_argument("--tenant-id", help="Nebius tenant ID. Defaults to CLI config tenant-id.")
    parser.add_argument("--project-id", help="Project ID used only for region discovery.")
    parser.add_argument("--region", help="Audit Logs region. Defaults to discovered project region, then eu-north1.")
    parser.add_argument("--start", help="Start timestamp in ISO 8601 format. Defaults to now minus --hours.")
    parser.add_argument("--end", help="End timestamp in ISO 8601 format. Defaults to now.")
    parser.add_argument(
        "--hours",
        type=float,
        default=DEFAULT_HOURS,
        help="Trailing time window when --start is omitted. Default: 24.",
    )
    parser.add_argument("--action", action="append", default=[], help="Audit action filter, for example DELETE.")
    parser.add_argument("--service", help="Service filter, for example COMPUTE.")
    parser.add_argument("--resource-type", help="Resource type filter, for example computeinstance.")
    parser.add_argument("--status", help="Operation status filter.")
    parser.add_argument("--raw-filter", help="Additional Nebius Audit Logs filter text appended with AND.")
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help="Bounded page size for live queries. Default: 100.",
    )
    parser.add_argument("--page-token", help="Page token for continuing a previous query.")
    parser.add_argument("--all", action="store_true", help="Ask the Nebius CLI to retrieve all pages.")
    parser.add_argument("--profile", help="Nebius CLI profile to use.")
    parser.add_argument("--dry-run", action="store_true", help="Print the resolved command and do not query Audit Logs.")
    parser.add_argument(
        "--format",
        choices=("summary", "json", "yaml", "table", "text"),
        default="summary",
        help="Output format. summary/json are sanitized unless --raw is set.",
    )
    parser.add_argument("--raw", action="store_true", help="Pass through raw Nebius CLI output.")
    parser.add_argument(
        "--include-pii",
        action="store_true",
        help="Include PII-bearing summary fields such as subject names.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        plan = build_query_plan(args)
        if args.dry_run:
            print_dry_run(plan, profile=args.profile)
            return 0

        if args.raw:
            stdout = run_nebius(plan.command, profile=args.profile)
            sys.stdout.write(stdout)
            return 0

        if args.format not in {"summary", "json"}:
            raise AuditLogError(
                "--format yaml/table/text requires --raw because only JSON output can be sanitized."
            )

        stdout = run_nebius(plan.command, profile=args.profile)
        payload = parse_json_output(stdout)
        sanitized = sanitize_payload(payload, include_pii=args.include_pii)
        if args.format == "json":
            print(json.dumps(sanitized, indent=2, sort_keys=True))
        else:
            print_summary(sanitized)
        return 0
    except AuditLogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def build_query_plan(args: argparse.Namespace) -> QueryPlan:
    validate_args(args)
    tenant_id = resolve_tenant_id(args.tenant_id, args.profile)
    region = resolve_region(args.region, args.project_id, args.profile)
    start, end = resolve_time_range(args.start, args.end, args.hours)
    filter_expr = build_filter(args)
    cli_format = cli_output_format(args)
    command = build_audit_list_command(
        tenant_id=tenant_id,
        region=region,
        start=start,
        end=end,
        filter_expr=filter_expr,
        page_size=args.page_size,
        page_token=args.page_token,
        all_pages=args.all,
        output_format=cli_format,
    )
    return QueryPlan(
        tenant_id=tenant_id,
        region=region,
        start=start,
        end=end,
        filter_expr=filter_expr,
        command=command,
    )


def validate_args(args: argparse.Namespace) -> None:
    if args.hours <= 0:
        raise AuditLogError("--hours must be greater than zero.")
    if args.page_size <= 0:
        raise AuditLogError("--page-size must be greater than zero.")
    if args.all and args.page_token:
        raise AuditLogError("--all cannot be combined with --page-token.")


def cli_output_format(args: argparse.Namespace) -> str:
    if args.raw:
        return "json" if args.format == "summary" else args.format
    return "json"


def nebius_binary() -> str:
    return os.environ.get("NEBIUS_BIN", "nebius")


def with_global_options(command: list[str], profile: str | None) -> list[str]:
    full_command = [nebius_binary(), *command, "--no-progress", "--no-check-update"]
    if profile:
        full_command.extend(["--profile", profile])
    return full_command


def run_nebius(command: list[str], profile: str | None = None) -> str:
    full_command = with_global_options(command, profile)
    try:
        proc = subprocess.run(
            full_command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise AuditLogError("Nebius CLI not found on PATH. Install or configure nebius first.") from exc

    if proc.returncode != 0:
        stderr = redact_error_text(proc.stderr.strip())
        detail = f" Nebius stderr: {stderr}" if stderr else ""
        raise AuditLogError(f"Nebius CLI command failed with exit code {proc.returncode}.{detail}")
    return proc.stdout


def redact_error_text(text: str) -> str:
    if not text:
        return ""
    redacted = re.sub(r"ne1[a-zA-Z0-9._-]+", "<redacted-token>", text)
    redacted = re.sub(r"NAKI[A-Za-z0-9._-]+", "<redacted-static-key>", redacted)
    redacted = re.sub(r"(?i)(private[-_ ]?key\s*[:=]\s*)\S+", r"\1<redacted>", redacted)
    redacted = re.sub(r"(?i)(--filter(?:=|\s+))\S+", r"\1<redacted-filter>", redacted)
    redacted = re.sub(r"(?im)^.*filter.*$", "<redacted-filter-error-line>", redacted)
    return redacted[-1000:]


def resolve_tenant_id(explicit_tenant_id: str | None, profile: str | None) -> str:
    if explicit_tenant_id:
        return explicit_tenant_id
    tenant_id = config_get("tenant-id", profile)
    if tenant_id:
        return tenant_id
    raise AuditLogError(
        "tenant ID was not provided and `nebius config get tenant-id` returned no value. "
        "Pass --tenant-id or select a configured Nebius profile."
    )


def resolve_region(
    explicit_region: str | None,
    explicit_project_id: str | None,
    profile: str | None,
) -> str:
    if explicit_region:
        return explicit_region

    project_id = explicit_project_id or configured_project_id(profile)
    if project_id:
        region = project_region(project_id, profile)
        if region:
            return region

    for prop in ("region", "default-region"):
        configured = config_get(prop, profile)
        if configured:
            return configured

    return DEFAULT_REGION


def config_get(property_name: str, profile: str | None) -> str | None:
    try:
        output = run_nebius(["config", "get", property_name], profile=profile)
    except AuditLogError:
        return None
    value = output.strip()
    return value or None


def configured_project_id(profile: str | None) -> str | None:
    parent_id = config_get("parent-id", profile)
    if parent_id and parent_id.startswith("project-"):
        return parent_id
    return None


def project_region(project_id: str, profile: str | None) -> str | None:
    try:
        output = run_nebius(
            ["iam", "v2", "project", "get", "--id", project_id, "--format", "json"],
            profile=profile,
        )
    except AuditLogError:
        return None
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None
    return find_region_value(payload)


def find_region_value(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("region", "region_id", "regionId", "project_region", "projectRegion"):
            candidate = value.get(key)
            if isinstance(candidate, str) and REGION_RE.fullmatch(candidate):
                return candidate
            if isinstance(candidate, dict):
                nested = find_region_value(candidate)
                if nested:
                    return nested
        for child in value.values():
            nested = find_region_value(child)
            if nested:
                return nested
    elif isinstance(value, list):
        for item in value:
            nested = find_region_value(item)
            if nested:
                return nested
    return None


def resolve_time_range(
    explicit_start: str | None,
    explicit_end: str | None,
    hours: float,
) -> tuple[str, str]:
    now = utc_now()
    end_dt = parse_time(explicit_end) if explicit_end else now
    start_dt = parse_time(explicit_start) if explicit_start else end_dt - timedelta(hours=hours)
    if start_dt >= end_dt:
        raise AuditLogError("--start must be earlier than --end.")
    return format_utc(start_dt), format_utc(end_dt)


def utc_now() -> datetime:
    override = os.environ.get("NEBIUS_AUDIT_LOG_NOW")
    if override:
        return parse_time(override)
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_time(value: str) -> datetime:
    raw = value.strip()
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise AuditLogError(f"invalid ISO 8601 timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_filter(args: argparse.Namespace) -> str:
    parts: list[str] = []
    if args.resource_id:
        parts.append(equals_filter("resource.metadata.id", args.resource_id))
    else:
        parts.append(resolve_current_subject_filter(args.profile))

    for action in args.action:
        parts.append(equals_filter("action", action))
    if args.service:
        parts.append(equals_filter("service.name", args.service))
    if args.resource_type:
        parts.append(equals_filter("resource.metadata.type", args.resource_type))
    if args.status:
        parts.append(equals_filter("status", args.status))
    if args.raw_filter:
        parts.append(args.raw_filter.strip())

    return " AND ".join(part for part in parts if part)


def equals_filter(field: str, value: str) -> str:
    return f"{field}='{safe_filter_value(value)}'"


def safe_filter_value(value: str) -> str:
    if not SAFE_FILTER_VALUE_RE.fullmatch(value):
        raise AuditLogError(
            f"unsafe filter value {value!r}; use --raw-filter for advanced Nebius filter syntax."
        )
    return value


def resolve_current_subject_filter(profile: str | None) -> str:
    try:
        output = run_nebius(["iam", "whoami", "--format", "json"], profile=profile)
    except AuditLogError as exc:
        raise AuditLogError(
            "resource ID was not provided and current Nebius principal could not be resolved. "
            "Pass --resource-id or fix `nebius iam whoami --format json`."
        ) from exc

    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise AuditLogError("`nebius iam whoami --format json` did not return valid JSON.") from exc

    tenant_user_id = find_prefixed_value(payload, "tenantuseraccount-")
    if tenant_user_id:
        return equals_filter("authentication.subject.tenant_user_id", tenant_user_id)

    service_account_id = find_prefixed_value(payload, "serviceaccount-")
    if service_account_id:
        return equals_filter("authentication.subject.service_account_id", service_account_id)

    raise AuditLogError(
        "resource ID was not provided and `nebius iam whoami` did not expose a "
        "tenantuseraccount-* or serviceaccount-* subject ID."
    )


def find_prefixed_value(value: Any, prefix: str) -> str | None:
    if isinstance(value, str):
        return value if value.startswith(prefix) else None
    if isinstance(value, dict):
        for child in value.values():
            found = find_prefixed_value(child, prefix)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = find_prefixed_value(child, prefix)
            if found:
                return found
    return None


def build_audit_list_command(
    *,
    tenant_id: str,
    region: str,
    start: str,
    end: str,
    filter_expr: str,
    page_size: int,
    page_token: str | None,
    all_pages: bool,
    output_format: str,
) -> list[str]:
    command = [
        "audit",
        "v2",
        "audit-event",
        "list",
        "--parent-id",
        tenant_id,
        "--start",
        start,
        "--end",
        end,
        "--event-type",
        DEFAULT_EVENT_TYPE,
        "--region",
        region,
        "--filter",
        filter_expr,
        "--format",
        output_format,
    ]
    if all_pages:
        command.append("--all")
    else:
        command.extend(["--page-size", str(page_size)])
        if page_token:
            command.extend(["--page-token", page_token])
    return command


def print_dry_run(plan: QueryPlan, *, profile: str | None) -> None:
    payload = {
        "command": with_global_options(plan.command, profile=profile),
        "query": {
            "event_type": DEFAULT_EVENT_TYPE,
            "filter": plan.filter_expr,
            "region": plan.region,
            "start": plan.start,
            "end": plan.end,
            "tenant_id": plan.tenant_id,
        },
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


def parse_json_output(stdout: str) -> Any:
    text = stdout.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AuditLogError("Nebius CLI did not return JSON output; retry with --raw to inspect it.") from exc


def sanitize_payload(payload: Any, *, include_pii: bool) -> dict[str, Any]:
    events = extract_events(payload)
    return {
        "events": [sanitize_event(event, include_pii=include_pii) for event in events],
        "next_page_token": next_page_token(payload),
    }


def extract_events(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("items", "events", "audit_events", "auditEvents", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if any(key in payload for key in ("id", "type", "action", "resource")):
            return [payload]
    return []


def next_page_token(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("next_page_token", "nextPageToken", "next_token", "nextToken"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def sanitize_event(event: dict[str, Any], *, include_pii: bool) -> dict[str, Any]:
    authentication = as_dict(event.get("authentication"))
    subject = as_dict(authentication.get("subject"))
    resource = as_dict(event.get("resource"))
    metadata = as_dict(resource.get("metadata"))
    service = as_dict(event.get("service"))
    project_region = as_dict(event.get("project_region"))
    authorization = as_dict(event.get("authorization"))

    resource_summary: dict[str, Any] = {
        "id": scalar(metadata.get("id")),
        "type": scalar(metadata.get("type")),
    }
    if include_pii:
        resource_summary["name"] = scalar(metadata.get("name"))

    sanitized: dict[str, Any] = {
        "id": scalar(event.get("id")),
        "time": scalar(event.get("time")),
        "type": scalar(event.get("type")),
        "service": scalar(service.get("name")),
        "action": scalar(event.get("action")),
        "status": scalar(event.get("status")),
        "project_region": scalar(project_region.get("name")),
        "resource": resource_summary,
        "subject": {
            "tenant_user_id": scalar(subject.get("tenant_user_id")),
            "service_account_id": scalar(subject.get("service_account_id")),
        },
        "authorized": scalar(authorization.get("authorized")),
    }
    if include_pii:
        sanitized["subject"]["name"] = scalar(subject.get("name"))
    return sanitized


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None


def print_summary(payload: dict[str, Any]) -> None:
    events = payload.get("events", [])
    print(f"Events: {len(events)}")
    for event in events:
        resource = event.get("resource") or {}
        subject = event.get("subject") or {}
        subject_id = subject.get("tenant_user_id") or subject.get("service_account_id") or "-"
        print(
            " - "
            f"{event.get('time') or '-'} "
            f"{event.get('action') or '-'} "
            f"{event.get('service') or '-'} "
            f"{resource.get('type') or '-'} "
            f"{resource.get('id') or '-'} "
            f"subject={subject_id}"
        )
    if payload.get("next_page_token"):
        print("Next page token is available; rerun with --page-token to continue.")


if __name__ == "__main__":
    raise SystemExit(main())
