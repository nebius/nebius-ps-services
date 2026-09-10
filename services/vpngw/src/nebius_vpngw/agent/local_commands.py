"""Opt-in bounded command accounting for synchronous ordinary reconciliation.

HA callers retain their existing command policy. The ordinary owner checks the
whole ledger even when a historical renderer catches an individual exception.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import tempfile
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from .. import ordinary_operations as operations
from ..ordinary_operations import Operation


@dataclass
class CommandBudget:
    deadline: float
    failures: list[str] = field(default_factory=list)
    operation: Operation | None = None
    effect_deadline: float | None = None

    def require_success(self) -> None:
        if self.failures or time.monotonic() >= self.deadline:
            raise RuntimeError("ordinary local command failed")


CURRENT: ContextVar[CommandBudget | None] = ContextVar("ordinary_command_budget", default=None)


def _bounded_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    """Join every child writer before returning to the routing-lock owner."""
    timeout = kwargs.pop("timeout")
    check = kwargs.pop("check", False)
    input_value = kwargs.pop("input", None)
    capture = kwargs.pop("capture_output", False)
    text_mode = kwargs.get("text", False) or kwargs.get("universal_newlines", False)
    with contextlib.ExitStack() as stack:
        streams = {}
        for name in ("stdout", "stderr"):
            if capture or kwargs.get(name) == subprocess.PIPE:
                stream = stack.enter_context(tempfile.TemporaryFile())
                streams[name] = stream
                kwargs[name] = stream
        if input_value is not None:
            kwargs["stdin"] = subprocess.PIPE
        budget = CURRENT.get()
        operation = budget.operation if budget else None
        index = operation.intent("process") if operation else None
        process = subprocess.Popen(args, start_new_session=True, **kwargs)
        try:
            if operation is not None and index is not None:
                operation.process_started(index, process.pid)
            process.communicate(input=input_value, timeout=timeout)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            settle_deadline = (
                min(budget.deadline, time.monotonic() + 5) if budget else time.monotonic() + 5
            )
            process.wait(timeout=max(0, settle_deadline - time.monotonic()))
            if operation is not None and index is not None:
                evidence = operation.check()["effects"][index].get("process")
                if evidence is None:
                    raise RuntimeError("ordinary command identity unavailable")
                # Killing and reaping the leader is not proof that descendants
                # have stopped writing. Preserve accepted evidence on uncertainty.
                while not operations.process_group_empty(evidence):
                    remaining = settle_deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError("ordinary command group unresolved")
                    time.sleep(min(0.05, remaining))
                operation.update(index, state="settled")
        output = {}
        for name, stream in streams.items():
            stream.seek(0)
            raw = stream.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise RuntimeError("ordinary command output limit")
            output[name] = (
                raw.decode(kwargs.get("encoding") or "utf-8", kwargs.get("errors") or "strict")
                if text_mode
                else raw
            )
        result = subprocess.CompletedProcess(
            args, process.returncode, output.get("stdout"), output.get("stderr")
        )
        if check:
            result.check_returncode()
        return result


def run(args: Any, **kwargs: Any) -> Any:
    budget = CURRENT.get()
    if budget is None:
        return subprocess.run(args, **kwargs)
    remaining = (budget.effect_deadline or budget.deadline) - time.monotonic()
    if remaining <= 0:
        budget.failures.append("deadline")
        raise TimeoutError("ordinary reconciliation deadline exceeded")
    kwargs["timeout"] = min(float(kwargs.get("timeout") or 30), remaining)
    try:
        operation = budget.operation
        if (
            operation is not None
            and isinstance(args, list)
            and args[:1] == ["systemctl"]
            and args[1:2] != ["is-active"]
        ):
            if len(args) != 3 or args[1] not in {
                "start",
                "stop",
                "restart",
                "reload",
                "reload-or-restart",
            }:
                raise RuntimeError("ordinary service command is outside admitted scope")
            name = args[2] if args[2].endswith(".service") else args[2] + ".service"
            # Required FRR updates have a deterministic restart when reload is
            # unavailable; select before submission, never retry a lost reply.
            action = args[1]
            if action == "reload-or-restart":
                unit = operation.manager.unit(name)
                action = (
                    "reload"
                    if unit.get("ActiveState") == "active" and unit.get("CanReload") is True
                    else "restart"
                )
            operation.service(action, name, timeout=kwargs["timeout"])
            return subprocess.CompletedProcess(args, 0, "", "")
        network = (
            operation.before_netplan()
            if operation is not None and args == ["netplan", "apply"]
            else None
        )
        result = _bounded_run(args, **kwargs)
        if operation is not None and network is not None and result.returncode == 0:
            operation.update(network, state="accepted")
            operation.after_netplan(network)
    except Exception:
        budget.failures.append("command")
        raise
    # iproute2's existing-object response is followed by exact observation or
    # replacement. Other unsuccessful commands remain failures even if caught.
    existing = (
        isinstance(args, list)
        and args[:3] in (["ip", "link", "add"], ["ip", "neigh", "add"])
        and "File exists" in str(result.stderr)
    )
    if result.returncode and not existing and args != ["swanctl", "--load-all"]:
        budget.failures.append("command")
    return result
