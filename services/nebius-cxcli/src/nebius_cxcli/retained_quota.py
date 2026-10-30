"""Conservative quota credit for retained filesystems and address allocations."""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .component_instances import component_instance_id
from .quota_checks import QuotaRequirement
from .runtime_config import to_plain_data


def planned_retained_quota_credit(
    config: Any,
    infra_dir: Path,
    manifest: Mapping[str, Any],
    *,
    runtime_env: Mapping[str, str] | None,
    plan_runner: Callable[..., Any],
    plan_reader: Callable[..., Any],
    warn: Callable[[str], Any],
) -> tuple[QuotaRequirement, ...]:
    payload = to_plain_data(config)
    if not isinstance(payload, Mapping) or not any(
        row.get("id") in {"sfs", "wireguard-gw", "nat-gateway", "tailscale-gw"}
        and row.get("enabled", False)
        for row in payload.get("infra", {}).get("components", [])
    ):
        return ()
    credits: tuple[QuotaRequirement, ...] = ()
    sources = manifest.get("render", {}).get("module_sources", [])
    from .deployment_cli import quota_plan_runtime_env

    for kind, matching_rows in (
        ("storage", _matching_generated_filesystems(payload, manifest)),
        ("allocations", _matching_generated_allocations(payload, manifest)),
    ):
        if not matching_rows:
            continue
        credit_config = {**payload, "infra": {**payload["infra"], "components": matching_rows}}
        targets = _filesystem_targets(matching_rows, sources) if kind == "storage" else ()
        if kind == "storage" and not targets:
            continue
        try:
            # Storage has no gateway bootstrap dependency. Its observation-only
            # plan must remain available while a new gateway is unresolved.
            # This plan never supplies deployment admission or apply authority.
            owner = (
                nullcontext(dict(runtime_env or {}))
                if kind == "storage"
                else quota_plan_runtime_env(infra_dir, manifest, runtime_env)
            )
            with (
                owner as plan_env,
                tempfile.TemporaryDirectory(prefix=".cxcli-quota-", dir=infra_dir) as directory,
            ):
                plan_file = Path(directory) / "quota.tfplan"
                plan_runner(
                    infra_dir,
                    extra_env=plan_env,
                    initialize=False,
                    quiet=True,
                    plan_file=plan_file,
                    **({"targets": targets} if targets else {}),
                )
                plan = plan_reader(
                    infra_dir,
                    extra_env=plan_env,
                    initialize=False,
                    quiet=True,
                    plan_file=plan_file,
                )
                assess = (
                    retained_filesystem_quota if kind == "storage" else retained_allocation_quota
                )
                credits += assess(credit_config, sources, plan)
                if kind == "allocations":
                    from .tailscale_quota import retained_compute

                    credits += retained_compute(credit_config, sources, plan)
        except Exception:
            # Failure in one group cannot erase independently proven credit.
            warn(f"Retained {kind} quota: fresh plan evidence unavailable; checking full demand.")
    return credits


def _filesystem_targets(
    rows: Sequence[Mapping[str, Any]], sources: Sequence[Mapping[str, Any]]
) -> tuple[str, ...]:
    targets = []
    for row in rows:
        matches = [
            source
            for source in sources
            if source.get("component_id") == "sfs"
            and source.get("instance_id") == component_instance_id(row)
        ]
        if len(matches) != 1:
            continue
        name = matches[0].get("module_name")
        if (
            isinstance(name, str)
            and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name)
            and sum(source.get("module_name") == name for source in sources) == 1
        ):
            targets.append(f"module.{name}")
    return tuple(sorted(set(targets)))


def _matching_generated_filesystems(
    config: Mapping[str, Any], manifest: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    """A generated plan cannot prove retention for changed filesystem inputs."""
    rendered = manifest.get("runtime_config")
    if not isinstance(rendered, Mapping) or config.get("client_info", {}).get(
        "nebius"
    ) != rendered.get("client_info", {}).get("nebius"):
        return []
    rendered_rows = rendered.get("infra", {}).get("components", [])
    eligible = []
    for row in config.get("infra", {}).get("components", []):
        if row.get("id") != "sfs" or not row.get("enabled", False):
            continue
        matches = [
            previous
            for previous in rendered_rows
            if previous.get("id") == "sfs"
            and previous.get("enabled", False)
            and component_instance_id(previous) == component_instance_id(row)
        ]
        if len(matches) == 1 and row.get("inputs") == matches[0].get("inputs"):
            eligible.append(row)
    return eligible


def _matching_generated_allocations(
    config: Mapping[str, Any], manifest: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    rendered = manifest.get("runtime_config")
    if not isinstance(rendered, Mapping) or config.get("client_info", {}).get(
        "nebius"
    ) != rendered.get("client_info", {}).get("nebius"):
        return []
    eligible = []
    for row in config.get("infra", {}).get("components", []):
        if row.get("id") not in {"wireguard-gw", "nat-gateway", "tailscale-gw"} or not row.get(
            "enabled", False
        ):
            continue
        matches = [
            previous
            for previous in rendered.get("infra", {}).get("components", [])
            if previous.get("id") == row["id"]
            and component_instance_id(previous) == component_instance_id(row)
        ]
        if len(matches) == 1:
            current, previous = dict(row), dict(matches[0])
            if row["id"] == "tailscale-gw":
                current.pop("tailscale", None)
                previous.pop("tailscale", None)
            if current == previous:
                eligible.append(row)
    return eligible


def retained_allocation_quota(
    config: Mapping[str, Any],
    module_sources: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any],
) -> tuple[QuotaRequirement, ...]:
    scope = config.get("client_info", {}).get("nebius", {})
    credits: list[QuotaRequirement] = []
    for row in config.get("infra", {}).get("components", []):
        if row.get("id") not in {"wireguard-gw", "nat-gateway", "tailscale-gw"} or not row.get(
            "enabled", False
        ):
            continue
        instance = component_instance_id(row)
        sources = [
            item
            for item in module_sources
            if item.get("component_id") == row["id"] and item.get("instance_id") == instance
        ]
        if len(sources) != 1 or not sources[0].get("module_name"):
            continue
        prefix = f"module.{sources[0]['module_name']}."
        resources = [
            item
            for item in plan.get("resource_changes", [])
            if item.get("address", "").startswith(prefix)
            and item.get("type") == "nebius_vpc_v1_allocation"
            and item.get("mode") == "managed"
        ]
        addresses = [item.get("address") for item in resources]
        identities = [(item.get("change", {}).get("before") or {}).get("id") for item in resources]
        known_ids = [identity for identity in identities if identity]
        if len(set(addresses)) != len(addresses) or len(set(known_ids)) != len(known_ids):
            continue
        project = row.get("inputs", {}).get("parent_id") or scope.get("project_id")
        region = row.get("inputs", {}).get("region") or scope.get("region_id")
        for item in resources:
            change = item.get("change", {})
            before, after = change.get("before") or {}, change.get("after") or {}
            unknown = change.get("after_unknown") or {}
            if (
                change.get("actions") not in (["no-op"], ["update"])
                or not project
                or not region
                or not before.get("id")
                or after.get("id") != before["id"]
                or before.get("parent_id") != project
                or after.get("parent_id") != project
                or not isinstance(unknown, Mapping)
                or any(unknown.get(k) for k in ("id", "parent_id", "ipv4_public", "ipv4_private"))
            ):
                continue
            kinds = [k for k in ("ipv4_public", "ipv4_private") if before.get(k) is not None]
            if (
                len(kinds) != 1
                or not isinstance(before[kinds[0]], Mapping)
                or any(before.get(k) != after.get(k) for k in ("ipv4_public", "ipv4_private"))
            ):
                continue
            names = ["vpc.allocation.count"]
            if kinds[0] == "ipv4_public":
                names.append("vpc.ipv4-address.public.count")
            credits.extend(
                QuotaRequirement(
                    row["id"],
                    instance,
                    instance,
                    name,
                    region,
                    1,
                    "retained address allocation in fresh Terraform plan",
                )
                for name in names
            )
    return tuple(credits)


def _positive_bytes(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def retained_filesystem_quota(
    config: Mapping[str, Any],
    module_sources: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any],
) -> tuple[QuotaRequirement, ...]:
    """Unknown identity/actions/size never earn credit against configured demand."""
    scope = config.get("client_info", {}).get("nebius", {})
    changes = plan.get("resource_changes", [])
    credits: list[QuotaRequirement] = []
    for row in config.get("infra", {}).get("components", []):
        if row.get("id") != "sfs" or not row.get("enabled", False):
            continue
        instance = component_instance_id(row)
        sources = [
            item
            for item in module_sources
            if item.get("component_id") == "sfs" and item.get("instance_id") == instance
        ]
        if len(sources) != 1 or not sources[0].get("module_name"):
            continue
        prefix = f"module.{sources[0]['module_name']}."
        resources = [
            item
            for item in changes
            if item.get("address", "").startswith(prefix)
            and item.get("type") == "nebius_compute_v1_filesystem"
            and item.get("mode") == "managed"
        ]
        if len(resources) != 1:
            continue
        change = resources[0].get("change", {})
        if change.get("actions") not in (["no-op"], ["update"]):
            continue
        before, after = change.get("before") or {}, change.get("after") or {}
        unknown = change.get("after_unknown") or {}
        if not isinstance(unknown, Mapping) or any(
            unknown.get(key) for key in ("id", "parent_id", "type", "size_bytes")
        ):
            continue
        inputs = row.get("inputs", {})
        project = inputs.get("parent_id") or scope.get("project_id")
        region = inputs.get("region") or scope.get("region_id")
        kind = str(inputs.get("type", "NETWORK_SSD")).upper()
        size_gib = inputs.get("size_gib")
        if (
            not project
            or not region
            or not before.get("id")
            or after.get("id") != before["id"]
            or before.get("parent_id") != project
            or after.get("parent_id") != project
            or before.get("type") != kind
            or after.get("type") != kind
            or not _positive_bytes(size_gib)
            or not _positive_bytes(before.get("size_bytes"))
            or not _positive_bytes(after.get("size_bytes"))
            or after["size_bytes"] != size_gib * 1024**3
        ):
            continue
        for quota_name, amount in (
            ("compute.filesystem.count", 1),
            (
                "compute.filesystem.size." + kind.lower().replace("_", "-"),
                min(before["size_bytes"], after["size_bytes"]),
            ),
        ):
            credits.append(
                QuotaRequirement(
                    component_id="sfs",
                    instance_id=instance,
                    component_label=instance,
                    quota_name=quota_name,
                    region=region,
                    required=amount,
                    reason="retained shared filesystem in fresh Terraform plan",
                )
            )
    return tuple(credits)
