#!/usr/bin/env python3
"""Verify and query explicit Nebius Control Plane Audit Logs scope."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from audit_cli import AuditError, Cli, InputError, invalid_response, object_value
from audit_filters import (
    ID,
    REGION,
    compile_filter,
    equality,
    parse_extra,
    require_id,
    subject_field,
)


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's original text may quote a secret-bearing invalid argument.
        raise InputError(
            "Invalid arguments. Run --help for supported options and required selectors."
        )


class Once(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest) is not None:
            raise InputError("--action accepts exactly one value.")
        setattr(namespace, self.dest, values)


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(description=__doc__, allow_abbrev=False)
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--resource-id", help="Investigate this resource ID.")
    selector.add_argument(
        "--subject-id", help="Investigate a tenantuseraccount-* or serviceaccount-* ID."
    )
    selector.add_argument(
        "--current-subject",
        action="store_true",
        help="Investigate the verified caller explicitly.",
    )
    selector.add_argument(
        "--tenant-wide",
        action="store_true",
        help="Search all actors/resources in the selected tenant, region and window.",
    )
    parser.add_argument(
        "--tenant-id", help="Tenant ID; otherwise use selected CLI configuration."
    )
    parser.add_argument(
        "--project-id",
        help="Project ID for region discovery only; does not filter events.",
    )
    parser.add_argument(
        "--region",
        help="Origin region; otherwise discover from a project in the selected tenant.",
    )
    parser.add_argument(
        "--profile", help="CLI profile; otherwise resolve the configured profile once."
    )
    parser.add_argument(
        "--start", help="ISO 8601 window start; timestamps without offsets use UTC."
    )
    parser.add_argument(
        "--end", help="ISO 8601 window end; defaults to current UTC time."
    )
    parser.add_argument(
        "--hours",
        type=float,
        help="Trailing window in hours; default 24. Cannot accompany --page-token.",
    )
    parser.add_argument(
        "--action", action=Once, help="One action filter, for example DELETE."
    )
    parser.add_argument("--service", help="Service filter, for example MK8S.")
    parser.add_argument("--resource-type", help="Resource type filter.")
    parser.add_argument(
        "--status",
        choices=("STARTED", "DONE", "ERROR"),
        help="Operation status filter.",
    )
    parser.add_argument(
        "--raw-filter",
        help="Additional AND-connected noncredential comparisons or regex predicates.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="Items per page, 1..500; default 100.",
    )
    parser.add_argument(
        "--max-pages", type=int, default=1, help="Maximum pages, 1..100; default 1."
    )
    parser.add_argument(
        "--page-token",
        help="Resume the same query; requires explicit --start and --end and unchanged filters.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120,
        help="Overall deadline in seconds, >0..600; default 120.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Offline preview; makes no CLI calls and never verifies access.",
    )
    parser.add_argument(
        "--format",
        choices=("summary", "json"),
        default="summary",
        help="Sanitized output format; default summary.",
    )
    parser.add_argument(
        "--include-pii",
        action="store_true",
        help="Include subject and resource names in event summaries.",
    )
    return parser


@dataclass(frozen=True)
class Query:
    selector: dict[str, str]
    filters: dict[str, str]
    extra: str
    extra_metadata: list[dict[str, str]]
    start: str
    end: str


def timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (
            result.replace(tzinfo=timezone.utc)
            if result.tzinfo is None
            else result.astimezone(timezone.utc)
        )
    except (ValueError, OverflowError) as exc:
        raise InputError("Timestamps must be valid ISO 8601 dates and times.") from exc


def utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def validate(args: argparse.Namespace) -> Query:
    if not 1 <= args.page_size <= 500 or not 1 <= args.max_pages <= 100:
        raise InputError("page-size must be 1..500 and max-pages must be 1..100.")
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 600:
        raise InputError("timeout must be finite, positive and at most 600 seconds.")
    hours = 24 if args.hours is None else args.hours
    if not math.isfinite(hours) or hours <= 0:
        raise InputError("hours must be finite and positive.")
    if args.page_token is not None:
        if not args.start or not args.end or args.hours is not None:
            raise InputError(
                "page-token requires explicit start/end, unchanged query parameters and no hours option."
            )
        if not valid_token(args.page_token) or not args.page_token:
            raise InputError("Invalid continuation token.")
    for field, prefix in (("tenant_id", "tenant-"), ("project_id", "project-")):
        value = getattr(args, field)
        if value is not None:
            require_id(value, field.replace("_", "-"), prefix)
    if args.region is not None and not REGION.fullmatch(args.region):
        raise InputError("region must be a Nebius region identifier.")
    if args.profile is not None and not valid_profile(args.profile):
        raise InputError(
            "profile must be a nonempty CLI profile name without control characters."
        )
    if args.resource_id is not None:
        selector = {
            "kind": "resource",
            "id": require_id(args.resource_id, "resource-id"),
        }
    elif args.subject_id is not None:
        subject_field(args.subject_id)
        selector = {"kind": "subject", "id": args.subject_id}
    else:
        selector = {
            "kind": "current_subject" if args.current_subject else "tenant_wide"
        }
    filters = {}
    for attribute, field in (
        ("action", "action"),
        ("service", "service.name"),
        ("resource_type", "resource.metadata.type"),
        ("status", "status"),
    ):
        value = getattr(args, attribute)
        if value is not None:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", value):
                raise InputError(
                    "Structured filters require service/action/type/status identifiers."
                )
            equality(field, value)
            filters[field] = value
    extra, metadata = parse_extra(args.raw_filter)
    try:
        now = (
            timestamp(os.environ["NEBIUS_AUDIT_LOG_NOW"])
            if "NEBIUS_AUDIT_LOG_NOW" in os.environ
            else datetime.now(timezone.utc)
        )
        end = timestamp(args.end) if args.end is not None else now
        start = (
            timestamp(args.start)
            if args.start is not None
            else end - timedelta(hours=hours)
        )
    except (OverflowError, ValueError) as exc:
        raise InputError("The requested time window is out of range.") from exc
    if start >= end:
        raise InputError("start must precede end.")
    return Query(selector, filters, extra, metadata, utc(start), utc(end))


def valid_profile(value: str) -> bool:
    return (
        bool(value.strip())
        and len(value) <= 256
        and all(ord(c) >= 32 and ord(c) != 127 for c in value)
    )


def valid_token(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= 8192
        and all(32 <= ord(c) < 127 for c in value)
    )


def parse_object(output: str, stage: str) -> dict[str, Any]:
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    def no_constant(value):
        raise ValueError("nonfinite")

    try:
        return object_value(
            json.loads(
                output, object_pairs_hook=no_duplicates, parse_constant=no_constant
            ),
            stage,
        )
    except (ValueError, RecursionError) as exc:
        raise invalid_response(stage) from exc


def config_value(cli: Cli, key: str) -> str:
    value = cli.run(["config", "get", key], "configuration").strip()
    if not value:
        raise AuditError(
            "missing_scope",
            "configuration",
            "Required CLI scope is absent; provide tenant-id and region or project-id.",
        )
    return value


def resolve_caller(payload: dict[str, Any], tenant: str) -> dict[str, str]:
    variants = [
        name
        for name in ("user_profile", "service_account_profile", "anonymous_profile")
        if name in payload
    ]
    if len(variants) != 1:
        raise invalid_response("identity")
    if variants[0] == "anonymous_profile":
        raise AuditError(
            "authentication_failed",
            "identity",
            "Nebius returned an anonymous identity.",
        )
    profile = object_value(payload[variants[0]], "identity")
    if variants[0] == "user_profile":
        tenants = profile.get("tenants", [])
        if not isinstance(tenants, list) or not all(
            isinstance(item, dict) for item in tenants
        ):
            raise invalid_response("identity")
        matches = [item for item in tenants if item.get("tenant_id") == tenant]
        if len(matches) != 1:
            raise AuditError(
                "tenant_identity_unresolved",
                "identity",
                "Exactly one user identity for the selected tenant is required.",
            )
        identity = matches[0].get("tenant_user_account_id")
        kind, prefix = "tenant_user", "tenantuseraccount-"
    else:
        info = object_value(profile.get("info"), "identity")
        identity = object_value(info.get("metadata"), "identity").get("id")
        kind, prefix = "service_account", "serviceaccount-"
    if (
        not isinstance(identity, str)
        or not ID.fullmatch(identity)
        or not identity.startswith(prefix)
    ):
        raise invalid_response("identity")
    return {"kind": kind, "id": identity}


def resolve_region(args, cli: Cli, tenant: str) -> str:
    if args.region:
        return args.region
    project = args.project_id or config_value(cli, "parent-id")
    if not ID.fullmatch(project) or not project.startswith("project-"):
        raise AuditError(
            "missing_region",
            "region",
            "Provide region or a project in the selected tenant.",
        )
    payload = parse_object(
        cli.run(
            ["iam", "v2", "project", "get", "--id", project, "--format", "json"],
            "region",
        ),
        "region",
    )
    metadata = object_value(payload.get("metadata"), "region")
    if metadata.get("id") != project or metadata.get("parent_id") != tenant:
        raise AuditError(
            "scope_mismatch",
            "region",
            "The discovered project does not match the requested project and tenant.",
        )
    region = object_value(payload.get("spec"), "region").get("region")
    if not isinstance(region, str) or not REGION.fullmatch(region):
        raise AuditError(
            "missing_region",
            "region",
            "The project has no valid origin region; provide region explicitly.",
        )
    return region


def audit_command(
    args, query: Query, tenant: str, region: str, expression: str, token: str | None
) -> list[str]:
    command = [
        "audit",
        "v2",
        "audit-event",
        "list",
        "--parent-id",
        tenant,
        "--region",
        region,
        "--start",
        query.start,
        "--end",
        query.end,
        "--event-type",
        "control_plane",
        "--page-size",
        str(args.page_size),
        "--format",
        "json",
    ]
    if expression:
        command.extend(["--filter", expression])
    if token:
        command.extend(["--page-token", token])
    return command


def parse_page(output: str, page_size: int) -> tuple[list[dict[str, Any]], str | None]:
    payload = parse_object(output, "audit")
    if payload and not ({"items", "next_page_token"} & payload.keys()):
        raise invalid_response("audit")
    events = payload.get("items", [])
    token = payload.get("next_page_token", "")
    if (
        not isinstance(events, list)
        or len(events) > page_size
        or not valid_token(token)
    ):
        raise invalid_response("audit")
    for event in events:
        if not isinstance(event, dict):
            raise invalid_response("audit")
        if not all(
            isinstance(event.get(key), str) and event[key]
            for key in ("id", "time", "type", "action", "status", "source")
        ):
            raise invalid_response("audit")
        if not isinstance(event.get("service"), dict) or not isinstance(
            event["service"].get("name"), str
        ):
            raise invalid_response("audit")
        for key in (
            "authentication",
            "authorization",
            "resource",
            "request",
            "response",
        ):
            if key in event and not isinstance(event[key], dict):
                raise invalid_response("audit")
    return events, token or None


def text_field(value: Any) -> str | None:
    # Safe protocol identifiers only. Names have their own explicit opt-in below.
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/+@=\-]{1,512}", value):
        return value
    return None


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def sanitize_event(event: dict[str, Any], include_pii: bool) -> dict[str, Any]:
    subject = as_dict(as_dict(event.get("authentication")).get("subject"))
    metadata = as_dict(as_dict(event.get("resource")).get("metadata"))
    result = {
        key: text_field(event.get(key))
        for key in ("id", "time", "type", "source", "action", "status")
    }
    result.update(
        {
            "service": text_field(as_dict(event.get("service")).get("name")),
            "resource": {key: text_field(metadata.get(key)) for key in ("id", "type")},
            "subject": {
                key: text_field(subject.get(key))
                for key in ("tenant_user_id", "service_account_id")
            },
            "request_id": text_field(as_dict(event.get("request")).get("request_id")),
            "response_status_code": text_field(
                as_dict(event.get("response")).get("status_code")
            ),
            "event_authorized": as_dict(event.get("authorization")).get("authorized")
            if isinstance(as_dict(event.get("authorization")).get("authorized"), bool)
            else None,
        }
    )
    if include_pii:
        for target, source in (
            (result["resource"], metadata),
            (result["subject"], subject),
        ):
            if isinstance(source.get("name"), str):
                target["name"] = source["name"][:512]
    return result


def new_report(args, query: Query) -> dict[str, Any]:
    return {
        "mode": "dry_run" if args.dry_run else "query",
        "scope": {
            "tenant_id": args.tenant_id,
            "region": args.region,
            "start": query.start,
            "end": query.end,
            "event_type": "control_plane",
            "selector": dict(query.selector),
            "filters": query.filters,
            "extra_predicates": query.extra_metadata,
            "profile_source": "explicit" if args.profile else "configured",
        },
        "caller": None,
        "access": {"status": "not_checked", "verified_pages": 0},
        "events": [],
        "complete": False,
        "errors": [],
        "next_page_token": args.page_token,
    }


def execute(args, query: Query, report: dict[str, Any]) -> None:
    cli = Cli(args.timeout)
    cli.profile = args.profile
    selected = cli.run(["profile", "current"], "configuration").strip()
    if not valid_profile(selected) or (
        args.profile is not None and selected != args.profile
    ):
        raise AuditError(
            "configuration_error",
            "configuration",
            "The CLI did not resolve the selected profile consistently.",
        )
    cli.profile = selected
    tenant = args.tenant_id or config_value(cli, "tenant-id")
    if not ID.fullmatch(tenant) or not tenant.startswith("tenant-"):
        raise AuditError(
            "missing_scope",
            "configuration",
            "Provide a valid tenant-id or configure the selected CLI profile.",
        )
    report["scope"]["tenant_id"] = tenant
    caller = resolve_caller(
        parse_object(
            cli.run(["iam", "whoami", "--format", "json"], "identity"), "identity"
        ),
        tenant,
    )
    report["caller"] = caller
    region = resolve_region(args, cli, tenant)
    report["scope"]["region"] = region
    expression = compile_filter(
        query.selector, query.filters, query.extra, caller["id"]
    )
    if query.selector["kind"] == "current_subject":
        report["scope"]["selector"]["id"] = caller["id"]
    token = args.page_token
    seen = {token} if token else set()
    for _ in range(args.max_pages):
        output = cli.run(
            audit_command(args, query, tenant, region, expression, token), "audit"
        )
        events, following = parse_page(output, args.page_size)
        report["events"].extend(
            sanitize_event(event, args.include_pii) for event in events
        )
        report["access"]["status"] = "verified"
        report["access"]["verified_pages"] += 1
        if following in seen:
            report["next_page_token"] = None
            raise AuditError(
                "pagination_cycle",
                "audit",
                "Nebius repeated a continuation token; narrow the query and retry.",
            )
        report["next_page_token"] = following
        if following is None:
            report["complete"] = True
            return
        seen.add(following)
        token = following
    raise AuditError(
        "page_limit",
        "audit",
        "The page limit was reached; results cover only part of the query.",
    )


def print_report(report: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
        return
    scope = report["scope"]
    print(
        f"Mode: {report['mode']}; audit access: {report['access']['status']}; complete: {report['complete']}"
    )
    print(
        f"Tenant: {scope['tenant_id'] or 'unresolved'}; region: {scope['region'] or 'unresolved'}"
    )
    print(
        f"Window: {scope['start']} to {scope['end']}; selector: {scope['selector']['kind']}"
    )
    if scope["selector"].get("id"):
        print(f"Selector ID: {scope['selector']['id']}")
    print("Filters: " + json.dumps(scope["filters"], sort_keys=True))
    if scope["extra_predicates"]:
        print(
            "Extra predicates (values omitted): "
            + json.dumps(scope["extra_predicates"], sort_keys=True)
        )
    if report["caller"]:
        print(f"Caller: {report['caller']['id']}")
    print(f"Events: {len(report['events'])}")
    for event in report["events"]:
        actor = (
            event["subject"].get("tenant_user_id")
            or event["subject"].get("service_account_id")
            or "unknown"
        )
        print(
            f" - {event['time']} {event['service']} {event['action']} {event['status']} resource={event['resource']['id']} actor={actor} request={event['request_id']} response={event['response_status_code']}"
        )
        if "name" in event["subject"] or "name" in event["resource"]:
            print(
                "   Names: "
                + json.dumps(
                    {
                        "subject": event["subject"].get("name"),
                        "resource": event["resource"].get("name"),
                    },
                    ensure_ascii=True,
                )
            )
    if report["next_page_token"]:
        print("Continuation token: " + json.dumps(report["next_page_token"]))
        print(
            "Resume with the same tenant, region, filters, selector, absolute start/end and --page-token."
        )
    if report["mode"] == "dry_run":
        print(
            "Offline preview only. Identity and audit access are unverified; configured values remain unresolved."
        )
    for error in report["errors"]:
        print(f"Error [{error['stage']}/{error['code']}]: {error['message']}")


def main(argv: list[str] | None = None) -> int:
    report = None
    args = None
    try:
        args = build_parser().parse_args(argv)
        query = validate(args)
        report = new_report(args, query)
        if not args.dry_run:
            execute(args, query, report)
        print_report(report, args.format)
        return 0
    except AuditError as exc:
        if report is None:
            print(
                json.dumps({"errors": [exc.record()]}, ensure_ascii=True),
                file=sys.stderr,
            )
        else:
            report["errors"].append(exc.record())
            if exc.stage == "audit":
                report["access"]["status"] = (
                    "denied"
                    if exc.code == "permission_denied"
                    else report["access"]["status"]
                )
            print_report(report, args.format)
        return 2 if isinstance(exc, InputError) else 1
    except KeyboardInterrupt:
        if report is not None and args is not None:
            report["errors"].append(
                {
                    "code": "cancelled",
                    "stage": "query",
                    "message": "The query was interrupted.",
                }
            )
            print_report(report, args.format)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
