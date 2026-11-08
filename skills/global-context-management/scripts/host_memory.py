#!/usr/bin/env python3
"""Observe host headroom and supervise only explicitly launched foreground jobs."""

from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

GIB = 1024 ** 3
WARNING_BYTES = 4 * GIB
STOP_BYTES = 2 * GIB
MAX_PROBE_BYTES = 65536
POLICY = """## Host Memory Safety

- Keep 2 GiB of available host memory as the hard operational floor. Check the
  current execution host before workloads and monitor throughout agent-owned
  work, using `global-context-management` host-memory guidance and helper.
- At or below 4 GiB but above 2 GiB, tell the user memory headroom is low and
  continue the task. A warning alone must not stop work or block new launches.
- At or below 2 GiB, stop agent-owned work safely, preserve progress and tell
  the user: "The host is running out of memory; I stopped my work to protect
  the remaining headroom." Continue only lightweight communication, diagnosis
  and safe cleanup until memory recovers above 2 GiB; warn while below 4 GiB.
- If host memory cannot be measured reliably, report that separately and pause
  memory-heavy work until visibility is restored. Use available/reclaimable
  memory, not process RSS or raw free pages alone; do not count swap as RAM.
- Stop only exactly owned jobs and use each resource owner's cleanup path for
  browsers, containers and remote workloads. Never kill unrelated applications
  or OS services, tune OS memory, or reset workflow evidence or repair budgets.
- These are sampled operational controls, not a physical RAM reservation.
  Keep monitors active while owned work runs and close them when it finishes.
"""


class MeasurementError(ValueError):
    """A fixed diagnostic boundary; probe details never reach public output."""


def classify(available: int) -> str:
    if type(available) is not int or available < 0:
        raise MeasurementError("invalid memory count")
    if available <= STOP_BYTES:
        return "critical"
    return "warning" if available <= WARNING_BYTES else "normal"


def parse_darwin(raw: str, total: int) -> int:
    sizes = re.findall(r"page size of (\d+) bytes", raw)
    if len(sizes) != 1 or int(sizes[0]) not in {4096, 16384}:
        raise MeasurementError("invalid native page size")
    counts = []
    for key in ("Pages free", "Pages inactive", "Pages speculative"):
        values = re.findall(rf"^{re.escape(key)}:\s*(\d+)\.\s*$", raw, re.M)
        if len(values) != 1 or len(re.findall(rf"^{re.escape(key)}:", raw, re.M)) != 1:
            raise MeasurementError("invalid native memory fields")
        counts.append(int(values[0]))
    available = sum(counts) * int(sizes[0])
    if type(total) is not int or not 0 < total <= 2 ** 60 or available > total:
        raise MeasurementError("invalid native memory total")
    return available


def parse_linux(raw: str) -> int:
    fields = []
    for key in ("MemTotal", "MemAvailable"):
        values = re.findall(rf"^{key}:\s*(\d+)\s+kB\s*$", raw, re.M)
        if len(values) != 1 or len(re.findall(rf"^{key}:", raw, re.M)) != 1:
            raise MeasurementError("invalid proc memory fields")
        fields.append(int(values[0]) * 1024)
    total, available = fields
    if not 0 < total <= 2 ** 60 or available > total:
        raise MeasurementError("invalid proc memory total")
    return available


def _command(arguments: list[str]) -> str:
    result = subprocess.run(arguments, stdin=subprocess.DEVNULL, capture_output=True,
                            timeout=2, env={"LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"})
    if result.returncode or len(result.stdout) > MAX_PROBE_BYTES:
        raise MeasurementError("native measurement unavailable")
    return result.stdout.decode("ascii", errors="strict").strip()


def _read(path: Path) -> str:
    with path.open("rb") as stream:
        raw = stream.read(MAX_PROBE_BYTES + 1)
    if len(raw) > MAX_PROBE_BYTES:
        raise MeasurementError("oversized native measurement")
    return raw.decode("ascii", errors="strict")


def _sample() -> dict:
    if sys.platform == "darwin":
        raw = _command(["/usr/bin/vm_stat"])
        total = int(_command(["/usr/sbin/sysctl", "-n", "hw.memsize"]))
        available = parse_darwin(raw, total)
        # Pressure is advisory. Only the requested 2 GiB numeric floor stops work.
        try:
            pressure = {"1": "normal", "2": "warning", "4": "critical"}.get(
                _command(["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"]), "unknown")
        except (OSError, ValueError, subprocess.SubprocessError):
            pressure = "unknown"
        return {"available_bytes": available, "method": "darwin_reclaimable_estimate",
                "pressure_advisory": pressure}
    if sys.platform.startswith("linux"):
        # Guest-visible /proc alone cannot establish physical-host headroom.
        groups = _read(Path("/proc/self/cgroup"))
        if (Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
                or any(line.rsplit(":", 1)[-1] != "/" for line in groups.splitlines())):
            raise MeasurementError("host visibility unavailable")
        return {"available_bytes": parse_linux(_read(Path("/proc/meminfo"))),
                "method": "linux_memavailable", "pressure_advisory": "not_observed"}
    raise MeasurementError("unsupported host measurement")


def sample() -> dict:
    observed = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        result = _sample()
        return {"status": classify(result["available_bytes"]), "observed_at": observed, **result}
    except Exception:
        return {"status": "unknown", "observed_at": observed,
                "reason": "host_memory_measurement_unavailable"}


def emit(observation: dict) -> None:
    messages = {
        "normal": "Host memory headroom is above 4 GiB.",
        "warning": "Memory warning: host headroom is at or below 4 GiB; the task continues.",
        "critical": "The host is running out of memory; stopping agent-owned work at the 2 GiB floor.",
        "unknown": "Host memory cannot be verified; memory-heavy work is paused.",
    }
    print(json.dumps({**observation, "message": messages[observation["status"]]}, sort_keys=True),
          file=sys.stderr, flush=True)


def _spawn(command: list[str]) -> tuple[int, int, int]:
    """Keep an owned session leader alive until parent cleanup; never reuse its PID."""
    reader, writer = os.pipe()
    gate_reader, gate_writer = os.pipe()
    try:
        pid = os.fork()
    except BaseException:
        os.close(reader)
        os.close(writer)
        os.close(gate_reader)
        os.close(gate_writer)
        raise
    if pid:
        os.close(writer)
        os.close(gate_reader)
        return pid, reader, gate_writer
    os.close(reader)
    os.close(gate_writer)
    try:
        os.setsid()
        # Caught handlers reset to default in exec children; the anchor stays alive.
        signal.signal(signal.SIGTERM, lambda *_: None)
        signal.signal(signal.SIGINT, lambda *_: None)
        os.write(writer, b'{"event":"ready"}\n')
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT, signal.SIGTERM})
        # No workload can exist until the parent has recorded group ownership.
        if os.read(gate_reader, 1) != b"G":
            os._exit(127)
        os.close(gate_reader)
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, close_fds=True)
            code = result.returncode
        except (OSError, ValueError):
            code = 127
        os.write(writer, (json.dumps({"event": "exit", "code": code}) + "\n").encode())
        while True:
            signal.pause()
    except BaseException:
        os._exit(127)


def _stop(pid: int, ready: bool) -> bool:
    # This direct child is deliberately not polled/reaped until after signalling.
    # Its live/zombie PID pins the process-group identity throughout cleanup.
    # Cancellation can arrive before the ready message is consumed. The pinned
    # direct child still proves whether its private group was already created.
    try:
        ready = os.getpgid(pid) == pid
    except ProcessLookupError:
        ready = False
    sender = os.killpg if ready else os.kill
    signalled = True
    for sig, delay in ((signal.SIGTERM, 0.2), (signal.SIGKILL, 0)):
        try:
            sender(pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            signalled = False
            break
        if delay:
            time.sleep(delay)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            if os.waitpid(pid, os.WNOHANG)[0] == pid:
                return signalled
        except ChildProcessError:
            return False
        time.sleep(0.02)
    return False


def supervise(command: list[str], *, probe=sample, interval: float = 1.0) -> int:
    current = probe()
    emit(current)
    if current["status"] not in {"normal", "warning"}:
        return 75
    if not hasattr(os, "fork") or signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        emit({"status": "unknown", "reason": "owned_supervision_unavailable"})
        return 75
    pid = reader = gate = None
    ready, result, finished = False, 70, False
    pending = b""
    previous = current["status"]
    next_sample = time.monotonic() + interval
    startup_deadline = time.monotonic() + 5
    try:
        # Publish the exact child identity before cancellation can run cleanup.
        old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        try:
            pid, reader, gate = _spawn(command)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
        with selectors.DefaultSelector() as selector:
            selector.register(reader, selectors.EVENT_READ)
            while not finished:
                if selector.select(max(0, min(interval, next_sample - time.monotonic()))):
                    data = os.read(reader, 4096)
                    if not data:
                        break
                    pending += data
                    if len(pending) > 4096:
                        break
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        event = json.loads(line)
                        if event == {"event": "ready"}:
                            ready = True
                            os.write(gate, b"G")
                            os.close(gate)
                            gate = None
                        elif ready and set(event) == {"event", "code"} and event["event"] == "exit" and type(event["code"]) is int:
                            result = event["code"] if event["code"] >= 0 else 128 - event["code"]
                            finished = True
                            break
                        else:
                            result, finished = 70, True
                            break
                if finished:
                    break
                if not ready and time.monotonic() >= startup_deadline:
                    break
                if time.monotonic() >= next_sample:
                    current = probe()
                    if current["status"] != previous:
                        emit(current)
                        previous = current["status"]
                    if current["status"] not in {"normal", "warning"}:
                        result = 75
                        break
                    next_sample = time.monotonic() + interval
    except KeyboardInterrupt:
        result = 130
    except Exception:
        emit({"status": "unknown", "reason": "supervision_observation_failed"})
    finally:
        cleanup_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        try:
            # Keep the pipe open while the work exits so the anchor remains alive
            # until the owned group's final signal and reap have completed.
            if pid is not None and not _stop(pid, ready):
                print('{"status":"cleanup_unverified"}', file=sys.stderr, flush=True)
                result = 70
        finally:
            if reader is not None:
                os.close(reader)
            if gate is not None:
                os.close(gate)
            signal.pthread_sigmask(signal.SIG_SETMASK, cleanup_mask)
    return result


def watch(seconds: float, *, probe=sample, interval: float = 1.0) -> int:
    deadline = time.monotonic() + seconds
    previous = None
    while True:
        current = probe()
        if current["status"] != previous:
            emit(current)
            previous = current["status"]
        if current["status"] not in {"normal", "warning"}:
            return 75
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 0
        time.sleep(min(interval, remaining))


def validate_policy(text: str) -> None:
    visible = re.sub(r"<!--[\s\S]*?-->", "", text)
    visible = re.sub(r"(?ms)^(```|~~~)[^\n]*\n.*?^\1[^\n]*$", "", visible)
    matches = list(re.finditer(r"(?m)^## Host Memory Safety\s*$", visible))
    headings = re.findall(r"(?im)^ {0,3}#{1,6}\s+[^\n]*host memory safety[^\n]*$", visible)
    setext = re.search(r"(?im)^.*host memory safety[^\n]*\n {0,3}[=-]+\s*$", visible)
    if len(matches) != 1 or len(headings) != 1 or setext:
        raise ValueError("missing or duplicate memory policy")
    section = visible[matches[0].start():]
    next_heading = re.search(r"(?m)^#{1,2} ", section[len("## Host Memory Safety"):])
    if next_heading:
        section = section[:len("## Host Memory Safety") + next_heading.start()]
    if " ".join(section.split()) != " ".join(POLICY.split()):
        raise ValueError("stale memory policy")


def check_policy(path: Path) -> int:
    try:
        path = path.absolute()
        if path.resolve() != path or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("unsafe policy path")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or info.st_mode & 0o022):
                raise ValueError("unsafe policy file")
            raw = stream.read(131073)
        if len(raw) > 131072:
            raise ValueError("oversized policy")
        validate_policy(raw.decode("utf-8"))
    except (OSError, ValueError):
        print('{"status":"NOT_ALIGNED","surface":"host_memory_policy"}')
        return 1
    print('{"status":"STATIC_PASS","surface":"host_memory_policy","runtime":"NOT_RUN"}')
    return 0


def _cancel(*_args) -> None:
    raise KeyboardInterrupt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("snapshot", help="Read host headroom; warning exits successfully.")
    watcher = commands.add_parser("watch", help="Read-only bounded monitoring; does not stop external resources.")
    watcher.add_argument("--seconds", type=float, required=True)
    runner = commands.add_parser("run", help="Supervise one authorized foreground command and its process group.")
    runner.add_argument("command", nargs=argparse.REMAINDER)
    policy = commands.add_parser("check-policy", help="Read-only check of just the global memory policy.")
    policy.add_argument("--instructions", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.action == "check-policy":
        return check_policy(args.instructions)
    if args.action == "snapshot":
        current = sample()
        emit(current)
        return 0 if current["status"] in {"normal", "warning"} else 75
    if args.action == "watch":
        if not 0 < args.seconds <= 43200:
            parser.error("--seconds must be between 0 and 43200")
        signal.signal(signal.SIGTERM, _cancel)
        try:
            return watch(args.seconds)
        except KeyboardInterrupt:
            return 130
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("run requires a command after --")
    signal.signal(signal.SIGTERM, _cancel)
    try:
        return supervise(command)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
