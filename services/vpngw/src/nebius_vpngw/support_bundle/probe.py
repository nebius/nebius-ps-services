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

from .projection import ENUMS, MAX_BYTES, project

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
        "host/time",
        "host/forwarding",
        "host/packages",
        "host/agent-package",
        "host/resources",
        "logs/kernel",
    }
    fixed.update("services/" + unit for unit in UNITS)
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


def journal_events(text: str) -> list[dict[str, Any]]:
    events = []
    omitted = 0
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
            r"\[Agent\].*(?:[Ff]ailed|[Ee]rror)|cloud-init.*(?:failed|ERROR)",
            "agent-or-bootstrap-failed",
        ),
    )
    codes = {code for code in ENUMS if "-" in code}
    for line in text.splitlines():
        try:
            event = json.loads(line)
            message = event.get("MESSAGE", "")
            if not isinstance(message, str):
                raise ValueError("invalid-data")
            found = codes.intersection(re.findall(r"[a-z][a-z0-9-]{1,95}", message))
            found.update(code for pattern, code in templates if re.search(pattern, message))
            if found:
                events.append(
                    {
                        "timestamp": str(event.get("__REALTIME_TIMESTAMP", "")),
                        "boot_id": event.get("_BOOT_ID"),
                        "reasons": sorted(found),
                    }
                )
            else:
                omitted += 1
        except (ValueError, TypeError, AttributeError):
            omitted += 1
    events.append({"name": "privacy-omitted", "total": omitted})
    return events


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
    argv: list[str], *, timeout: float = 30, limit: int = MAX_BYTES, stdin: bytes | None = None
) -> tuple[bytes, str, int | None]:
    """Drain bounded pipes and terminate only this owned process group."""
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    output = bytearray()
    deadline = time.monotonic() + timeout
    outcome = "ok"
    try:
        with selectors.DefaultSelector() as selector:
            assert process.stdout is not None
            selector.register(process.stdout, selectors.EVENT_READ)
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
                        output.extend(chunk[: max(0, limit - len(output))])
                        if len(output) >= limit:
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
    ) -> None:
        omissions = [0]
        limits = [0]
        clean = project(value, omitted=omissions, limited=limits)
        if outcome == "ok" and limits[0]:
            outcome = "source-limit"
        elif outcome == "ok" and value and not clean and omissions[0]:
            outcome = "partial"
        record = {
            "probe": name,
            "outcome": outcome,
            "data": clean,
            "omitted_fields": omissions[0],
            "truncated_fields": limits[0],
            "exit_code": exit_code,
            "observed_at": time.time(),
            "duration_seconds": 0 if started is None else time.monotonic() - started,
        }
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
            raw, outcome, code = bounded_command(argv, timeout=min(30, deadline - started))
            if name.startswith("logs/"):
                lines = raw.splitlines()
                if len(lines) > 5000:
                    raw = b"\n".join(lines[:5000])
                    outcome = "source-limit"
            try:
                value = parser(raw.decode("utf-8", errors="replace")) if raw else {}
            except (ValueError, TypeError, KeyError):
                value = {}
                if outcome == "ok":
                    outcome = "invalid-data"
            add(name, value, outcome=outcome, exit_code=code, started=started)
            return value
        except (OSError, ValueError):
            add(name, {}, outcome="unavailable", started=started)

    def fields(text: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for line in text.splitlines():
            key, separator, value = line.partition("=")
            if separator:
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
                if re.fullmatch(
                    r"nebius-vpngw-(?:repair-)?package-[a-f0-9]{64}-[0-9]+\.service", unit
                ):
                    worker_units.append((index, unit))
                    command(
                        f"services/package-worker/{index}",
                        ["systemctl", "show", unit, "--property=" + ",".join(PROPERTIES)],
                        fields,
                    )
            except (OSError, ValueError, AttributeError, TypeError):
                add(f"state/worker/{index}", {}, outcome="invalid-data")
    for unit in UNITS:
        command(
            "services/" + unit,
            ["systemctl", "show", unit + ".service", "--property=" + ",".join(PROPERTIES)],
            fields,
        )
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
        ("xfrm-policy", ["ip", "-j", "xfrm", "policy"]),
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
    command(
        "host/time",
        ["timedatectl", "show", "--property=NTPSynchronized,TimeUSec,RTCTimeUSec"],
        fields,
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
        return {decode(k): ([dict(name=decode(n), **decode(v)) for n,v in val.items()] if decode(k)=='child-sas' else decode(val)) for k,val in x.items()}
    if isinstance(x, list): return [decode(v) for v in x]
    return x
s=socket.socket(socket.AF_UNIX); s.settimeout(10); s.connect('/var/run/charon.vici')
try:
    print(json.dumps([dict(name=decode(k), **decode(v)) for sa in vici.Session(s).list_sas() for k,v in sa.items()]))
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
    time.sleep(max(0, min(2 - (time.monotonic() - started), deadline - time.monotonic())))
    snapshot(1)

    for unit in UNITS:
        command(
            "logs/" + unit,
            [
                "journalctl",
                "--reverse",
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
                "--output-fields=MESSAGE,_BOOT_ID,__REALTIME_TIMESTAMP",
            ],
            journal_events,
        )
    for index, unit in worker_units:
        command(
            f"logs/package-worker/{index}",
            [
                "journalctl",
                "--reverse",
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
                "--output-fields=MESSAGE,_BOOT_ID,__REALTIME_TIMESTAMP",
            ],
            journal_events,
        )
    command(
        "logs/kernel",
        [
            "journalctl",
            "--reverse",
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
            "--output-fields=MESSAGE,_BOOT_ID,__REALTIME_TIMESTAMP",
        ],
        journal_events,
    )
    return records
