"""Single-submit, bounded ordinary tunnel restart, also streamed by the CLI."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

try:
    import _vpngw_ipsec_observation as observation  # type: ignore[import-not-found]
    import _vpngw_ordinary_operations as ops  # type: ignore[import-not-found]
    from _vpngw_tunnel_iterator import iter_active_tunnels  # type: ignore[import-not-found]
except ModuleNotFoundError:
    from . import ipsec_observation as observation
    from . import ordinary_operations as ops
    from .agent.tunnel_iterator import iter_active_tunnels

CONFIG = Path("/etc/nebius-vpngw/config-resolved.yaml")
REQUEST_SCHEMA = "nebius-vpngw/tunnel-restart-request-v1"
RESULT_SCHEMA = "nebius-vpngw/tunnel-restart-result-v1"


def targets(config: dict[str, Any]) -> list[dict[str, Any]]:
    if (
        not isinstance(config, dict)
        or config.get("vm_ha")
        or ((config.get("gateway_group") or {}).get("vm_ha", {}).get("enabled"))
    ):
        raise ValueError("ordinary topology required")
    result = []
    for index, _interface, connection, tunnel in iter_active_tunnels(config):
        name = tunnel.get("name") or f"tunnel{index}"
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
            raise ValueError("invalid restart target")
        mode = (
            tunnel.get("routing_mode")
            or connection.get("routing_mode")
            or (config.get("defaults", {}).get("routing", {}).get("mode", "bgp"))
        )
        peer = None
        if mode == "bgp" and (connection.get("bgp") or {}).get("enabled") is not False:
            peer = tunnel.get("inner_remote_ip")
            if not isinstance(peer, str):
                raise ValueError("configured BGP peer is missing")
            peer = str(ipaddress.ip_address(peer))
        result.append({"name": name, "child": name, "if_id": index + 100, "peer": peer})
    if len({target["name"] for target in result}) != len(result):
        raise ValueError("ambiguous restart targets")
    return result


def budget(selected: list[dict[str, Any]]) -> int:
    return 5 + 127 * len(selected) + 31 * len({t["peer"] for t in selected if t["peer"]})


def request(config: dict[str, Any], selector: str) -> dict[str, Any]:
    selected = [
        target
        for target in targets(config)
        if selector.lower() == "all" or target["name"] == selector
    ]
    if not selected:
        raise ValueError("no requested restart targets")
    asn = config.get("gateway", {}).get("local_asn")
    if any(target["peer"] for target in selected) and (
        type(asn) is not int or not 1 <= asn <= 4294967295
    ):
        raise ValueError("configured BGP ASN is missing")
    return {
        "schema": REQUEST_SCHEMA,
        "config_digest": ops.digest(config),
        "selector": selector,
        "targets": selected,
        "local_asn": asn,
        "budget": budget(selected),
    }


def installed_config() -> dict[str, Any]:
    fd = os.open(CONFIG, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 1024 * 1024:
            raise ValueError("unsafe installed configuration")
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("installed configuration too large")
    value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise ValueError("invalid installed configuration")
    return value


class Restart:
    def __init__(
        self, deadline: float, *, runner: Any = None, clock: Any = None, sleep: Any = None
    ):
        self.deadline = deadline
        self.runner = runner or ops.bounded
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.restoring = False
        self.cancelled = False

    def end(self, seconds: float) -> float:
        return min(self.deadline, self.clock() + seconds)

    def run(self, args: list[str], end: float, limit: float) -> Any:
        if self.cancelled and not self.restoring:
            raise InterruptedError("restart cancelled")
        remaining = min(end, self.deadline) - self.clock()
        if remaining <= 0:
            raise TimeoutError("restart deadline")
        return self.runner(args, timeout=min(limit, remaining))

    def pause(self, seconds: float, end: float) -> None:
        if self.cancelled and not self.restoring:
            raise InterruptedError("restart cancelled")
        remaining = min(end, self.deadline) - self.clock()
        if remaining <= 0:
            raise TimeoutError("restart deadline")
        self.sleep(min(seconds, remaining))

    def sas(self, end: float, name: str | None = None) -> str:
        args = ["swanctl", "--list-sas", "--raw"]
        if name is not None:
            args += ["--ike", name]
        value = self.run(args, end, 5)
        if value.returncode:
            raise ValueError("IPsec observation unavailable")
        observation.parse_sas(value.stdout)
        return str(value.stdout)

    def wait(self, target: dict[str, Any], end: float, *, terminated: bool = False) -> bool:
        while self.clock() < min(end, self.deadline):
            try:
                raw = self.sas(end, target["name"])
                if terminated:
                    matched = not any(
                        name == target["name"] for name, _ in observation.parse_sas(raw)
                    )
                else:
                    matched = observation.child_ready(raw, target["name"], target["if_id"])
                if matched:
                    return self.clock() < min(end, self.deadline)
            except InterruptedError:
                raise
            except (OSError, ValueError, TimeoutError, subprocess.TimeoutExpired):
                pass  # Unknown observations cannot establish a postcondition.
            if self.clock() < min(end, self.deadline):
                self.pause(1, end)
        return False

    def validate_targets(self, selected: list[dict[str, Any]]) -> None:
        preflight = self.sas(self.end(5))
        names = {target["name"] for target in selected}
        for name, sa in observation.parse_sas(preflight):
            if name in names:
                children = sa.get("child-sas", {})
                if not isinstance(children, dict) or any(
                    not isinstance(child, dict) or child.get("name") != name
                    for child in children.values()
                ):
                    raise ValueError("restart IKE contains unrelated CHILD")

    def restart_ipsec(self, target: dict[str, Any]) -> bool:
        name = target["name"]
        if self.run(["swanctl", "--load-all"], self.end(15), 15).returncode:
            return False
        termination = self.end(20)
        try:
            self.run(["swanctl", "--terminate", "--ike", name, "--timeout", "15"], termination, 15)
        except (TimeoutError, subprocess.TimeoutExpired):
            pass
        if not self.wait(target, termination, terminated=True):
            return False
        self.pause(2, self.end(2))
        try:
            # Native timeout detaches; it never authorizes a second submission.
            self.run(
                ["swanctl", "--initiate", "--child", name, "--timeout", "20"], self.end(25), 25
            )
        except (TimeoutError, subprocess.TimeoutExpired):
            pass
        return self.wait(target, self.end(60))

    def reset_peer(self, peer: str, asn: int) -> bool:
        if self.deadline - self.clock() < 31:
            return False
        end = self.end(31)
        restore = None
        shutdown = None
        command = ["vtysh", "-c", "configure terminal", "-c", f"router bgp {asn}", "-c"]
        try:
            # Reserve 15 seconds for unshutdown even if shutdown fails or is cancelled.
            shutdown = self.run(command + [f"neighbor {peer} shutdown"], end - 16, 15)
            self.pause(1, end - 15)
        finally:
            self.restoring = True
            try:
                restore = self.run(command + [f"no neighbor {peer} shutdown"], end, 15)
            finally:
                self.restoring = False
        return shutdown is not None and shutdown.returncode == 0 and restore.returncode == 0

    def cancelled_signal(self, _signum: int, _frame: Any) -> None:
        self.cancelled = True
        if not self.restoring:
            raise InterruptedError("restart interrupted; remote state unverified")


def execute(value: dict[str, Any], *, operation: Restart | None = None) -> dict[str, Any]:
    completed: list[str] = []
    reset: list[str] = []
    result = {
        "schema": RESULT_SCHEMA,
        "status": "incomplete",
        "completed_tunnels": completed,
        "reset_peers": reset,
    }
    try:
        if (
            not isinstance(value, dict)
            or type(value.get("budget")) is not int
            or not 0 < value["budget"] <= 86400
        ):
            raise ValueError("invalid restart budget")
        operation = operation or Restart(time.monotonic() + value["budget"])
        with ops.mutation_lock():
            config = installed_config()
            if request(config, value["selector"]) != value or not shutil.which("swanctl"):
                raise ValueError("restart admission changed")
            selected = value["targets"]
            operation.validate_targets(selected)
            for target in selected:
                if not operation.restart_ipsec(target):
                    return result
                completed.append(target["name"])
            for peer in sorted({target["peer"] for target in selected if target["peer"]}):
                if not operation.reset_peer(peer, value["local_asn"]):
                    return result
                reset.append(peer)
            for target in selected:
                if not operation.wait(target, operation.end(5)):
                    return result
            if operation.cancelled or operation.clock() >= operation.deadline:
                return result
            result["status"] = "complete"
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.TimeoutExpired):
        pass
    return result


def main(value: dict[str, Any]) -> int:
    if (
        not isinstance(value, dict)
        or type(value.get("budget")) is not int
        or not 0 < value["budget"] <= 86400
    ):
        print(
            json.dumps(
                {
                    "schema": RESULT_SCHEMA,
                    "status": "incomplete",
                    "completed_tunnels": [],
                    "reset_peers": [],
                }
            )
        )
        return 1
    operation = Restart(time.monotonic() + value["budget"])
    previous = {}
    try:
        for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            previous[signum] = signal.signal(signum, operation.cancelled_signal)
        result = execute(value, operation=operation)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "complete" else 1


def streamed_source() -> str:
    import base64

    root = Path(__file__).parent
    result = ops.streamed_bootstrap() + "\nimport base64,sys,types\n"
    for name, relative in (
        ("_vpngw_ipsec_observation", "ipsec_observation.py"),
        ("_vpngw_tunnel_iterator", "agent/tunnel_iterator.py"),
        ("_vpngw_tunnel_restart", "tunnel_restart.py"),
    ):
        encoded = base64.b64encode((root / relative).read_bytes()).decode()
        result += (
            f"_module=types.ModuleType({name!r});sys.modules[{name!r}]=_module;"
            f"exec(compile(base64.b64decode({encoded!r}),'<tunnel-restart>','exec'),_module.__dict__)\n"
        )
    return result
