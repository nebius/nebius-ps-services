"""Operator-side orchestration and exclusive private ZIP publication."""

from __future__ import annotations

import concurrent.futures
import hashlib
import ipaddress
import json
import os
import re
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .probe import bounded_command, read_regular, valid_remote_name
from .projection import MAX_BYTES, SECRET, project
from .worker import scope

SCHEMA = "nebius-vpngw/support-bundle-v1"
OUTCOMES = {
    "ok",
    "partial",
    "unavailable",
    "timeout",
    "changed",
    "not-applicable",
    "invalid-data",
    "source-limit",
}
PROBE_NAME = re.compile(r"[a-z0-9][a-z0-9_./-]{0,160}\Z")
TOTAL_LIMIT = 64 * 1024 * 1024
ZIP_LIMIT = 25 * 1024 * 1024


def operation_references(value: Any) -> list[str]:
    """Read only explicitly named provider IDs, never local execution identifiers."""
    pending = [value]
    seen = set()
    result = set()
    for _ in range(4096):
        if not pending:
            break
        item = pending.pop()
        if isinstance(item, (dict, list)):
            if id(item) in seen:
                continue
            seen.add(id(item))
        if isinstance(item, dict):
            for key, child in list(item.items())[:4096]:
                if key in {
                    "cloud_operation_id",
                    "accepted_cloud_operation_id",
                    "provider_operation_id",
                }:
                    if isinstance(child, str) and re.fullmatch(
                        r"[A-Za-z][A-Za-z0-9-]{0,127}", child
                    ):
                        result.add(child)
                elif isinstance(child, (dict, list)):
                    pending.append(child)
        elif isinstance(item, list):
            pending.extend(item[:4096])
    return sorted(result)[:16]


def lifecycle_resources(value: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Accept only references recorded for this exact selected group/project."""
    from ..deploy.vm_ha_lifecycle import VMHALifecycleState

    try:
        VMHALifecycleState.from_dict(value)
    except (ValueError, TypeError, KeyError, AttributeError):
        return {}
    identity = value.get("identity") if isinstance(value, dict) else None
    if (
        not isinstance(identity, dict)
        or identity.get("project_id") != config["project_id"]
        or identity.get("gateway_name") != config["gateway_group"]["name"]
    ) or identity.get("cluster_id") != (config["gateway_group"].get("vm_ha") or {}).get(
        "cluster_id"
    ):
        return {}
    result: dict[str, Any] = {"route_targets": []}
    allocation = identity.get("allocation_id")
    if isinstance(allocation, str) and re.fullmatch(r"[A-Za-z0-9-]{1,128}", allocation):
        result["allocation_id"] = allocation
    for item in identity.get("route_targets", [])[:32]:
        try:
            target = json.loads(item)
            if target.get("project_id") == config["project_id"] and all(
                isinstance(target.get(key), str)
                and re.fullmatch(r"[A-Za-z0-9-]{1,128}", target[key])
                for key in ("network_id", "workload_subnet_id", "route_table_id")
            ):
                result["route_targets"].append(
                    {
                        key: target[key]
                        for key in (
                            "project_id",
                            "network_id",
                            "workload_subnet_id",
                            "route_table_id",
                        )
                    }
                )
        except (ValueError, TypeError, AttributeError):
            continue
    return result


def run_worker(request: dict[str, Any], timeout: float) -> tuple[list[dict[str, Any]], str]:
    try:
        raw, outcome, _code = bounded_command(
            (
                [sys.executable, "--support-bundle-worker"]
                if getattr(sys, "frozen", False)
                else [sys.executable, "-m", "nebius_vpngw.support_bundle.worker"]
            ),
            stdin=json.dumps(request).encode(),
            timeout=timeout,
            limit=32 * MAX_BYTES,
        )
    except (OSError, ValueError):
        return [], "unavailable"
    records = []
    for line in raw.splitlines():
        try:
            record = json.loads(line)
            if isinstance(record, dict):
                records.append(record)
            else:
                outcome = "invalid-data"
        except ValueError:
            outcome = "invalid-data"
    return records, outcome


def time_window(since: str | None, now: datetime) -> tuple[str, str]:
    start = (
        datetime.fromisoformat(since.replace("Z", "+00:00")) if since else now - timedelta(days=1)
    )
    if start.tzinfo is None or not now - timedelta(days=7) <= start <= now:
        raise ValueError("Since must include a timezone and be within the previous seven days.")
    return start.astimezone(timezone.utc).isoformat(), now.isoformat()


def record_metadata(record: dict[str, Any]) -> dict[str, int | float]:
    return {
        key: record[key]
        for key in (
            "observed_at",
            "duration_seconds",
            "exit_code",
            "omitted_fields",
            "truncated_fields",
        )
        if type(record.get(key)) in (int, float)
        and abs(record[key]) <= 10**20
        and (record[key] >= 0 or key == "exit_code")
    }


def trim_oldest_logs(entries: dict[str, bytes], manifest: list[dict[str, Any]]) -> bool:
    """Remove an oldest-first batch across all journals, retaining newer evidence."""
    logs = {}
    ordered = []
    for name, data in entries.items():
        if "/logs/" not in name:
            continue
        value = json.loads(data)
        if not isinstance(value, list):
            continue
        logs[name] = value
        for index, item in enumerate(value):
            timestamp = item.get("timestamp") if isinstance(item, dict) else None
            if isinstance(timestamp, str) and timestamp.isdecimal():
                ordered.append((int(timestamp), name, index))
    if not ordered:
        return False
    remove: dict[str, set[int]] = {}
    for _, name, index in sorted(ordered)[: max(1, len(ordered) // 2)]:
        remove.setdefault(name, set()).add(index)
    for name, indices in remove.items():
        data = json.dumps(
            [item for index, item in enumerate(logs[name]) if index not in indices],
            sort_keys=True,
            indent=2,
        ).encode()
        entries[name] = data
        for row in manifest:
            if row["file"] == name:
                row.update(
                    outcome="source-limit", bytes=len(data), sha256=hashlib.sha256(data).hexdigest()
                )
                row["truncated_events"] = row.get("truncated_events", 0) + len(indices)
    return True


def collect(
    config_path: Path,
    *,
    project_id: str | None = None,
    region: str | None = None,
    output: Path | None = None,
    since: str | None = None,
    runner: Callable[[dict[str, Any], float], tuple[list[dict[str, Any]], str]] = run_worker,
) -> tuple[Path, int]:
    now = datetime.now(timezone.utc)
    start, end = time_window(since, now)
    deadline = time.monotonic() + 300
    config = scope(config_path, project_id, region)
    group = config["gateway_group"]
    destination = output or Path(f"nebius-vpngw-support-{now:%Y%m%dT%H%M%SZ}.zip")
    if destination.suffix.lower() != ".zip" or destination.exists() or destination.is_symlink():
        raise ValueError("Choose a new .zip output file; existing files are never overwritten.")
    entries: dict[str, bytes] = {}
    manifest: list[dict[str, Any]] = []
    total = 0

    def add(name: str, value: Any, outcome: str = "ok", **metadata: Any) -> None:
        nonlocal total
        if not PROBE_NAME.fullmatch(name) or any(
            part in {"", ".", ".."} for part in name.split("/")
        ):
            name, value, outcome = "invalid-probe", {}, "invalid-data"
        omitted = [0]
        limited = [0]
        clean = project(value, omitted=omitted, limited=limited)
        truncated = limited[0] + int(metadata.get("truncated_fields", 0))
        if outcome == "ok" and truncated:
            outcome = "source-limit"
        elif outcome == "ok" and value and not clean and omitted[0]:
            outcome = "partial"
        data = json.dumps(clean, sort_keys=True, indent=2).encode()
        if SECRET.search(data.decode()):
            data, outcome = b"{}", "invalid-data"
        if len(data) > MAX_BYTES or total + len(data) > TOTAL_LIMIT:
            data, outcome = b"{}", "source-limit"
        entry = name + ".json"
        if entry in entries:
            name = f"invalid-duplicate-{len(manifest)}"
            entry = name + ".json"
            data, outcome = b"{}", "invalid-data"
        entries[entry] = data
        total += len(data)
        manifest.append(
            {
                "file": entry,
                "outcome": outcome if outcome in OUTCOMES else "invalid-data",
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "omitted_fields": omitted[0] + int(metadata.get("omitted_fields", 0)),
                "truncated_fields": truncated,
                "observed_at": metadata.get("observed_at", time.time()),
                "duration_seconds": metadata.get("duration_seconds", 0),
                "exit_code": metadata.get("exit_code"),
            }
        )

    add("operator/topology", config)
    from .. import __version__

    add(
        "operator/runtime",
        {
            "version": __version__,
            "python_version": sys.version.split()[0],
        },
    )
    operations: set[str] = set()
    resources: dict[str, Any] = {}
    paths = [(config_path.with_name(config_path.name + ".vm-ha-lifecycle.json"), "")]
    state_root = Path.home() / ".local/state/nebius-vpngw"
    for owner in ("apply", "repair", "destroy", "replacement", "ordinary"):
        root = state_root / owner / config["project_id"] / group["name"]
        if root.is_dir() and not root.is_symlink():
            # Bounded, scoped state only; never read credentials or arbitrary file types.
            for pattern, label in (
                ("*.json", ""),
                ("failures/*.json", "first"),
                ("failures/latest/*.json", "latest"),
                ("runs/*/context.json", ""),
            ):
                paths.extend((path, label) for path in sorted(root.glob(pattern))[:32])
    for index, (path, label) in enumerate(paths[:128]):
        try:
            value = json.loads(read_regular(path))
            add(f"operator/state/{index}", {label: value} if label else value)
            operations.update(operation_references(value))
            if index == 0:
                resources = lifecycle_resources(value, config)
                add("operator/scoped-resources", resources)
        except FileNotFoundError:
            add(f"operator/state/{index}", {}, "not-applicable")
        except (OSError, ValueError):
            add(f"operator/state/{index}", {}, "invalid-data")
    try:
        from packaging.utils import parse_wheel_filename

        from ..deploy.ssh_push import SSHPush

        wheel, source = SSHPush().select_vm_ha_agent_wheel()
        _name, version, _build, _tags = parse_wheel_filename(wheel.name)
        descriptor = os.open(wheel, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            import stat

            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > 128 * MAX_BYTES:
                raise ValueError("source-limit")
            digest = hashlib.sha256()
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                while chunk := stream.read(65536):
                    if time.monotonic() >= deadline:
                        raise ValueError("timeout")
                    digest.update(chunk)
            after = os.fstat(descriptor)
            changed = (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size)
            add(
                "operator/selected-artifact",
                {
                    "artifact_sha256": digest.hexdigest(),
                    "version": str(version),
                    "source_kind": source,
                },
                "changed" if changed else "ok",
            )
        finally:
            os.close(descriptor)
    except Exception:
        add("operator/selected-artifact", {}, "unavailable")
    base = {
        "config": str(config_path.absolute()),
        "project": project_id,
        "region": region,
        "since": start,
        "until": end,
        "operations": sorted(operations)[:16],
        "resources": resources,
    }
    from ..deploy.ssh_client_auth import operator_identity

    identity = operator_identity()
    if identity is not None:
        base["identity"] = str(identity._key_path or identity._identity_file)
    cloud_records, cloud_outcome = runner(
        {**base, "action": "cloud"}, min(30, deadline - time.monotonic())
    )
    targets = {}
    for record in cloud_records:
        name = record.get("probe", "invalid-probe")
        if not isinstance(name, str) or not re.fullmatch(
            r"(?:target/[0-9]+|cloud/(?:auxiliary|instance|subnet|network|routes|routes-limit|allocation|security-group|security-rules|security-rules-limit|attachments|operation|workload-routes|route-table)/[0-9]+|worker)",
            name,
        ):
            name = "invalid-probe"
        if isinstance(name, str) and name.startswith("target/"):
            try:
                index = int(name.split("/")[-1])
                if 0 <= index < group["instance_count"]:
                    targets[f"{group['name']}-{index}"] = str(
                        ipaddress.ip_address(record["data"]["address"])
                    )
            except (ValueError, TypeError, KeyError):
                pass
        add(
            "operator/" + str(name),
            record.get("data"),
            record.get("outcome", "invalid-data"),
            **record_metadata(record),
        )
    if cloud_outcome != "ok":
        add("operator/cloud-worker", {}, cloud_outcome)
    for index in range(group["instance_count"]):
        name = f"{group['name']}-{index}"
        if name not in targets:
            try:
                targets[name] = str(ipaddress.ip_address(group["external_ips"][index][0]))
            except (KeyError, IndexError, TypeError, ValueError):
                pass

    def member(index: int) -> tuple[int, list[dict[str, Any]], str]:
        name = f"{group['name']}-{index}"
        remaining = deadline - time.monotonic()
        if name not in targets or remaining <= 0:
            return index, [], "unavailable" if remaining > 0 else "timeout"
        records, outcome = runner(
            {**base, "action": "remote", "targets": targets, "hostname": name}, min(220, remaining)
        )
        return index, records, outcome

    def add_member_record(index: int, record: dict[str, Any]) -> None:
        probe_name = record.get("probe")
        if not valid_remote_name(probe_name):
            add(f"nodes/{index}/invalid-probe", {}, "invalid-data")
            return
        add(
            f"nodes/{index}/" + str(probe_name),
            record.get("data"),
            record.get("outcome", "invalid-data"),
            **record_metadata(record),
        )

    logs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for index, records, outcome in pool.map(member, range(group["instance_count"])):
            snapshots = {
                item.get("probe"): item.get("data")
                for item in records
                if isinstance(item.get("probe"), str)
            }
            first, last = snapshots.get("ha-0/native"), snapshots.get("ha-1/native")
            if (
                isinstance(first, dict)
                and isinstance(last, dict)
                and any(
                    first.get(key) != last.get(key)
                    for key in (
                        "boot_id",
                        "guard_boot_id",
                        "generation_id",
                        "apply_operation_id",
                        "observed_owner_node_id",
                        "data_plane_mode",
                        "apply_locked",
                    )
                )
            ):
                add(f"nodes/{index}/observation-change", {"changed": True}, "changed")
            if not records or outcome != "ok":
                add(f"nodes/{index}/collection", {}, outcome if outcome != "ok" else "unavailable")
            if len(records) > 256:
                add(f"nodes/{index}/record-limit", {}, "source-limit")
            for record in sorted(
                records, key=lambda item: str(item.get("probe")).startswith("logs/")
            )[:256]:
                if str(record.get("probe")).startswith("logs/"):
                    logs.append((index, record))
                else:
                    add_member_record(index, record)
    try:
        current_config = scope(config_path, project_id, region)
        if project(current_config) != project(config):
            add("operator/topology-end", current_config, "changed")
    except (OSError, ValueError, RuntimeError):
        add("operator/topology-end", {}, "changed")
    # All members' current facts take precedence over historical logs.
    for index, record in logs:
        add_member_record(index, record)
    partial = any(item["outcome"] not in {"ok", "not-applicable"} for item in manifest)
    # Build only projected data in a private directory beside the destination.
    with tempfile.TemporaryDirectory(prefix=".vpngw-support-", dir=destination.parent) as temp:
        archive = Path(temp) / "bundle.zip"
        while True:
            header = {
                "schema": SCHEMA,
                "created_at": end,
                "since": start,
                "complete": not partial,
                "probes": manifest,
                "privacy": "Operational identifiers retained; credentials and arbitrary text excluded.",
            }
            summary = (
                "VPNGW private support bundle\n\n"
                + (
                    "PARTIAL: inspect manifest.json for unavailable or truncated probes.\n"
                    if partial
                    else "Collection completed.\n"
                )
                + "Contains operational identifiers. Attach only to a private support ticket.\n"
                + "Raw logs, credentials and unknown fields are excluded; omission counts are recorded.\n"
                + "Collection is observational and does not establish HA readiness or authorize recovery.\n"
            )
            with zipfile.ZipFile(
                archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as bundle:
                bundle.writestr("manifest.json", json.dumps(header, sort_keys=True, indent=2))
                bundle.writestr("README.txt", summary)
                for name, data in entries.items():
                    bundle.writestr(name, data)
            with zipfile.ZipFile(archive) as bundle:
                for name in bundle.namelist():
                    if SECRET.search(name) or SECRET.search(bundle.read(name).decode("utf-8")):
                        raise ValueError("Unsafe bundle export rejected.")
            if archive.stat().st_size <= ZIP_LIMIT:
                break
            if trim_oldest_logs(entries, manifest):
                partial = True
                continue
            candidates = [name for name in entries if "/logs/" in name] or list(entries)
            if not candidates:
                raise ValueError("Bundle metadata exceeds the archive size limit.")
            removed = candidates[0]
            del entries[removed]
            for item in manifest:
                if item["file"] == removed:
                    item.update(outcome="source-limit", bytes=0, sha256=None)
            partial = True
        os.chmod(archive, 0o600)
        with archive.open("rb") as stream:
            os.fsync(stream.fileno())
        os.link(archive, destination)  # Atomic exclusive publication; never replace a destination.
    return destination, 1 if partial else 0
