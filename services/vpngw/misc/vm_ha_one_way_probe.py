#!/usr/bin/env python3
"""Observe one-way packet recovery through a separately operated VPN transfer."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROBE_SCHEMA = "nebius-vpngw/vm-ha-one-way-probe-v1"
_MAX_COUNT = 10_000
_SSH_USER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,31}")
_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
_REPLY_RE = re.compile(
    r"^\[(?P<timestamp>\d+(?:\.\d+)?)\]\s+.*\bicmp_seq=(?P<sequence>\d+)"
    r".*\btime[=<](?P<latency>\d+(?:\.\d+)?)\s*ms\b.*$"
)
_SUMMARY_RE = re.compile(
    r"^(?P<transmitted>\d+) packets transmitted,\s+"
    r"(?P<received>\d+)(?: packets)? received"
    r"(?P<errors>,\s+\+\d+ errors?)?,\s+"
    r"\d+(?:\.\d+)?% packet loss,\s+time \d+ms$"
)
_NO_ANSWER_RE = re.compile(
    r"^\[(?:\d+(?:\.\d+)?)\]\s+no answer yet for icmp_seq=(?P<sequence>\d+)$"
)
_STATISTICS_RE = re.compile(r"^--- .+ ping statistics ---$")
_RTT_RE = re.compile(
    r"^(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = "
    r"\d+(?:\.\d+)?/\d+(?:\.\d+)?/\d+(?:\.\d+)?/\d+(?:\.\d+)? ms$"
)


class ProbeError(RuntimeError):
    """The observation could not produce a complete, trustworthy summary."""


@dataclass(frozen=True)
class ProbeArguments:
    ssh_target: str
    known_hosts_file: Path
    identity_file: Path
    destination: str
    count: int
    direction_label: str


def _regular_readable_file(path: Path, *, label: str, private: bool = False) -> Path:
    candidate = path.expanduser()
    if candidate.is_symlink() or not candidate.is_file():
        raise argparse.ArgumentTypeError(f"{label} must be a regular non-symlink file")
    metadata = candidate.stat()
    mode = stat.S_IMODE(metadata.st_mode)
    if metadata.st_uid != os.geteuid() or not mode & stat.S_IRUSR:
        raise argparse.ArgumentTypeError(f"{label} must be owned and readable by the current user")
    if private and mode & 0o077:
        raise argparse.ArgumentTypeError(f"{label} must not grant group or other permissions")
    if not private and mode & 0o022:
        raise argparse.ArgumentTypeError(f"{label} must not be group- or other-writable")
    return candidate.resolve()


def _parse_arguments(argv: Sequence[str] | None = None) -> ProbeArguments:
    parser = argparse.ArgumentParser(
        description=(
            "Observe one-way ICMP timing from a test VM while failover or failback "
            "is run separately. This helper never changes VPN or cloud state."
        )
    )
    parser.add_argument("--ssh-target", required=True, metavar="USER@IPV4")
    parser.add_argument("--known-hosts-file", required=True, type=Path)
    parser.add_argument("--identity-file", required=True, type=Path)
    parser.add_argument("--destination", required=True, metavar="IPV4")
    parser.add_argument("--count", required=True, type=int)
    parser.add_argument("--direction-label", required=True)
    values = parser.parse_args(argv)

    user, separator, host = values.ssh_target.partition("@")
    try:
        target_ip = ipaddress.IPv4Address(host)
        destination_ip = ipaddress.IPv4Address(values.destination)
    except ipaddress.AddressValueError as error:
        parser.error("SSH target and destination must use literal IPv4 addresses")
        raise AssertionError from error
    if separator != "@" or not _SSH_USER_RE.fullmatch(user):
        parser.error("--ssh-target must be USER@IPV4 with a simple SSH user name")
    if not 1 <= values.count <= _MAX_COUNT:
        parser.error(f"--count must be between 1 and {_MAX_COUNT}")
    if not _LABEL_RE.fullmatch(values.direction_label):
        parser.error("--direction-label contains unsupported characters")
    try:
        known_hosts = _regular_readable_file(
            values.known_hosts_file,
            label="--known-hosts-file",
        )
        identity = _regular_readable_file(
            values.identity_file,
            label="--identity-file",
            private=True,
        )
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))
    return ProbeArguments(
        ssh_target=f"{user}@{target_ip}",
        known_hosts_file=known_hosts,
        identity_file=identity,
        destination=str(destination_ip),
        count=values.count,
        direction_label=values.direction_label,
    )


def _ssh_command(arguments: ProbeArguments) -> list[str]:
    command = [
        "ssh",
        "-F",
        "/dev/null",
        "-o",
        "BatchMode=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={arguments.known_hosts_file}",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        "ProxyCommand=none",
        "-o",
        "ProxyJump=none",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "LogLevel=ERROR",
        "-i",
        str(arguments.identity_file),
    ]
    command.extend(
        (
            arguments.ssh_target,
            f"env LC_ALL=C ping -n -D -O -i 0.2 -W 1 -c {arguments.count} {arguments.destination}",
        )
    )
    return command


def parse_probe_output(
    output: str,
    *,
    expected_count: int,
    direction_label: str,
) -> list[dict[str, Any]]:
    """Parse a complete Linux ping transcript into endpoint-free JSONL records."""

    replies: dict[int, tuple[float, float]] = {}
    reply_order: list[int] = []
    summary_match: re.Match[str] | None = None
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        reply = _REPLY_RE.fullmatch(line)
        if reply is not None:
            sequence = int(reply.group("sequence"))
            if not 1 <= sequence <= expected_count:
                raise ProbeError("ping reply sequence is outside the declared trial")
            if sequence not in replies:
                replies[sequence] = (
                    float(reply.group("timestamp")),
                    float(reply.group("latency")),
                )
                reply_order.append(sequence)
            continue
        no_answer = _NO_ANSWER_RE.fullmatch(line)
        if no_answer is not None:
            sequence = int(no_answer.group("sequence"))
            if not 1 <= sequence <= expected_count:
                raise ProbeError("ping loss sequence is outside the declared trial")
            continue
        candidate = _SUMMARY_RE.fullmatch(line)
        if candidate is not None:
            if summary_match is not None or candidate.group("errors") is not None:
                raise ProbeError("ping reported duplicate summary or runtime errors")
            summary_match = candidate
            continue
        if line.startswith("PING ") or _STATISTICS_RE.fullmatch(line) or _RTT_RE.fullmatch(line):
            continue
        raise ProbeError("ping returned unsupported or error output")
    if summary_match is None:
        raise ProbeError("ping did not return a complete packet summary")
    transmitted = int(summary_match.group("transmitted"))
    received = int(summary_match.group("received"))
    if transmitted != expected_count or received != len(replies):
        raise ProbeError("ping summary does not match the unique reply evidence")

    events = [
        {
            "schema": PROBE_SCHEMA,
            "event": "reply",
            "direction": direction_label,
            "sequence": sequence,
            "observed_at": replies[sequence][0],
            "latency_ms": replies[sequence][1],
        }
        for sequence in sorted(replies)
    ]
    missing = [sequence for sequence in range(1, transmitted + 1) if sequence not in replies]
    recovery_start: int | None = None
    recovery_confirmation: int | None = None
    last_loss_sequence = missing[-1] if missing else 0
    recovery_run: list[int] = []
    for sequence in reply_order:
        if sequence <= last_loss_sequence:
            continue
        recovery_run = (
            [*recovery_run, sequence]
            if recovery_run and sequence == recovery_run[-1] + 1
            else [sequence]
        )
        if len(recovery_run) == 5:
            recovery_start = recovery_run[0]
            recovery_confirmation = recovery_run[-1]
            break
    events.append(
        {
            "schema": PROBE_SCHEMA,
            "event": "summary",
            "direction": direction_label,
            "transmitted": transmitted,
            "received": received,
            "lost": transmitted - received,
            "missing_sequences": missing,
            "loss_percent": round((transmitted - received) * 100.0 / transmitted, 3),
            "first_reply_at": replies[min(replies)][0] if replies else None,
            "last_reply_at": replies[max(replies)][0] if replies else None,
            "last_loss_sequence": last_loss_sequence if missing else None,
            "stable_recovery_sequence": recovery_start,
            "stable_recovery_started_at": (
                replies[recovery_start][0] if recovery_start is not None else None
            ),
            "stable_recovery_confirmed_at": (
                replies[recovery_confirmation][0] if recovery_confirmation is not None else None
            ),
            "stable_recovery_replies": 5,
            "complete": True,
        }
    )
    return events


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    try:
        completed = subprocess.run(
            _ssh_command(arguments),
            capture_output=True,
            text=True,
            check=False,
            timeout=max(30.0, arguments.count * 1.2 + 20.0),
        )
        if completed.returncode not in {0, 1}:
            raise ProbeError("SSH or remote ping execution failed")
        if completed.stderr.strip():
            raise ProbeError("SSH or remote ping reported an execution error")
        records = parse_probe_output(
            completed.stdout,
            expected_count=arguments.count,
            direction_label=arguments.direction_label,
        )
    except (OSError, subprocess.TimeoutExpired, ProbeError) as error:
        print(f"Probe failed: {error}", file=sys.stderr)
        return 1
    for record in records:
        print(json.dumps(record, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
