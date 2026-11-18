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

from .diagnostics import clean_failure, clean_progress
from .probe import bounded_command, read_regular, valid_remote_name
from .projection import MAX_BYTES, project, project_journal, retention_priorities
from .redaction import REDACTION_POLICY, redact_text, unsafe_text
from .report import build_reports, collection_counts
from .worker import scope

SCHEMA = "nebius-vpngw/support-bundle-v3"
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
STATUS_BUDGET = 90


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


def run_status(
    request: dict[str, Any], timeout: float, on_progress: Callable[[dict[str, Any]], None] | None
) -> tuple[list[dict[str, Any]], str]:
    """Run the public CLI directly in the owned group, so cancellation reaps it."""
    if on_progress:
        on_progress({"stage": "status", "state": "started"})
    argv = (
        [sys.executable]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-m", "nebius_vpngw"]
    )
    if request.get("identity"):
        argv += ["--ssh-identity", request["identity"]]
    argv += ["status", "--local-config-file", request["config"], "--packet-details"]
    for field, flag in (("project", "--project-id"), ("region", "--region")):
        if request.get(field):
            argv += [flag, request[field]]
    started = time.time()
    errors: list[bytes] = []
    try:
        raw, outcome, code = bounded_command(
            argv, timeout=min(STATUS_BUDGET, timeout), stderr_out=errors
        )
    except OSError:
        raw, outcome, code = b"", "unavailable", None
    complete = outcome not in {"timeout", "source-limit"}
    record: dict[str, Any] = {
        "probe": "status",
        "data": {},
        "outcome": outcome,
        "observed_at": time.time(),
        "collection_started_at": started,
        "collection_finished_at": time.time(),
        "exit_code": code,
        "text": {
            "stdout": redact_text(raw.decode("utf-8", errors="replace"), complete=complete),
            "stderr": redact_text(
                b"".join(errors).decode("utf-8", errors="replace"), complete=complete
            ),
        },
    }
    event: dict[str, Any] = {
        "stage": "status",
        "state": "completed" if outcome == "ok" else "failed",
    }
    if outcome != "ok":
        record["failure"] = clean_failure(
            {
                "stage": "status",
                "code": outcome if outcome in {"timeout", "source-limit"} else "probe-failed",
            }
        )
        event["failure"] = record["failure"]
    if on_progress:
        on_progress(event)
    return [record], outcome


def run_worker(
    request: dict[str, Any],
    timeout: float,
    *,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    if request.get("action") == "status":
        return run_status(request, timeout, on_progress)
    records: list[dict[str, Any]] = []
    buffer = bytearray()
    invalid = False
    last_stage = "status" if request.get("action") == "status" else "discovery"
    attempted = False

    def receive(chunk: bytes) -> None:
        nonlocal buffer, invalid, last_stage, attempted
        buffer.extend(chunk)
        while b"\n" in buffer:
            line, _, rest = buffer.partition(b"\n")
            buffer = bytearray(rest)
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("invalid-data")
                if record.get("kind") == "progress":
                    event = clean_progress(record)
                    if event is not None:
                        last_stage = event["stage"]
                        attempted = attempted or last_stage == "connect"
                        if on_progress is not None:
                            on_progress(event)
                elif len(records) < 256:
                    records.append(record)
                else:
                    invalid = True
            except (ValueError, TypeError):
                invalid = True
        if len(buffer) > 4 * MAX_BYTES:
            raise ValueError("source-limit")

    errors: list[bytes] = []
    try:
        raw, outcome, _code = bounded_command(
            [sys.executable, "--support-bundle-worker"]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "nebius_vpngw.support_bundle.worker"],
            stdin=json.dumps(request).encode(),
            timeout=timeout,
            limit=32 * MAX_BYTES,
            stderr_out=errors,
            on_stdout=receive,
        )
    except OSError:
        outcome = "unavailable"
    except ValueError:
        outcome = "source-limit"
    if (buffer.strip() or invalid) and outcome == "ok":
        outcome = "invalid-data"
    if outcome != "ok":
        detail = clean_failure(
            {
                "stage": last_stage,
                "code": {
                    "timeout": "timeout",
                    "source-limit": "source-limit",
                    "invalid-data": "invalid-output",
                }.get(outcome, "probe-failed"),
                "connection_attempted": attempted,
            }
        )
        records.append({"probe": "worker", "data": {}, "outcome": outcome, "failure": detail})
        if on_progress is not None:
            on_progress(
                {"kind": "progress", "stage": last_stage, "state": "failed", "failure": detail}
            )
    if errors and b"".join(errors):
        records.append(
            {
                "probe": "startup",
                "data": {},
                "outcome": "unavailable",
                "text": {
                    "stderr": redact_text(
                        b"".join(errors).decode("utf-8", errors="replace"),
                        complete=outcome not in {"timeout", "source-limit"},
                    )
                },
            }
        )
    return records, outcome


def time_window(since: str | None, now: datetime) -> tuple[str, str]:
    start = (
        datetime.fromisoformat(since.replace("Z", "+00:00")) if since else now - timedelta(days=1)
    )
    if start.tzinfo is None or not now - timedelta(days=7) <= start <= now:
        raise ValueError("Since must include a timezone and be within the previous seven days.")
    return start.astimezone(timezone.utc).isoformat(), now.isoformat()


def record_metadata(record: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: record[key]
        for key in (
            "observed_at",
            "duration_seconds",
            "exit_code",
            "omitted_fields",
            "truncated_fields",
            "collection_started_at",
            "collection_finished_at",
        )
        if type(record.get(key)) in (int, float)
        and abs(record[key]) <= 10**20
        and (record[key] >= 0 or key == "exit_code")
    }

    if isinstance(record.get("text"), dict):
        result["text"] = record["text"]
    if "failure" in record:
        result["failure"] = clean_failure(record["failure"])
    return result


def journal_text(value: dict[str, Any]) -> str:
    return "".join(
        f"{item.get('timestamp', '?')} {item.get('message', '')}\n"
        for item in value.get("events", [])
        if isinstance(item, dict)
    )


def bounded_text(data: bytes, limit: int) -> bytes:
    if len(data) <= limit:
        return data
    prefix = data[:limit]
    return prefix.rsplit(b"\n", 1)[0] + b"\n" if b"\n" in prefix else b""


def unsafe_export(name: str, data: bytes, literals: tuple[str, ...]) -> bool:
    """Scan decoded values, so JSON quoting cannot hide or invent credentials."""
    if not name.endswith(".json"):
        return unsafe_text(data.decode("utf-8"), literals)
    pending = [json.loads(data)]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str) and unsafe_text(value, literals):
            return True
    return False


def synchronize_text(entries: dict[str, bytes], manifest: list[dict[str, Any]]) -> None:
    """Text views must reference only evidence retained after archive trimming."""
    for row in manifest:
        source = row.get("source_file")
        name = row["file"]
        if not source or name not in entries:
            continue
        if source not in entries:
            del entries[name]
            row.update(outcome="source-limit", bytes=0, sha256=None)
        elif "/logs/" in source and row.get("stream") == "stdout":
            source_value = json.loads(entries[source])
            data = bounded_text(journal_text(source_value).encode(), row["text_cap"])
            if "coverage" in source_value:
                row["coverage"] = source_value["coverage"].copy()
                if source_value["coverage"].get("truncation") != "none":
                    row["outcome"] = "source-limit"
            entries[name] = data
            row.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def trim_oldest_logs(entries: dict[str, bytes], manifest: list[dict[str, Any]]) -> bool:
    """Remove an oldest-first batch across all journals, retaining newer evidence."""
    logs = {}
    ordered = []
    for name, data in entries.items():
        if "/logs/" not in name or not name.endswith(".json"):
            continue
        value = json.loads(data)
        if not isinstance(value, dict) or not isinstance(value.get("events"), list):
            continue
        logs[name] = value
        events = value["events"]
        priorities = retention_priorities(events)
        for index, item in enumerate(events):
            timestamp = item.get("timestamp") if isinstance(item, dict) else None
            if isinstance(timestamp, str) and timestamp.isdecimal():
                priority = priorities[index]
                ordered.append((priority, int(timestamp), name, index))
    if not ordered:
        return False
    remove: dict[str, set[int]] = {}
    priority = min(item[0] for item in ordered)
    candidates = sorted(item for item in ordered if item[0] == priority)
    for _, _, name, index in candidates[: max(1, len(candidates) // 2)]:
        remove.setdefault(name, set()).add(index)
    for name, indices in remove.items():
        envelope = logs[name]
        envelope["events"] = [
            item for index, item in enumerate(envelope["events"]) if index not in indices
        ]
        envelope["coverage"]["discarded"] += len(indices)
        envelope["coverage"]["exported"] = len(envelope["events"])
        envelope["coverage"]["truncation"] = "oldest"
        data = json.dumps(envelope, sort_keys=True, indent=2).encode()
        entries[name] = data
        for row in manifest:
            if row["file"] == name:
                row.update(
                    outcome="source-limit", bytes=len(data), sha256=hashlib.sha256(data).hexdigest()
                )
                row["truncated_events"] = row.get("truncated_events", 0) + len(indices)
                row["coverage"] = envelope["coverage"].copy()
    return True


def collect(
    config_path: Path,
    *,
    project_id: str | None = None,
    region: str | None = None,
    output: Path | None = None,
    since: str | None = None,
    runner: Callable[[dict[str, Any], float], tuple[list[dict[str, Any]], str]] = run_worker,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[Path, int]:
    now = datetime.now(timezone.utc)
    start, end = time_window(since, now)
    deadline = time.monotonic() + 300
    config = scope(config_path, project_id, region)
    group = config["gateway_group"]
    literals = tuple(
        str(connection["psk"])
        for connection in config.get("connections", [])
        if isinstance(connection, dict)
        and isinstance(connection.get("psk"), str)
        and connection["psk"]
        and "${" not in connection["psk"]
    )

    def notify(event: dict[str, Any]) -> None:
        if progress is not None:
            progress(event)

    def execute(
        request: dict[str, Any], timeout: float, node: int | None = None
    ) -> tuple[list[dict[str, Any]], str]:
        if runner is run_worker:
            return run_worker(
                request, timeout, on_progress=lambda event: notify({**event, "node": node})
            )
        return runner(request, timeout)

    destination = output or Path(f"nebius-vpngw-support-{now:%Y%m%dT%H%M%SZ}.zip")
    if destination.suffix.lower() != ".zip" or destination.exists() or destination.is_symlink():
        raise ValueError("Choose a new .zip output file; existing files are never overwritten.")
    entries: dict[str, bytes] = {}
    manifest: list[dict[str, Any]] = []
    total = 0
    report_reserve = min(1024 * 1024, TOTAL_LIMIT // 8)
    log_allowance = MAX_BYTES

    def add(name: str, value: Any, outcome: str = "ok", **metadata: Any) -> None:
        nonlocal total
        if not PROBE_NAME.fullmatch(name) or any(
            part in {"", ".", ".."} for part in name.split("/")
        ):
            name, value, outcome = "invalid-probe", {}, "invalid-data"
        omitted = [0]
        limited = [0]
        clean = project(value, omitted=omitted, limited=limited)
        if "/logs/" in name and not (
            isinstance(clean, dict) and "coverage" in clean and "events" in clean
        ):
            clean, outcome = {}, "invalid-data"
        if isinstance(clean, dict) and "coverage" in clean and "events" in clean:
            for index, event in enumerate(clean["events"]):
                if isinstance(event.get("message"), str):
                    event["message"] = redact_text(event["message"], literals=literals)["content"]
                event["event_id"] = hashlib.sha256(
                    (name + ":" + str(index) + ":" + json.dumps(event, sort_keys=True)).encode()
                ).hexdigest()
            clean = project_journal(clean, omitted, limited, byte_limit=log_allowance)
        truncated = limited[0] + int(metadata.get("truncated_fields", 0))
        if outcome == "ok" and truncated:
            outcome = "source-limit"
        elif outcome == "ok" and value and not clean and omitted[0]:
            outcome = "partial"
        if "failure" in metadata:
            clean = {"failure": clean_failure(metadata["failure"]), "observation": clean}
        data = json.dumps(clean, sort_keys=True, indent=2).encode()
        if unsafe_export(name + ".json", data, literals):
            data, outcome = b"{}", "invalid-data"
        if len(data) > MAX_BYTES or total + len(data) > TOTAL_LIMIT - report_reserve:
            data, outcome = b"{}", "source-limit"
        entry = name + ".json"
        if entry in entries:
            name = f"invalid-duplicate-{len(manifest)}"
            entry = name + ".json"
            data, outcome = b"{}", "invalid-data"
        coverage = None
        if isinstance(clean, dict) and isinstance(clean.get("coverage"), dict):
            coverage = clean["coverage"].copy()
            if data == b"{}":
                coverage["discarded"] = coverage.get("discarded", 0) + coverage.get("exported", 0)
                coverage["exported"] = 0
                coverage["truncation"] = "oldest"
        entries[entry] = data
        total += len(data)
        manifest.append(
            {
                "file": entry,
                "record_id": hashlib.sha256(entry.encode()).hexdigest()[:24],
                "outcome": outcome if outcome in OUTCOMES else "invalid-data",
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "omitted_fields": omitted[0] + int(metadata.get("omitted_fields", 0)),
                "truncated_fields": truncated,
                "observed_at": metadata.get("observed_at", time.time()),
                "duration_seconds": metadata.get("duration_seconds", 0),
                "exit_code": metadata.get("exit_code"),
                "collection_started_at": metadata.get(
                    "collection_started_at", metadata.get("observed_at", time.time())
                ),
                "collection_finished_at": metadata.get(
                    "collection_finished_at", metadata.get("observed_at", time.time())
                ),
                "evidence_class": "historical-events"
                if "/logs/" in name
                else "current-samples"
                if name.endswith("resource-samples")
                else "current-snapshot",
                **({"coverage": coverage} if coverage is not None else {}),
            }
        )

        if "failure" in metadata:
            manifest[-1]["failure"] = clean_failure(metadata["failure"])
        texts = metadata.get("text")
        if isinstance(texts, dict):
            source_row = manifest[-1]
            allowance = max(0, MAX_BYTES - len(data))
            for stream in ("stderr", "stdout"):
                block = texts.get(stream)
                if not isinstance(block, dict) or not isinstance(block.get("content"), str):
                    continue
                clean_text = redact_text(
                    block["content"], complete=block.get("truncated") is not True, literals=literals
                )
                if "/logs/" in name and stream == "stdout" and isinstance(clean, dict):
                    clean_text["content"] = journal_text(clean)
                encoded = clean_text["content"].encode()
                text_outcome = (
                    "source-limit"
                    if outcome == "ok" and (block.get("truncated") or clean_text["truncated"])
                    else outcome
                )
                cap = min(allowance, max(0, TOTAL_LIMIT - report_reserve - total))
                if len(encoded) > cap:
                    encoded = bounded_text(encoded, cap)
                    text_outcome = "source-limit"
                if unsafe_text(encoded.decode("utf-8", errors="replace"), literals):
                    encoded = b""
                    text_outcome = "invalid-data"
                allowance -= len(encoded)
                filename = name + (
                    ".txt"
                    if name == "operator/status" and stream == "stdout"
                    else "." + stream + ".txt"
                )
                entries[filename] = encoded
                total += len(encoded)
                manifest.append(
                    {
                        **source_row,
                        "file": filename,
                        "record_id": hashlib.sha256(filename.encode()).hexdigest()[:24],
                        "outcome": text_outcome,
                        "bytes": len(encoded),
                        "sha256": hashlib.sha256(encoded).hexdigest(),
                        "source_file": entry,
                        "command_id": name,
                        "stream": stream,
                        "redaction_policy": REDACTION_POLICY,
                        "redactions": clean_text["redactions"]
                        + (
                            block.get("redactions", 0)
                            if type(block.get("redactions")) is int
                            else 0
                        ),
                        "discarded_text": clean_text["discarded"]
                        + (block.get("discarded", 0) if type(block.get("discarded")) is int else 0),
                        "text_cap": len(encoded) if text_outcome == "source-limit" else cap,
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
            from .checkpoints import project as project_checkpoint

            try:
                checkpoint = project_checkpoint(value, config) if isinstance(value, dict) else None
            except (ValueError, KeyError, TypeError, AttributeError, RuntimeError):
                checkpoint = None
                add(f"operator/checkpoints/{index}", {}, "invalid-data")
            if checkpoint is not None:
                add(f"operator/checkpoints/{index}", checkpoint)
            operations.update(operation_references(value))
            if index == 0:
                resources = lifecycle_resources(value, config)
                add("operator/scoped-resources", resources)
        except FileNotFoundError:
            add(f"operator/state/{index}", {}, "not-applicable")
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            add(f"operator/state/{index}", {}, "invalid-data")
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
    artifact_records, artifact_outcome = execute(
        {"action": "artifact"}, max(0.01, min(30, deadline - time.monotonic() - 50))
    )
    artifact = next(
        (row for row in artifact_records if row.get("probe") == "selected-artifact"), None
    )
    if artifact is not None:
        add(
            "operator/selected-artifact",
            artifact.get("data"),
            artifact.get("outcome", "invalid-data"),
            **record_metadata(artifact),
        )
    else:
        failed = next((row for row in artifact_records if "failure" in row), {})
        add(
            "operator/selected-artifact",
            {},
            artifact_outcome if artifact_outcome != "ok" else "unavailable",
            **record_metadata(failed),
        )
    notify({"stage": "discovery", "state": "started", "node": None})
    cloud_records, cloud_outcome = execute(
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
        remaining = deadline - time.monotonic() - 50
        if name not in targets or remaining <= 0:
            detail = clean_failure(
                {
                    "stage": "discovery",
                    "code": "target-unavailable" if remaining > 0 else "timeout",
                    "connection_attempted": False,
                }
            )
            notify({"stage": "discovery", "state": "failed", "node": index, "failure": detail})
            outcome = "unavailable" if remaining > 0 else "timeout"
            return (
                index,
                [{"probe": "worker", "data": {}, "outcome": outcome, "failure": detail}],
                outcome,
            )
        records, outcome = execute(
            {**base, "action": "remote", "targets": targets, "hostname": name},
            min(220, remaining),
            index,
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
                item.get("probe"): item
                for item in records
                if isinstance(item.get("probe"), str)
            }
            first_record = snapshots.get("ha-0/native", {})
            last_record = snapshots.get("ha-1/native", {})
            first, last = first_record.get("data"), last_record.get("data")
            from ..operational_diagnostics import peer_channel, peer_direction

            sample = last_record if isinstance(last, dict) else first_record
            captured_at = record_metadata(sample).get("observed_at")
            # Both timestamps come from the gateway. Log collection and a slower
            # peer must not age a valid observation while the archive is assembled.
            assessment = (
                peer_channel(sample.get("data"), now=captured_at)
                if captured_at is not None
                else "unknown"
            )

            add(
                f"nodes/{index}/network/peer-channel",
                {
                    "local_port": 9443,
                    "direction": peer_direction(assessment),
                    "assessment": assessment,
                    "source": "ha-heartbeat-snapshot",
                },
                **({"observed_at": captured_at} if captured_at is not None else {}),
            )
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
    # Reserve each source a share before admitting logs from any member.
    log_allowance = min(
        MAX_BYTES, max(0, TOTAL_LIMIT - report_reserve - total) // max(1, len(logs))
    )
    for index, record in logs:
        add_member_record(index, record)
    status_records, status_outcome = execute(
        {**base, "action": "status"},
        max(0.01, min(STATUS_BUDGET, deadline - time.monotonic() - 20)),
    )
    for record in status_records:
        if record.get("probe") in {"status", "worker", "startup"}:
            add(
                "operator/" + record["probe"],
                record.get("data", {}),
                record.get("outcome", "invalid-data"),
                **record_metadata(record),
            )
    if not any(row.get("probe") == "status" for row in status_records) or status_outcome != "ok":
        add(
            "operator/status-collection",
            {},
            status_outcome if status_outcome != "ok" else "unavailable",
        )

    def node_coverage() -> list[dict[str, int]]:
        result = []
        for node in range(group["instance_count"]):
            rows = [
                row
                for row in manifest
                if row["file"].startswith(f"nodes/{node}/") and row["file"] in entries
            ]
            result.append(
                {
                    "node": node,
                    **collection_counts(rows),
                    "successful_probes": sum(
                        row["file"].endswith(".json")
                        and row["outcome"] == "ok"
                        and not row["file"].endswith("/network/peer-channel.json")
                        and any(
                            part in row["file"]
                            for part in ("/host/", "/services/", "/ha-", "/logs/", "/network")
                        )
                        for row in rows
                    ),
                    "text_files": sum(
                        row["file"].endswith(".txt") and row["bytes"] > 0 for row in rows
                    ),
                    "incomplete_probes": sum(
                        row["file"].endswith(".json")
                        and row["outcome"] not in {"ok", "not-applicable"}
                        for row in rows
                    ),
                }
            )
        return result

    notify({"stage": "archive", "state": "started", "node": None})
    partial = any(item["outcome"] not in {"ok", "not-applicable"} for item in manifest)
    # Build only projected data in a private directory beside the destination.
    with tempfile.TemporaryDirectory(prefix=".vpngw-support-", dir=destination.parent) as temp:
        archive = Path(temp) / "bundle.zip"
        while True:
            synchronize_text(entries, manifest)
            coverage = node_coverage()
            guest_nodes = [row["node"] for row in coverage if row["successful_probes"]]
            partial = partial or len(guest_nodes) != group["instance_count"]
            reports = build_reports(entries, manifest, not partial)
            header = {
                "schema": SCHEMA,
                "created_at": end,
                "since": start,
                "complete": not partial,
                "guest_nodes": guest_nodes,
                "node_coverage": coverage,
                "expected_nodes": group["instance_count"],
                "redaction_policy": REDACTION_POLICY,
                "probes": manifest,
                "diagnostic_sufficiency": "unassessed",
                "reports": [
                    {"file": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                    for name, data in reports.items()
                ],
                "privacy": "Private operational evidence with targeted credential redaction; unknown ordinary text retained. Redaction cannot guarantee removal of every secret.",
            }
            summary = (
                "VPNGW private support bundle\n\n"
                + (
                    "PARTIAL: inspect manifest.json for unavailable or truncated probes.\n"
                    if partial
                    else "Collection completed.\n"
                )
                + "Contains operational identifiers. Attach only to a private support ticket.\n"
                + "Logs and command output are redacted; review before private sharing. Redaction is not an absolute secret-free guarantee.\n"
                + "Read summary.md, evidence-index.json and analysis-guide.md.\n"
                + "Collection is observational and does not establish HA readiness or authorize recovery.\n"
            )
            header_data = json.dumps(header, sort_keys=True, indent=2).encode()
            uncompressed = (
                len(header_data)
                + len(summary.encode())
                + sum(map(len, entries.values()))
                + sum(map(len, reports.values()))
            )
            with zipfile.ZipFile(
                archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as bundle:
                bundle.writestr("manifest.json", header_data)
                bundle.writestr("README.txt", summary)
                for name, data in reports.items():
                    bundle.writestr(name, data)
                for name, data in entries.items():
                    bundle.writestr(name, data)
            with zipfile.ZipFile(archive) as bundle:
                for name in bundle.namelist():
                    if unsafe_text(name, literals) or unsafe_export(
                        name, bundle.read(name), literals
                    ):
                        raise ValueError("Unsafe bundle export rejected.")
            if archive.stat().st_size <= ZIP_LIMIT and uncompressed <= TOTAL_LIMIT:
                break
            if time.monotonic() >= deadline:
                raise ValueError("Support bundle finalization exceeded its deadline.")
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
                    if "coverage" in item:
                        item["coverage"]["discarded"] += item["coverage"]["exported"]
                        item["coverage"]["exported"] = 0
                        item["coverage"]["truncation"] = "oldest"
            partial = True
        os.chmod(archive, 0o600)
        with archive.open("rb") as stream:
            os.fsync(stream.fileno())
        os.link(archive, destination)  # Atomic exclusive publication; never replace a destination.
    for row in coverage:
        notify({"stage": "collect", "state": "completed", **row})
    notify(
        {
            "stage": "archive",
            "state": "completed",
            "node": None,
            "guest_nodes": len(guest_nodes),
            "expected_nodes": group["instance_count"],
            **collection_counts(manifest),
        }
    )
    return destination, 1 if partial else 0
