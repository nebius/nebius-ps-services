"""Standalone read-only Linux probe. No package imports on the gateway."""

from __future__ import annotations

import json
import os
import re
import selectors
import signal
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

from .projection import (
    DIAGNOSTIC_STRINGS,
    ENUMS,
    EXCEPTIONS,
    FRAMES,
    MAX_BYTES,
    PACKAGE_WORKER_UNIT,
    project,
)
from .redaction import TextRedactor, redact_text

STATE_FILES = (
    "status.json",
    "controller-checkpoint.json",
    "guard.json",
    "materialization.json",
    "apply.lock",
    "apply-owner-adoption.json",
    "effect-receipt.json",
    "transfer-lineage.json",
    "transfer-progress.json",
    "promotion-receipt.json",
    "rearm-status.json",
    "standby-ready.json",
    "standby-replacement.inhibition.json",
    "manual-failback.json",
    "manual-failover.json",
    "emergency-active-only.json",
    "mtls/active.json",
    "mtls/inhibition.json",
    "outbound-heartbeat-sequence.json",
    "generation-store/pointers.json",
    "generation-store/pointers.backup.json",
    "generation-store/route-reconciliation-receipt.json",
)
UNITS = (
    "nebius-vpngw-agent",
    "nebius-vpngw-vm-ha",
    "nebius-vpngw-vm-ha-guard",
    "nebius-vpngw-vm-ha-rearm",
    "nebius-vpngw-ingress",
    "nebius-vpngw-fix-routes",
    "nebius-vpngw-ordinary-fix-routes",
    "nebius-vpngw-health-monitor",
    "nebius-vpngw-ordinary-agent",
    "nebius-vpngw-ordinary-health-monitor",
    "nebius-vpngw-esp4-preflight",
    "strongswan",
    "strongswan-starter",
    "strongswan-swanctl",
    "charon-systemd",
    "frr",
    "systemd-networkd",
    "cloud-init",
    "cloud-init-local",
    "cloud-config",
    "cloud-final",
)
TIMERS = ("nebius-vpngw-fix-routes.timer",)
PROPERTIES = (
    "ActiveState",
    "SubState",
    "LoadState",
    "Result",
    "MainPID",
    "NRestarts",
    "ExecMainStatus",
    "ExecMainCode",
    "UnitFileState",
    "ActiveEnterTimestampMonotonic",
    "InactiveEnterTimestampMonotonic",
    "InvocationID",
    "ExecMainStartTimestampMonotonic",
    "ExecMainExitTimestampMonotonic",
    "OOMKilled",
)


def valid_remote_name(name: Any) -> bool:
    """Names are product-owned identifiers, never source basenames."""
    if not isinstance(name, str):
        return False
    for prefix in ("ha-0/", "ha-1/"):
        if name.startswith(prefix):
            tail = name[len(prefix) :]
            return tail in (*STATE_FILES, "native", "mtls/transactions-limit") or bool(
                re.fullmatch(
                    r"mtls/transactions/[0-7]|generation-store/peers/[0-3]/(?:replay|accepted-heartbeat|accepted-heartbeat-reset)\.json",
                    tail,
                )
            )
    fixed = {
        "worker",
        "access",
        "host/time",
        "host/forwarding",
        "host/packages",
        "host/agent-package",
        "host/resources",
        "host/resource-samples",
        "host/cloud-init",
        "host/storage",
        "host/service-cgroups",
        "network/listeners",
        "logs/kernel",
    }
    fixed.update("services/" + unit for unit in UNITS)
    fixed.update("service-status/" + unit for unit in UNITS)
    fixed.update(
        {
            "startup",
            "host/uptime",
            "host/free",
            "host/vmstat",
            "host/dmesg",
            "host/cloud-init-status",
        }
    )
    fixed.update(
        "file-logs/" + name
        for name in ("route-fix", "firewall-setup", "cloud-init", "cloud-init-output")
    )
    fixed.update("services/" + timer for timer in TIMERS)
    fixed.update("logs/" + unit for unit in UNITS)
    fixed.update(
        "network/" + item
        for item in (
            "addresses",
            "routes",
            "rules",
            "xfrm-policy",
            "firewall",
            "bgp-summary",
            "bgp-neighbors",
            "bgp-routes",
            "ipsec-sas",
        )
    )
    fixed.update(
        f"network-{index}/{item}" for index in (0, 1) for item in ("links", "xfrm-counters")
    )
    return (
        name in fixed
        or bool(
            re.fullmatch(
                r"(?:network/bgp-advertised|state/worker|state/worker-limit|services/package-worker|logs/package-worker)/[0-9]{1,2}",
                name,
            )
        )
        or name in {"state/ordinary", "state/repair"}
    )


def error_context(message: str) -> dict[str, Any]:
    """Recognize facts without exporting exception arguments, paths or source code."""
    result: dict[str, Any] = {}
    for name in sorted(EXCEPTIONS):
        if re.search(r"\b" + name + r"\s*:", message):
            result["exception_class"] = name
            break
    match = re.search(r"\[Errno ([0-9]{1,4})\]", message)
    if match:
        result["errno"] = int(match[1])
    frames = []
    for frame in sorted(FRAMES):
        match = re.search(r"/nebius_vpngw/" + re.escape(frame) + r'", line ([0-9]{1,7})', message)
        if match:
            frames.append({"frame": frame, "line": int(match[1])})
    if frames:
        result["frames"] = frames[:8]
    return result


def journal_events(text: str) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {
        "scanned": 0,
        "invalid": 0,
        "unrecognized": 0,
        "recognized": 0,
        "exported": 0,
        "discarded": 0,
        "counts": "exact",
        "retention": "unknown",
        "truncation": "none",
        "source_limited": False,
    }
    templates = (
        (r"Failed to start|Failed with result|Main process exited", "service-failed"),
        (r"Started |Starting |Stopped |Stopping ", "service-transition"),
        (
            r"AUTHENTICATION_FAILED|authentication .*failed|no shared key found",
            "ipsec-authentication-failed",
        ),
        (r"NO_PROPOSAL_CHOSEN|no proposal chosen", "ipsec-proposal-mismatch"),
        (r"TS_UNACCEPTABLE|traffic selectors .*unacceptable", "ipsec-selector-mismatch"),
        (r"IKE_SA .*established|CHILD_SA .*established", "ipsec-established"),
        (r"retransmit .*request|giving up after .*retransmit", "ipsec-retransmission"),
        (r"BGP.*(Down|down)|neighbor .*Down|ADJCHANGE.*Down", "bgp-peer-down"),
        (r"BGP.*Established|neighbor .*Up|ADJCHANGE.*Up", "bgp-peer-up"),
        (r"Out of memory|oom-kill|Killed process", "kernel-oom"),
        (r"NETDEV WATCHDOG|link is down|Lost carrier", "network-link-down"),
        (r"Gained carrier|link becomes ready", "network-link-up"),
        (r"Traceback \(most recent call last\)", "python-exception"),
        (
            r"certificate.*(?:expired|verify failed|not yet valid)|CERTIFICATE_VERIFY_FAILED",
            "certificate-error",
        ),
        (r"dpkg: error|pip.*error|No matching distribution", "package-error"),
        (
            r"\[Agent\].*(?:[Ff]ailed|[Ee]rror)|cloud-init.*(?:failed|ERROR)",
            "agent-or-bootstrap-failed",
        ),
    )
    codes = {code for code in ENUMS if "-" in code}
    timestamps = []
    lines = text.splitlines()
    # journalctl is newest-first. Redact multiline credentials in chronological
    # order, carrying state only within the same boot/unit/process stream.
    messages = {}
    redactors: dict[tuple[str, ...], TextRedactor] = {}
    # Preserve journal traversal order: wall clocks can move backwards and
    # multiple entries can have the same timestamp. Reversing --reverse output
    # keeps credential openers before continuations even in those cases.
    for index in reversed(range(len(lines))):
        try:
            event = json.loads(lines[index])
            if not isinstance(event, dict) or not isinstance(event.get("MESSAGE"), str):
                continue
        except (ValueError, TypeError):
            continue
        identity = tuple(str(event.get(key, "")) for key in ("_BOOT_ID", "_SYSTEMD_UNIT", "_PID"))
        redactor = redactors.setdefault(identity, TextRedactor())
        messages[index] = redactor.feed(event["MESSAGE"] + "\n", final=True).rstrip("\n")
    for line_index, line in enumerate(lines):
        coverage["scanned"] += 1
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("invalid-data")
            message = event.get("MESSAGE", "")
            if not isinstance(message, str):
                raise ValueError("invalid-data")
            found = codes.intersection(re.findall(r"[a-z][a-z0-9-]{1,95}", message))
            found.update(code for pattern, code in templates if re.search(pattern, message))
            detail = error_context(message)
            if detail.get("exception_class") or detail.get("frames"):
                found.add("python-error")
            if "errno" in detail:
                found.add("errno-error")
            timestamp = str(event.get("__REALTIME_TIMESTAMP", ""))
            if timestamp.isdecimal() and len(timestamp) <= 20:
                timestamps.append(int(timestamp))
            if message:
                item = {
                    "timestamp": timestamp,
                    "boot_id": event.get("_BOOT_ID"),
                    "reasons": sorted(found),
                    "message": messages.get(line_index, ""),
                    **detail,
                }
                for source, target in (
                    ("__MONOTONIC_TIMESTAMP", "monotonic_usec"),
                    ("PRIORITY", "priority"),
                    ("_PID", "pid"),
                    ("ERRNO", "errno"),
                ):
                    val = str(event.get(source, ""))
                    if val.isdecimal() and len(val) <= 20:
                        item[target] = int(val)
                invocation = event.get("_SYSTEMD_INVOCATION_ID", event.get("INVOCATION_ID"))
                if isinstance(invocation, str) and re.fullmatch(r"[a-f0-9]{32}", invocation):
                    item["invocation_id"] = invocation
                unit = str(event.get("_SYSTEMD_UNIT", "")).removesuffix(".service")
                if unit in DIAGNOSTIC_STRINGS["unit"]:
                    item["unit"] = unit
                # Only explicitly named operation fields, never arbitrary message fragments.
                for key in ("operation_id", "apply_operation_id", "generation_id"):
                    match = re.search(r"\b" + key + r'["\s:=]+([a-f0-9]{12,64})\b', message)
                    if match:
                        item[key] = match[1]
                if "kernel-oom" in found:
                    victim = re.search(r"Killed process ([0-9]{1,10})", message)
                    if victim:
                        item["victim_pid"] = int(victim[1])
                    rss = re.search(r"anon-rss:([0-9]{1,15})kB", message)
                    if rss:
                        item["rss_bytes"] = int(rss[1]) * 1024
                events.append(item)
                if found:
                    coverage["recognized"] += 1
                else:
                    coverage["unrecognized"] += 1
            else:
                coverage["unrecognized"] += 1
        except (ValueError, TypeError, AttributeError):
            coverage["invalid"] += 1
    coverage["exported"] = len(events)
    if timestamps:
        coverage.update(observed_start_usec=min(timestamps), observed_end_usec=max(timestamps))
    return {"coverage": coverage, "events": events}


def cloud_init_status(text: str) -> dict[str, Any]:
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("status") not in {
        "running",
        "done",
        "disabled",
        "error",
        "not started",
        "not run",
    }:
        raise ValueError("invalid-data")

    def errors(value: dict[str, Any]) -> dict[str, Any]:
        fatal = value.get("errors", [])
        recoverable = value.get("recoverable_errors", {})
        if not isinstance(fatal, list) or not isinstance(recoverable, dict):
            raise ValueError("invalid-data")
        recovered = [
            item for items in recoverable.values() if isinstance(items, list) for item in items
        ]
        details = [
            error_context(item) for item in (fatal + recovered)[:64] if isinstance(item, str)
        ]
        return {
            "error_count": len(fatal),
            "recoverable_error_count": len(recovered),
            "unknown_errors": sum(not detail for detail in details),
            "discarded": max(0, len(fatal) + len(recovered) - 64),
            "errors": details,
        }

    result: dict[str, Any] = {
        "status": raw["status"].replace(" ", "-"),
        "degraded": str(raw.get("extended_status", "")).startswith("degraded"),
        "stage": raw.get("stage") if raw.get("stage") in DIAGNOSTIC_STRINGS["stage"] else "unknown",
        "stages": [],
        **errors(raw),
    }
    for stage in ("init-local", "init", "modules-config", "modules-final"):
        value = raw.get(stage)
        if not isinstance(value, dict):
            continue
        item = {
            "stage": stage,
            "start": value.get("start"),
            "finished": value.get("finished"),
            **errors(value),
        }
        result["stages"].append(item)
    result["source_limited"] = bool(
        result["discarded"] or any(item["discarded"] for item in result["stages"])
    )
    return result


def link_counters(text: str) -> list[dict[str, Any]]:
    """Normalize native interface counters into the fixed exported counter fields."""
    links = json.loads(text)
    if not isinstance(links, list) or any(not isinstance(link, dict) for link in links):
        raise ValueError("invalid-data")
    for link in links:
        counters = link.get("stats64") or link.get("stats") or {}
        if not isinstance(counters, dict):
            raise ValueError("invalid-data")
        for direction in ("rx", "tx"):
            values = counters.get(direction, {})
            if not isinstance(values, dict):
                raise ValueError("invalid-data")
            for metric in ("bytes", "packets", "errors", "dropped"):
                value = values.get(metric)
                if type(value) is int and 0 <= value <= 10**20:
                    link[f"{direction}_{metric}"] = value
    return links


def bounded_command(
    argv: list[str],
    *,
    timeout: float = 30,
    limit: int = MAX_BYTES,
    stdin: bytes | None = None,
    stderr_out: list[bytes] | None = None,
    on_stdout: Any = None,
) -> tuple[bytes, str, int | None]:
    """Drain bounded pipes and terminate only this owned process group."""
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    output = bytearray()
    errors = bytearray()
    deadline = time.monotonic() + timeout
    outcome = "ok"
    try:
        with selectors.DefaultSelector() as selector:
            assert process.stdout is not None
            selector.register(process.stdout, selectors.EVENT_READ)
            assert process.stderr is not None
            selector.register(process.stderr, selectors.EVENT_READ)
            pending = memoryview(stdin or b"")
            if process.stdin is not None:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    outcome = "timeout"
                    break
                for key, _events in selector.select(min(remaining, 0.1)):
                    if key.fileobj is process.stdin:
                        count = os.write(key.fd, pending[:65536]) if pending else 0
                        pending = pending[count:]
                        if not pending:
                            selector.unregister(key.fileobj)
                            process.stdin.close()
                    else:
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        target = errors if key.fileobj is process.stderr else output
                        admitted = chunk[: max(0, limit - len(output) - len(errors))]
                        target.extend(admitted)
                        if key.fileobj is process.stdout and on_stdout is not None:
                            on_stdout(admitted)
                        if len(output) + len(errors) >= limit:
                            outcome = "source-limit"
                            break
                if outcome != "ok":
                    break
            if outcome == "ok":
                try:
                    process.wait(timeout=max(0.01, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    outcome = "timeout"
    finally:
        # Also reap descendants that inherited pipes after the leader exited.
        if outcome != "ok" or process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        if process.stdout is not None:
            process.stdout.close()
        if process.stdin is not None:
            process.stdin.close()
        if process.stderr is not None:
            process.stderr.close()
    if stderr_out is not None:
        stderr_out.append(bytes(errors))
    return (
        bytes(output),
        outcome if outcome != "ok" else ("ok" if process.returncode == 0 else "unavailable"),
        process.returncode,
    )


def read_regular(path: Path, limit: int = MAX_BYTES) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("invalid-data")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("source-limit")
        after = os.fstat(fd)
        current = path.lstat()
        if (info.st_ino, info.st_dev, info.st_size, info.st_mtime_ns) != (
            after.st_ino,
            after.st_dev,
            after.st_size,
            after.st_mtime_ns,
        ) or (info.st_ino, info.st_dev) != (current.st_ino, current.st_dev):
            raise ValueError("changed")
        return data
    finally:
        os.close(fd)


def read_log_tail(path: Path) -> tuple[str, bool, bool]:
    """Read only a fixed regular log's bounded tail; never follow symlinks."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("invalid-data")
        offset = max(0, before.st_size - MAX_BYTES)
        os.lseek(fd, offset, os.SEEK_SET)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BYTES)
        if offset:
            raw = raw.partition(b"\n")[2]
        after = os.fstat(fd)
        current = path.lstat()
        changed = (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)
        return raw.decode("utf-8", errors="replace"), bool(offset), changed
    finally:
        os.close(fd)


def select_transactions(base: Path) -> tuple[list[Path], bool]:
    """Prefer the inhibited operation and apply fence, then recent bounded history."""
    from itertools import islice

    directory = base / "mtls/transactions"
    priorities: list[Path] = []
    for filename in ("mtls/inhibition.json", "apply.lock", "status.json"):
        try:
            value = json.loads(read_regular(base / filename))
            operation = value.get("operation_id") if isinstance(value, dict) else None
            if isinstance(operation, str) and re.fullmatch(r"[a-f0-9]{64}", operation):
                path = directory / (operation + ".json")
                if path not in priorities:
                    priorities.append(path)
        except (OSError, ValueError):
            pass
    recent: list[tuple[int, Path]] = []
    limited = False
    try:
        for index, path in enumerate(islice(directory.iterdir(), 1025)):
            if index == 1024:
                limited = True
                break
            if not re.fullmatch(r"[a-f0-9]{64}\.json", path.name):
                continue
            try:
                info = path.lstat()
                if stat.S_ISREG(info.st_mode):
                    recent.append((info.st_mtime_ns, path))
            except OSError:
                limited = True
    except OSError:
        return priorities, True
    selected = priorities + [
        path for _, path in sorted(recent, reverse=True) if path not in priorities
    ]
    return selected[:8], limited or len(selected) > 8


def select_worker_transports(root: Path, deadline: float) -> tuple[list[Path], bool]:
    """Bound inspection and prefer unfinished workers over terminal history."""
    from itertools import islice

    candidates = []
    limited = False
    deadline = min(deadline, time.monotonic() + 30)
    for index, path in enumerate(islice(root.glob("*/transport.json"), 65)):
        if index == 64 or time.monotonic() >= deadline:
            limited = True
            break
        if not re.fullmatch(r"[a-f0-9]{64}", path.parent.name) or path.parent.is_symlink():
            continue
        try:
            value = json.loads(read_regular(path))
            if not isinstance(value, dict):
                raise ValueError("invalid-data")
            candidates.append((value.get("terminal") is not None, -path.lstat().st_mtime_ns, path))
        except (OSError, ValueError):
            limited = True
    return [item[2] for item in sorted(candidates)[:16]], limited or len(candidates) > 16


def pressure_values(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0] not in {"some", "full"}:
            continue
        fields = dict(part.split("=", 1) for part in parts[1:] if "=" in part)
        result[parts[0]] = {
            key: float(fields[key]) for key in ("avg10", "avg60", "avg300") if key in fields
        }
        if "total" in fields:
            result[parts[0]]["total_usec"] = int(fields["total"])
    if not result:
        raise ValueError("invalid-data")
    return result


def resource_snapshot() -> dict[str, Any]:
    def read(name: str) -> str:
        return read_regular(Path(name)).decode()

    stat_fields = {
        parts[0]: parts[1:] for line in read("/proc/stat").splitlines() if (parts := line.split())
    }
    cpu = [int(value) for value in stat_fields["cpu"][:8]]
    if len(cpu) != 8:
        raise ValueError("invalid-data")
    memory = dict(line.split(":", 1) for line in read("/proc/meminfo").splitlines())
    vm = dict(line.split() for line in read("/proc/vmstat").splitlines())
    load = [float(value) for value in read("/proc/loadavg").split()[:3]]
    result: dict[str, Any] = {
        "boot_id": read("/proc/sys/kernel/random/boot_id").strip(),
        "clock_realtime_usec": time.time_ns() // 1000,
        "clock_monotonic_usec": time.monotonic_ns() // 1000,
        "_cpu": cpu,
        "runnable": int(stat_fields["procs_running"][0]),
        "blocked": int(stat_fields["procs_blocked"][0]),
        "load_1": load[0],
        "load_5": load[1],
        "load_15": load[2],
        "pressure": [],
    }
    for key, source in (
        ("memory_total", "MemTotal"),
        ("memory_available", "MemAvailable"),
        ("swap_total", "SwapTotal"),
        ("swap_free", "SwapFree"),
    ):
        result[key] = int(memory[source].split()[0]) * 1024
    for key, source in (
        ("swap_in", "pswpin"),
        ("swap_out", "pswpout"),
        ("page_in", "pgpgin"),
        ("page_out", "pgpgout"),
    ):
        result[key] = int(vm[source])
    for resource in ("cpu", "memory", "io"):
        try:
            value = {"available": True, **pressure_values(read("/proc/pressure/" + resource))}
        except (OSError, ValueError):
            value = {"available": False}
        result["pressure"].append({"resource": resource, **value})
    return result


def resource_samples(deadline: float) -> tuple[dict[str, Any], str]:
    result: dict[str, Any] = {"expected_samples": 5, "sample_count": 0, "samples": []}
    outcome = "ok"
    try:
        previous = resource_snapshot()
        for _ in range(5):
            if deadline - time.monotonic() < 1:
                return result, "timeout"
            time.sleep(1)
            current = resource_snapshot()
            elapsed = (
                current["clock_monotonic_usec"] - previous["clock_monotonic_usec"]
            ) / 1_000_000
            delta = [new - old for new, old in zip(current["_cpu"], previous["_cpu"], strict=True)]
            reset = current["boot_id"] != previous["boot_id"] or min(delta) < 0 or elapsed <= 0
            sample = {key: value for key, value in current.items() if key != "_cpu"}
            sample.update(interval_seconds=max(0, elapsed), counter_reset=reset)
            if not reset and sum(delta):
                for key, ticks in (
                    ("cpu_user", delta[0] + delta[1]),
                    ("cpu_system", delta[2] + delta[5] + delta[6]),
                    ("cpu_idle", delta[3]),
                    ("cpu_iowait", delta[4]),
                    ("cpu_steal", delta[7]),
                ):
                    sample[key] = 100 * ticks / sum(delta)
            for key in ("swap_in", "swap_out", "page_in", "page_out"):
                difference = current[key] - previous[key]
                if reset or difference < 0:
                    sample.pop(key, None)
                    sample["counter_reset"] = True
                else:
                    sample[key] = difference
            if (
                sample["counter_reset"]
                or not sum(delta)
                or not all(item["available"] for item in sample["pressure"])
            ):
                outcome = "partial"
            result["samples"].append(sample)
            result["sample_count"] += 1
            previous = current
    except (OSError, ValueError, KeyError, IndexError):
        outcome = "partial" if result["samples"] else "unavailable"
    return result, outcome


def storage_facts() -> list[dict[str, Any]]:
    result = []
    for location, path in (
        ("root", "/"),
        ("logs", "/var/log"),
        ("state", "/var/lib/nebius-vpngw"),
        ("temporary", "/tmp"),
    ):
        try:
            statfs = os.statvfs(path)
            result.append(
                {
                    "location": location,
                    "available": True,
                    "disk_total": statfs.f_blocks * statfs.f_frsize,
                    "disk_free": statfs.f_bavail * statfs.f_frsize,
                    "inodes_total": statfs.f_files,
                    "inodes_free": statfs.f_favail,
                }
            )
        except OSError:
            result.append({"location": location, "available": False})
    return result


def service_cgroups(units: list[str]) -> list[dict[str, Any]]:
    result = []
    for unit in units:
        base = Path("/sys/fs/cgroup/system.slice") / (unit + ".service")
        item: dict[str, Any] = {
            "unit": unit,
            "available": False,
            "outcome": "unavailable",
            "pid_membership_available": False,
        }
        try:
            events = dict(
                line.split() for line in read_regular(base / "memory.events").decode().splitlines()
            )
            item["memory_events"] = {
                key: int(events[key])
                for key in ("low", "high", "max", "oom", "oom_kill")
                if key in events
            }
            item["memory_current"] = int(read_regular(base / "memory.current"))
            item["available"] = True
            item["outcome"] = "ok"
        except FileNotFoundError:
            if Path("/sys/fs/cgroup/cgroup.controllers").is_file() and not base.exists():
                item["outcome"] = "not-applicable"
        except (OSError, ValueError):
            pass
        try:
            raw_pids = read_regular(base / "cgroup.procs").decode().splitlines()
            pids = [int(value) for value in raw_pids[:64]]
            if any(not 0 < pid < 2**31 for pid in pids):
                raise ValueError("invalid-data")
            item["pids"] = pids
            item["pid_membership_available"] = True
            item["source_limited"] = len(raw_pids) > 64
        except (OSError, ValueError):
            pass
        result.append(item)
    return result


def managed_listeners(text: str, pids: dict[int, str]) -> list[dict[str, Any]]:
    import ipaddress

    result = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 6 or parts[0] not in {"tcp", "udp"}:
            continue
        owners = [int(value) for value in re.findall(r"pid=([0-9]{1,10})", line)]
        owners = [pid for pid in owners if pid in pids]
        if not owners:
            continue
        address, _, port = parts[4].rpartition(":")
        try:
            address = (
                "wildcard"
                if address == "*"
                else str(ipaddress.ip_address(address.strip("[]").split("%", 1)[0]))
            )
            port_number = int(port)
            if not 0 <= port_number <= 65535:
                continue
        except ValueError:
            continue
        for pid in owners:
            result.append(
                {
                    "unit": pids[pid],
                    "pid": pid,
                    "protocol": parts[0],
                    "address": address,
                    "local_port": port_number,
                }
            )
    return result


def collect_probe(request: dict[str, Any], emit: Any = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    deadline = time.monotonic() + min(float(request.get("budget", 180)), 180)

    def add(
        name: str,
        value: Any,
        *,
        outcome: str = "ok",
        exit_code: int | None = None,
        started: float | None = None,
        text: dict[str, Any] | None = None,
    ) -> None:
        omissions = [0]
        limits = [0]
        clean = project(value, omitted=omissions, limited=limits)
        if outcome == "ok" and limits[0]:
            outcome = "source-limit"
        elif outcome == "ok" and value and not clean and omissions[0]:
            outcome = "partial"
        observed = time.time()
        duration = 0 if started is None else max(0, time.monotonic() - started)
        record = {
            "probe": name,
            "outcome": outcome,
            "data": clean,
            "omitted_fields": omissions[0],
            "truncated_fields": limits[0],
            "exit_code": exit_code,
            "observed_at": observed,
            "duration_seconds": duration,
            "collection_started_at": observed - duration,
            "collection_finished_at": observed,
        }
        if text is not None:
            record["text"] = text
        if emit is None:
            records.append(record)
        else:
            emit(record)

    def command(name: str, argv: list[str], parser: Any = json.loads) -> Any:
        started = time.monotonic()
        if deadline <= started:
            add(name, {}, outcome="timeout")
            return
        try:
            errors: list[bytes] = []
            raw, outcome, code = bounded_command(
                argv, timeout=min(30, deadline - started), stderr_out=errors
            )
            complete = outcome not in {"timeout", "source-limit"}
            text = {
                "stdout": redact_text(raw.decode("utf-8", errors="replace"), complete=complete),
                "stderr": redact_text(
                    b"".join(errors).decode("utf-8", errors="replace"), complete=complete
                ),
            }
            if name.startswith("logs/"):
                lines = raw.splitlines()
                if len(lines) > 5000:
                    raw = b"\n".join(lines[:5000])
                    outcome = "source-limit"
            try:
                value = (
                    parser(raw.decode("utf-8", errors="replace"))
                    if raw or name.startswith("logs/")
                    else {}
                )
            except (ValueError, TypeError, KeyError):
                value = {}
                if outcome == "ok":
                    outcome = "invalid-data"
            if name.startswith("logs/") and isinstance(value, dict) and "coverage" in value:
                # Journal JSON is parsed before its message is exported. Do not
                # duplicate opaque JSON-escaped message text into the text view.
                text["stdout"] = redact_text(
                    "\n".join(
                        str(event.get("timestamp", "")) + " " + str(event.get("message", ""))
                        for event in value.get("events", [])
                    )
                )
                value["coverage"].update(
                    requested_since=request["since"], requested_until=request["until"]
                )
                if outcome != "ok":
                    value["coverage"].update(
                        counts="lower-bound", source_limited=True, truncation="oldest"
                    )
                elif value["coverage"]["invalid"]:
                    outcome = "partial"
            if (name.startswith("service-status/") and code in {0, 3, 4}) or (
                name == "host/cloud-init-status" and code in {0, 1, 2}
            ):
                if outcome not in {"timeout", "source-limit"}:
                    outcome = "ok"
            # A reported bootstrap failure is a successful status observation.
            if (
                name == "host/cloud-init"
                and code in {0, 1, 2}
                and isinstance(value, dict)
                and "status" in value
                and outcome not in {"timeout", "source-limit"}
            ):
                outcome = "source-limit" if value["source_limited"] else "ok"
            if name == "network/listeners" and outcome == "ok" and isinstance(value, dict):
                if value.get("source_limited"):
                    outcome = "source-limit"
                elif not value.get("available"):
                    outcome = "partial"
            add(name, value, outcome=outcome, exit_code=code, started=started, text=text)
            return value
        except (OSError, ValueError):
            add(name, {}, outcome="unavailable", started=started)

    def fields(text: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for line in text.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                if key == "InvocationID":
                    result["invocation_id"] = value
                else:
                    result[key] = int(value) if value.isdecimal() else value
        return result

    def snapshot(number: int) -> None:
        base = Path("/var/lib/nebius-vpngw/vm-ha")
        files = [(name, name) for name in STATE_FILES]
        from itertools import islice

        for index, peer_dir in enumerate(islice((base / "generation-store/peers").glob("*"), 4)):
            if peer_dir.is_symlink() or not peer_dir.is_dir():
                continue
            for filename in (
                "replay.json",
                "accepted-heartbeat.json",
                "accepted-heartbeat-reset.json",
            ):
                files.append(
                    (
                        f"generation-store/peers/{index}/{filename}",
                        str(peer_dir.relative_to(base) / filename),
                    )
                )
        if (base / "mtls/transactions").is_dir():
            transactions, limited = select_transactions(base)
            files.extend(
                (f"mtls/transactions/{index}", "mtls/transactions/" + path.name)
                for index, path in enumerate(transactions)
            )
            if limited:
                add(f"ha-{number}/mtls/transactions-limit", {}, outcome="source-limit")
        for label, filename in files:
            try:
                value = json.loads(read_regular(base / filename))
                add(f"ha-{number}/{label}", value)
            except FileNotFoundError:
                add(f"ha-{number}/{label}", {}, outcome="not-applicable")
            except (OSError, ValueError):
                add(f"ha-{number}/{label}", {}, outcome="invalid-data")
        command(
            f"ha-{number}/native",
            ["/usr/bin/python3", "-m", "nebius_vpngw.agent.main", "--vm-ha-status"],
        )
        try:
            counters = {}
            for line in Path("/proc/net/xfrm_stat").read_text().splitlines():
                key, value = line.split()
                if key.startswith("Xfrm") and value.isdecimal():
                    # Counter names are fixed by the kernel; store as operational names.
                    counters[key] = int(value)
            add(
                f"network-{number}/xfrm-counters",
                [{"name": k, "packets": v} for k, v in counters.items()],
            )
        except (OSError, ValueError):
            add(f"network-{number}/xfrm-counters", {}, outcome="unavailable")
        command(f"network-{number}/links", ["ip", "-j", "-s", "link", "show"], link_counters)

    snapshot(0)
    started = time.monotonic()
    worker_units: list[tuple[int, str]] = []
    for label, path in (
        ("state/ordinary", Path("/var/lib/nebius-vpngw/ordinary/operation.json")),
        ("state/repair", Path("/var/lib/nebius-vpngw-repair/operation.json")),
    ):
        try:
            add(label, json.loads(read_regular(path)))
        except FileNotFoundError:
            add(label, {}, outcome="not-applicable")
        except (OSError, ValueError):
            add(label, {}, outcome="invalid-data")
    worker_index = 0
    for root_index, root in enumerate(
        (
            Path("/var/lib/nebius-vpngw/runtime-migration/workers"),
            Path("/var/lib/nebius-vpngw-repair/workers"),
        )
    ):
        paths, limited = select_worker_transports(root, deadline)
        if limited:
            add(f"state/worker-limit/{root_index}", {}, outcome="source-limit")
        for path in paths:
            index = worker_index
            worker_index += 1
            try:
                value = json.loads(read_regular(path))
                add(f"state/worker/{index}", value)
                unit = value.get("unit", "")
                if PACKAGE_WORKER_UNIT.fullmatch(unit):
                    worker_units.append((index, unit))
                    command(
                        f"services/package-worker/{index}",
                        ["systemctl", "show", unit, "--property=" + ",".join(PROPERTIES)],
                        fields,
                    )
            except (OSError, ValueError, AttributeError, TypeError):
                add(f"state/worker/{index}", {}, outcome="invalid-data")
    managed_pids = {}
    for unit in UNITS:
        service = command(
            "services/" + unit,
            ["systemctl", "show", unit + ".service", "--property=" + ",".join(PROPERTIES)],
            fields,
        )
        if (
            isinstance(service, dict)
            and type(service.get("MainPID")) is int
            and service["MainPID"] > 0
        ):
            managed_pids[service["MainPID"]] = unit
    for timer in TIMERS:
        command(
            "services/" + timer,
            ["systemctl", "show", timer, "--property=" + ",".join(PROPERTIES)],
            fields,
        )
    neighbors = {}
    for name, args in (
        ("addresses", ["ip", "-j", "address", "show"]),
        ("routes", ["ip", "-j", "route", "show", "table", "all"]),
        ("rules", ["ip", "-j", "rule", "show"]),
        ("firewall", ["nft", "-j", "list", "ruleset"]),
        ("bgp-summary", ["vtysh", "-c", "show bgp summary json"]),
        ("bgp-neighbors", ["vtysh", "-c", "show bgp neighbors json"]),
        ("bgp-routes", ["vtysh", "-c", "show bgp ipv4 unicast json"]),
    ):
        value = command("network/" + name, args)
        if name == "bgp-neighbors" and isinstance(value, dict):
            neighbors = value
    for index, address in enumerate(list(neighbors)[:16]):
        try:
            import ipaddress

            peer = str(ipaddress.ip_address(address))
        except ValueError:
            continue
        command(
            f"network/bgp-advertised/{index}",
            ["vtysh", "-c", f"show bgp ipv4 unicast neighbors {peer} advertised-routes json"],
        )
    # iproute2's XFRM policy observer emits text even with the generic -j flag.
    command("network/xfrm-policy", ["ip", "xfrm", "policy"], lambda _text: {})
    cgroups = service_cgroups(list(UNITS))
    for item in cgroups:
        managed_pids.update({pid: item["unit"] for pid in item.get("pids", [])})
    membership_complete = all(
        item["pid_membership_available"] or item["outcome"] == "not-applicable" for item in cgroups
    )
    membership_limited = any(item.get("source_limited") for item in cgroups)
    command(
        "network/listeners",
        ["ss", "-H", "-lntup"],
        lambda text: {
            "listeners": managed_listeners(text, managed_pids),
            "available": membership_complete,
            "source_limited": membership_limited,
        },
    )
    add(
        "host/service-cgroups",
        cgroups,
        outcome="source-limit"
        if membership_limited
        else "ok"
        if all(item["available"] or item["outcome"] == "not-applicable" for item in cgroups)
        else "partial",
    )
    command("host/cloud-init", ["cloud-init", "status", "--format", "json"], cloud_init_status)
    command(
        "host/time",
        ["timedatectl", "show", "--property=NTPSynchronized,TimeUSec,RTCTimeUSec"],
        lambda text: {
            "NTPSynchronized": fields(text).get("NTPSynchronized"),
            "clock_realtime_usec": time.time_ns() // 1000,
            "clock_monotonic_usec": time.monotonic_ns() // 1000,
        },
    )
    command(
        "host/forwarding",
        ["sysctl", "-n", "net.ipv4.ip_forward"],
        lambda text: {"enabled": text.strip() == "1"},
    )
    command(
        "host/packages",
        [
            "dpkg-query",
            "-W",
            "-f=${Package}\t${Version}\n",
            "strongswan*",
            "frr*",
            "python3*",
            "linux-image*",
            "cloud-init",
            "systemd",
        ],
        lambda text: [
            {"package": line.split("\t")[0], "version": line.split("\t")[1]}
            for line in text.splitlines()
            if "\t" in line
        ],
    )
    command(
        "host/agent-package",
        [
            "/usr/bin/python3",
            "-c",
            "import importlib.metadata,json; print(json.dumps({'version':importlib.metadata.version('nebius-vpngw')}))",
        ],
    )
    vici_code = """import json, socket, vici
def decode(x):
    if isinstance(x, bytes): return x.decode('utf-8', errors='replace')
    if isinstance(x, dict):
        return {decode(k): ([{**decode(v), "name":decode(n)} for n,v in val.items()] if decode(k)=='child-sas' else decode(val)) for k,val in x.items()}
    if isinstance(x, list): return [decode(v) for v in x]
    return x
s=socket.socket(socket.AF_UNIX); s.settimeout(10); s.connect('/var/run/charon.vici')
try:
    print(json.dumps([{**decode(v), "name":decode(k)} for sa in vici.Session(s).list_sas() for k,v in sa.items()]))
finally: s.close()
"""
    command("network/ipsec-sas", ["/usr/bin/python3", "-c", vici_code])
    try:
        import platform
        import shutil

        memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        disk = shutil.disk_usage("/")
        os_facts = {}
        for source in (Path("/etc/os-release"), Path("/usr/lib/os-release")):
            try:
                for line in read_regular(source).decode().splitlines():
                    key, separator, value = line.partition("=")
                    if separator and key in {"ID", "VERSION_ID"}:
                        os_facts["os_" + key.lower()] = value.strip("\"'")
                break
            except (OSError, ValueError):
                continue
        add(
            "host/resources",
            {
                **os_facts,
                "kernel": platform.release(),
                "machine": platform.machine(),
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "uptime": float(Path("/proc/uptime").read_text().split()[0]),
                "memory_total": int(memory["MemTotal"].split()[0]) * 1024,
                "memory_available": int(memory["MemAvailable"].split()[0]) * 1024,
                "disk_total": disk.total,
                "disk_free": disk.free,
            },
        )
    except (OSError, ValueError, KeyError):
        add("host/resources", {}, outcome="unavailable")
    storage = storage_facts()
    add(
        "host/storage",
        storage,
        outcome="ok" if all(item["available"] for item in storage) else "partial",
    )
    sample_started = time.monotonic()
    samples, sample_outcome = resource_samples(min(deadline, sample_started + 10))
    add("host/resource-samples", samples, outcome=sample_outcome, started=sample_started)
    time.sleep(max(0, min(2 - (time.monotonic() - started), deadline - time.monotonic())))
    snapshot(1)

    for name, argv in (
        ("host/uptime", ["uptime"]),
        ("host/free", ["free", "-m"]),
        ("host/vmstat", ["vmstat", "1", "5"]),
        ("host/dmesg", ["dmesg", "--time-format=iso"]),
        ("host/cloud-init-status", ["cloud-init", "status", "--long"]),
    ):
        command(name, argv, lambda _text: {})
    for unit in UNITS:
        command(
            "service-status/" + unit,
            ["systemctl", "status", unit + ".service", "--full", "--no-pager", "--lines=30"],
            lambda _text: {},
        )
    for label, filename in (
        ("route-fix", "/var/log/vpngw-fix-routes.log"),
        ("firewall-setup", "/var/log/vpngw-firewall-setup.log"),
        ("cloud-init", "/var/log/cloud-init.log"),
        ("cloud-init-output", "/var/log/cloud-init-output.log"),
    ):
        started_file = time.monotonic()
        if started_file >= deadline:
            add("file-logs/" + label, {}, outcome="timeout")
            continue
        try:
            raw_text, truncated, changed = read_log_tail(Path(filename))
            add(
                "file-logs/" + label,
                {},
                outcome="changed" if changed else "source-limit" if truncated else "ok",
                started=started_file,
                text={"stdout": redact_text(raw_text)},
            )
        except FileNotFoundError:
            add("file-logs/" + label, {}, outcome="not-applicable")
        except (OSError, ValueError):
            add("file-logs/" + label, {}, outcome="unavailable")

    for unit in UNITS:
        command(
            "logs/" + unit,
            [
                "journalctl",
                "--reverse",
                "--all",
                "--utc",
                "--no-pager",
                "-o",
                "json",
                "-n",
                "5001",
                "--since",
                request["since"],
                "--until",
                request["until"],
                "-u",
                unit + ".service",
                "--output-fields=MESSAGE,_BOOT_ID,__REALTIME_TIMESTAMP,__MONOTONIC_TIMESTAMP,_SYSTEMD_INVOCATION_ID,INVOCATION_ID,_SYSTEMD_UNIT,_PID,PRIORITY,ERRNO",
            ],
            journal_events,
        )
    for index, unit in worker_units:
        command(
            f"logs/package-worker/{index}",
            [
                "journalctl",
                "--reverse",
                "--all",
                "--utc",
                "--no-pager",
                "-o",
                "json",
                "-n",
                "5001",
                "--since",
                request["since"],
                "--until",
                request["until"],
                "-u",
                unit,
                "--output-fields=MESSAGE,_BOOT_ID,__REALTIME_TIMESTAMP,__MONOTONIC_TIMESTAMP,_SYSTEMD_INVOCATION_ID,INVOCATION_ID,_SYSTEMD_UNIT,_PID,PRIORITY,ERRNO",
            ],
            journal_events,
        )
    command(
        "logs/kernel",
        [
            "journalctl",
            "--reverse",
            "--all",
            "--utc",
            "--no-pager",
            "-o",
            "json",
            "-n",
            "5001",
            "--since",
            request["since"],
            "--until",
            request["until"],
            "_TRANSPORT=kernel",
            "--output-fields=MESSAGE,_BOOT_ID,__REALTIME_TIMESTAMP,__MONOTONIC_TIMESTAMP,_SYSTEMD_INVOCATION_ID,INVOCATION_ID,_SYSTEMD_UNIT,_PID,PRIORITY,ERRNO",
        ],
        journal_events,
    )
    return records
