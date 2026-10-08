"""Read-only, key-free packet observations. This module also runs over SSH on stdlib Python.

No installed gateway package or configuration is read by the remote probe. Only the
allowlisted normalized observations cross SSH; command output is never returned.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import selectors
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

SCHEMA = "nebius-vpngw.packet-health.v1"
OBSERVATION_SECONDS = 3
GATEWAY_TIMEOUT = 30
MAX_OUTPUT = 4 * 1024 * 1024
REASONS = {"", "unavailable", "ambiguous", "incomplete"}
COUNTERS = ("late", "duplicate", "integrity", "packets")


def _integer(value: Any, base: int = 10) -> int:
    if isinstance(value, bool):
        raise ValueError("invalid counter")
    result = int(str(value), base)
    if result < 0:
        raise ValueError("invalid counter")
    return result


def _section(tokens: list[str], offset: int) -> tuple[dict[str, Any], int]:
    """Parse balanced VICI raw sections and lists, including unknown nested fields."""
    result: dict[str, Any] = {}
    while offset < len(tokens) and tokens[offset] != "}":
        key = tokens[offset]
        offset += 1
        if offset >= len(tokens) or key in result:
            raise ValueError("invalid VICI section")
        kind = tokens[offset]
        offset += 1
        if kind == "=" and offset < len(tokens) and tokens[offset] == "[":
            kind = "["
            offset += 1
        if kind == "{":
            value, offset = _section(tokens, offset)
            result[key] = value
        elif kind == "[":
            while offset < len(tokens) and tokens[offset] != "]":
                if tokens[offset] in {"{", "}", "["}:
                    raise ValueError("invalid VICI list")
                offset += 1
            if offset >= len(tokens):
                raise ValueError("incomplete VICI list")
            offset += 1
        elif kind == "=":
            words = []
            while offset < len(tokens) and tokens[offset] != "}":
                if offset + 1 < len(tokens) and tokens[offset + 1] in {"=", "{", "["}:
                    break
                if tokens[offset] in {"{", "[", "]", "="}:
                    raise ValueError("invalid VICI value")
                words.append(tokens[offset])
                offset += 1
            result[key] = " ".join(words)
        else:
            raise ValueError("invalid VICI token")
    if offset >= len(tokens):
        raise ValueError("incomplete VICI section")
    return result, offset + 1


def parse_sas(raw: str) -> list[dict[str, Any]]:
    tokens = re.findall(r"[^\s{}\[\]=]+|[{}\[\]=]", raw)
    offset = 0
    children = []
    replied = False
    while offset < len(tokens):
        header = tokens[offset : offset + 3]
        if header not in (["list-sa", "event", "{"], ["list-sas", "reply", "{"]):
            raise ValueError("unrecognized VICI output")
        section, offset = _section(tokens, offset + 3)
        if header[0] == "list-sas":
            if section or replied or offset != len(tokens):
                raise ValueError("invalid VICI reply")
            replied = True
            continue
        for ike in section.values():
            if not isinstance(ike, dict):
                raise ValueError("invalid IKE section")
            child_sas = ike.get("child-sas", {})
            if not isinstance(child_sas, dict):
                raise ValueError("invalid CHILD section")
            for child in child_sas.values():
                if not isinstance(child, dict):
                    raise ValueError("invalid CHILD")
                if child.get("state") not in {"INSTALLED", "REKEYING", "REKEYED", "DELETING"}:
                    continue
                if child.get("protocol") != "ESP":
                    continue
                # VICI prints SPI and interface IDs as hexadecimal even without 0x.
                children.append(
                    {
                        "name": child["name"],
                        "unique": _integer(child["uniqueid"]),
                        "spi": _integer(child["spi-in"], 16),
                        "reqid": _integer(child["reqid"]),
                        "if_id": _integer(child.get("if-id-in", "0"), 16),
                        "src": str(ipaddress.ip_address(ike["remote-host"])),
                        "dst": str(ipaddress.ip_address(ike["local-host"])),
                    }
                )
    if not replied:
        raise ValueError("incomplete VICI response")
    return children


def parse_xfrm(raw: str) -> list[dict[str, Any]]:
    blocks = re.split(r"(?m)(?=^src\s)", raw)
    states = []
    for block in blocks:
        if not block.strip():
            continue
        endpoints = re.match(r"src (\S+) dst (\S+)", block)
        protocol = re.search(
            r"\bproto (\S+) spi (0x[0-9a-fA-F]+)(?:\(\d+\))? "
            r"reqid (\d+)(?:\(0x[0-9a-fA-F]+\))? mode (\S+)",
            block,
        )
        if endpoints is None or protocol is None:
            raise ValueError("invalid XFRM state")
        if protocol[1] != "esp":
            continue
        # Reject key-bearing input even in local fixtures. Collection always requests nokeys.
        for line in block.splitlines():
            if re.match(r"\s*(?:auth(?:-trunc)?|enc|aead)\s", line) and re.search(
                r"\b0x[0-9a-fA-F]+", line
            ):
                raise ValueError("key-bearing XFRM output")
        window = re.search(r"\breplay_window (\d+)", block)
        if window is None:
            window = re.search(r"(?m)^\s*replay-window (\d+)(?: seq \S+)? flag\b", block)
        stats = re.search(r"\bstats:\s*replay-window (\d+) replay (\d+) failed (\d+)", block)
        lifetime = re.search(r"\blifetime current:\s*(\d+)\(bytes\),\s*(\d+)\(packets\)", block)
        created = re.search(r"(?m)^\s*add (\S+ \S+) use\b", block)
        if window is None or stats is None or lifetime is None or created is None:
            raise ValueError("incomplete XFRM state")
        if_id = re.search(r"\bif_id (0x[0-9a-fA-F]+|\d+)", block)
        mark = re.search(r"\bmark (0x[0-9a-fA-F]+|\d+)/(0x[0-9a-fA-F]+|\d+)", block)
        src = ipaddress.ip_address(endpoints[1])
        dst = ipaddress.ip_address(endpoints[2])
        states.append(
            {
                "src": str(src),
                "dst": str(dst),
                "family": src.version,
                "spi": int(protocol[2], 16),
                "reqid": int(protocol[3]),
                "mode": protocol[4],
                "if_id": int(if_id[1], 0) if if_id else 0,
                "mark": [int(mark[1], 0), int(mark[2], 0)] if mark else [0, 0],
                "created": created[1],
                "window": int(window[1]),
                "late": int(stats[1]),
                "duplicate": int(stats[2]),
                "integrity": int(stats[3]),
                "packets": int(lifetime[2]),
            }
        )
    return states


def parse_links(raw: str) -> dict[str, dict[str, Any]]:
    result = {}
    for link in json.loads(raw):
        info = link.get("linkinfo", {})
        if info.get("info_kind") != "xfrm":
            continue
        rx = (link.get("stats64") or link.get("stats") or {})["rx"]
        if_id = info["info_data"]["if_id"]
        if isinstance(if_id, str):
            if_id = int(if_id, 0)
        name = link["ifname"]
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,64}", name):
            raise ValueError("invalid interface name")
        result[str(_integer(link["ifindex"]))] = {
            "name": name,
            "if_id": _integer(if_id),
            **{key: _integer(rx[key]) for key in ("errors", "dropped", "packets")},
        }
    return result


def _command(
    args: list[str], deadline: float, *, input_text: str | None = None, timeout: float = 3
) -> str:
    """Bound runtime and captured bytes; stderr is never captured or forwarded."""
    end = min(deadline, time.monotonic() + timeout)
    if end <= time.monotonic():
        raise TimeoutError
    with subprocess.Popen(
        args,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
    ) as process:
        assert process.stdout is not None
        chunks = bytearray()
        try:
            with selectors.DefaultSelector() as selector:
                os.set_blocking(process.stdout.fileno(), False)
                selector.register(process.stdout, selectors.EVENT_READ)
                pending = memoryview(input_text.encode() if input_text is not None else b"")
                if process.stdin is not None:
                    if pending:
                        os.set_blocking(process.stdin.fileno(), False)
                        selector.register(process.stdin, selectors.EVENT_WRITE)
                    else:
                        process.stdin.close()
                while selector.get_map():
                    remaining = end - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    events = selector.select(remaining)
                    if not events:
                        raise TimeoutError
                    for key, event in events:
                        try:
                            if event & selectors.EVENT_WRITE:
                                pending = pending[os.write(key.fd, pending[:4096]) :]
                                if not pending:
                                    selector.unregister(key.fileobj)
                                    assert process.stdin is not None
                                    process.stdin.close()
                            else:
                                chunk = os.read(key.fd, min(65536, MAX_OUTPUT + 1 - len(chunks)))
                                if not chunk:
                                    selector.unregister(key.fileobj)
                                    continue
                                chunks.extend(chunk)
                                if len(chunks) > MAX_OUTPUT:
                                    raise ValueError("observation too large")
                        except BlockingIOError:
                            continue
            if process.wait(timeout=max(0.01, end - time.monotonic())) != 0:
                raise ValueError("observation failed")
            return chunks.decode("utf-8", errors="strict")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def _boot_id() -> str:
    value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value):
        raise ValueError("invalid boot identity")
    return value


def snapshot(expected: dict[str, int | None], deadline: float) -> dict[str, Any]:
    start = time.monotonic()
    boot = _boot_id()
    complete = True
    children: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    links: dict[str, dict[str, Any]] = {}
    global_seq = None
    for source in ("sas", "states", "links", "global"):
        try:
            if source == "sas":
                children = parse_sas(_command(["swanctl", "--list-sas", "--raw"], deadline))
            elif source == "states":
                states = parse_xfrm(
                    _command(["ip", "-s", "xfrm", "state", "list", "nokeys"], deadline)
                )
            elif source == "links":
                links = parse_links(_command(["ip", "-j", "-d", "-s", "link", "show"], deadline))
            else:
                text = Path("/proc/net/xfrm_stat").read_text()
                match = re.search(r"(?m)^XfrmInStateSeqError\s+(\d+)\s*$", text)
                if match is None:
                    raise ValueError("missing sequence counter")
                global_seq = int(match[1])
        except (OSError, ValueError, KeyError, TypeError, TimeoutError, subprocess.TimeoutExpired):
            complete = False
    if _boot_id() != boot:
        raise ValueError("boot changed")
    tunnels = {}
    for name in expected:
        mapped: dict[str, Any] = {}
        reason = "" if complete else "unavailable"
        used = set()
        relevant = set()
        for child in children:
            if child["name"] != name:
                continue
            if time.monotonic() >= deadline:
                reason = "incomplete"
                break
            for state in states:
                if all(state[key] == child[key] for key in ("src", "dst", "if_id")):
                    relevant.add(
                        json.dumps(
                            {
                                key: state[key]
                                for key in (
                                    "src",
                                    "dst",
                                    "family",
                                    "spi",
                                    "reqid",
                                    "if_id",
                                    "mark",
                                    "created",
                                    "mode",
                                )
                            },
                            sort_keys=True,
                        )
                    )
            matches = [
                state
                for state in states
                if all(state[key] == child[key] for key in ("src", "dst", "spi", "reqid", "if_id"))
                and state["mode"] == "tunnel"
            ]
            if len(matches) != 1:
                reason = "ambiguous" if len(matches) > 1 else "incomplete"
                continue
            state = matches[0]
            owners = [
                candidate
                for candidate in children
                if all(
                    state[key] == candidate[key] for key in ("src", "dst", "spi", "reqid", "if_id")
                )
            ]
            if len(owners) != 1:
                reason = "ambiguous"
                continue
            binding = [index for index, link in links.items() if link["if_id"] == state["if_id"]]
            if len(binding) != 1:
                reason = "ambiguous" if len(binding) > 1 else "incomplete"
            identity_fields = {
                key: state[key]
                for key in (
                    "src",
                    "dst",
                    "family",
                    "spi",
                    "reqid",
                    "if_id",
                    "mark",
                    "created",
                    "mode",
                )
            }
            kernel_identity = json.dumps(identity_fields, sort_keys=True)
            if kernel_identity in used:
                reason = "ambiguous"
                continue
            used.add(kernel_identity)
            identity_fields.update(boot=boot, child=child["unique"])
            identity = hashlib.sha256(
                json.dumps(identity_fields, sort_keys=True).encode()
            ).hexdigest()
            mapped[identity] = {
                **{key: state[key] for key in (*COUNTERS, "window", "spi")},
                "interface": binding[0] if len(binding) == 1 else None,
            }
        if (relevant - used or (not children and states)) and not reason:
            # VICI and Netlink reads are not atomic. Unaccounted kernel SAs may
            # still receive traffic; a mapped subset cannot prove a quiet tunnel.
            reason = "incomplete"
        tunnels[name] = {"sas": mapped, "complete": not reason, "reason": reason}
    return {
        "boot": boot,
        "started": start,
        "finished": time.monotonic(),
        "tunnels": tunnels,
        "links": links,
        "global_seq": global_seq,
    }


def probe(expected: dict[str, int | None]) -> dict[str, Any]:
    deadline = time.monotonic() + GATEWAY_TIMEOUT - 4
    samples: list[dict[str, Any] | None] = []
    for index in range(2):
        if index:
            time.sleep(OBSERVATION_SECONDS)
        try:
            samples.append(snapshot(expected, deadline))
        except (OSError, ValueError, KeyError, TypeError):
            samples.append(None)
    return {"schema": SCHEMA, "samples": samples}


def remote_script(expected: dict[str, int | None]) -> str:
    # No YAML, PSKs, agent imports, remote files, shell interpolation or CLI args.
    return Path(__file__).read_text() + "\nprint(json.dumps(probe(" + repr(expected) + ")))\n"


def _natural(value: Any) -> bool:
    return type(value) is int and 0 <= value <= 2**64 - 1


def validate_observation(value: Any, expected: dict[str, int | None]) -> dict[str, Any]:
    """Reject malformed remote data before presentation; no raw remote errors escape."""
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "samples"}
        or value["schema"] != SCHEMA
    ):
        raise ValueError("invalid observation")
    if not isinstance(value["samples"], list) or len(value["samples"]) != 2:
        raise ValueError("invalid samples")
    for sample in value["samples"]:
        if sample is None:
            continue
        if not isinstance(sample, dict) or set(sample) != {
            "boot",
            "started",
            "finished",
            "tunnels",
            "links",
            "global_seq",
        }:
            raise ValueError("invalid sample")
        if not isinstance(sample["boot"], str) or not re.fullmatch(
            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", sample["boot"]
        ):
            raise ValueError("invalid boot")
        for key in ("started", "finished"):
            if (
                type(sample[key]) not in (int, float)
                or sample[key] < 0
                or sample[key] > 10**12
                or not math.isfinite(sample[key])
            ):
                raise ValueError("invalid time")
        if not 0 <= sample["finished"] - sample["started"] <= GATEWAY_TIMEOUT:
            raise ValueError("invalid interval")
        if sample["global_seq"] is not None and not _natural(sample["global_seq"]):
            raise ValueError("invalid global counter")
        if not isinstance(sample["links"], dict) or len(sample["links"]) > 4096:
            raise ValueError("invalid interfaces")
        for index, link in sample["links"].items():
            if (
                not re.fullmatch(r"[0-9]{1,12}", index)
                or not isinstance(link, dict)
                or set(link) != {"name", "if_id", "errors", "dropped", "packets"}
            ):
                raise ValueError("invalid interface")
            if not isinstance(link["name"], str) or not re.fullmatch(
                r"[a-zA-Z0-9_.:-]{1,64}", link["name"]
            ):
                raise ValueError("invalid interface name")
            if not all(_natural(link[key]) for key in ("if_id", "errors", "dropped", "packets")):
                raise ValueError("invalid interface counters")
        if not isinstance(sample["tunnels"], dict) or set(sample["tunnels"]) != set(expected):
            raise ValueError("unexpected tunnels")
        for tunnel in sample["tunnels"].values():
            if not isinstance(tunnel, dict) or set(tunnel) != {"sas", "complete", "reason"}:
                raise ValueError("invalid tunnel")
            if type(tunnel["complete"]) is not bool or tunnel["reason"] not in REASONS:
                raise ValueError("invalid completeness")
            if tunnel["complete"] != (tunnel["reason"] == ""):
                raise ValueError("inconsistent completeness")
            if not isinstance(tunnel["sas"], dict) or len(tunnel["sas"]) > 256:
                raise ValueError("invalid SAs")
            for identity, sa in tunnel["sas"].items():
                if (
                    not re.fullmatch(r"[0-9a-f]{64}", identity)
                    or not isinstance(sa, dict)
                    or set(sa) != {*COUNTERS, "window", "spi", "interface"}
                ):
                    raise ValueError("invalid SA")
                if not all(_natural(sa[key]) for key in (*COUNTERS, "window", "spi")):
                    raise ValueError("invalid SA counters")
                if sa["interface"] is not None and sa["interface"] not in sample["links"]:
                    raise ValueError("missing interface")
    return value


def collect(command: list[str], expected: dict[str, int | None]) -> dict[str, Any]:
    try:
        output = _command(
            [*command, "sudo -n /usr/bin/python3 -B -"],
            time.monotonic() + GATEWAY_TIMEOUT,
            input_text=remote_script(expected),
            timeout=GATEWAY_TIMEOUT,
        )
        return validate_observation(json.loads(output), expected)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
        RecursionError,
        subprocess.TimeoutExpired,
    ):
        return {"schema": SCHEMA, "samples": [None, None]}


def collect_gateways(jobs: dict[str, tuple[list[str], dict[str, int | None]]]) -> dict[str, Any]:
    def run(host: str) -> tuple[str, Any]:
        command, expected = jobs[host]
        return host, collect(command, expected)

    with ThreadPoolExecutor(max_workers=4) as executor:
        return dict(executor.map(run, jobs))


def _delta(before: dict[str, Any], after: dict[str, Any], key: str) -> int | None:
    if after[key] < before[key]:
        return None
    return int(after[key] - before[key])


def summarize(
    observation: dict[str, Any], name: str, expected: int | None, *, cold_standby: bool = False
) -> dict[str, Any]:
    """Derive health from exact stable identities, never cumulative historical totals."""
    first, second = observation["samples"]
    result: dict[str, Any] = {"window": "Unknown", "health": "Unavailable", "details": []}
    if second is None:
        return result
    current = second["tunnels"][name]
    windows = {sa["window"] for sa in current["sas"].values()}
    result["window"] = (
        str(next(iter(windows))) if len(windows) == 1 else "Mixed" if windows else "—"
    )
    warnings = []
    if 0 in windows:
        warnings.append("Replay protection disabled")
    if expected is not None and windows and windows != {expected}:
        warnings.append(f"Expected {expected}; not active")
    if first is None or first["boot"] != second["boot"]:
        result["health"] = "; ".join(
            [*warnings, "Sample incomplete" if first is None else "Gateway restarted"]
        )
        return result
    previous = first["tunnels"][name]
    interval = (second["started"] + second["finished"] - first["started"] - first["finished"]) / 2
    complete = (
        previous["complete"]
        and current["complete"]
        and set(previous["sas"]) == set(current["sas"])
        and second["started"] >= first["finished"] + OBSERVATION_SECONDS - 0.1
        and len(windows) <= 1
    )
    details = result["details"]
    details.append(f"Sample interval: {interval:.2f}s (source reads are sequential)")
    totals = dict.fromkeys(COUNTERS, 0)
    for identity in sorted(set(previous["sas"]) & set(current["sas"])):
        before = previous["sas"][identity]
        after = current["sas"][identity]
        if before["window"] != after["window"] or before["interface"] != after["interface"]:
            complete = False
        for key in COUNTERS:
            delta = _delta(before, after, key)
            if delta is None:
                complete = False
            else:
                totals[key] += delta
            details.append(
                f"Inbound SA 0x{after['spi']:08x} {key}: {before[key]} → {after[key]} "
                + (f"(+{delta})" if delta is not None else "(reset)")
            )
    interfaces = {sa["interface"] for sa in current["sas"].values() if sa["interface"] is not None}
    interface_drops = False
    for index in sorted(interfaces):
        before = first["links"].get(index)
        after = second["links"].get(index)
        if (
            not before
            or not after
            or (before["name"], before["if_id"]) != (after["name"], after["if_id"])
        ):
            complete = False
            continue
        for key in ("errors", "dropped"):
            delta = _delta(before, after, key)
            if delta is None:
                complete = False
            elif delta > 0:
                interface_drops = True
            details.append(
                f"Interface {after['name']} RX {key}: {before[key]} → {after[key]} "
                + (f"(+{delta})" if delta is not None else "(reset)")
            )
    before_seq, after_seq = first["global_seq"], second["global_seq"]
    if before_seq is None or after_seq is None or after_seq < before_seq:
        complete = False
    else:
        details.append(
            f"Gateway-wide XfrmInStateSeqError: {before_seq} → {after_seq} (+{after_seq - before_seq})"
        )
    reasons = [
        f"{totals[key]} {label}"
        for key, label in (
            ("late", "late drops"),
            ("duplicate", "duplicate drops"),
            ("integrity", "integrity failures"),
        )
        if totals[key]
    ]
    if interface_drops and not reasons:
        reasons.append("Interface RX errors/drops increased")
    if not complete:
        changed = set(previous["sas"]) != set(current["sas"])
        if "ambiguous" in {previous["reason"], current["reason"]}:
            reasons.append("sample incomplete (ambiguous mapping)")
        else:
            reasons.append("sample incomplete (SAs changed)" if changed else "sample incomplete")
    elif not current["sas"]:
        reasons.append("Cold standby (no inbound SA)" if cold_standby else "No inbound SA")
    elif not reasons:
        reasons.append("No new drops" if totals["packets"] else "Idle")
    result["health"] = "; ".join([*warnings, *reasons])
    return result
