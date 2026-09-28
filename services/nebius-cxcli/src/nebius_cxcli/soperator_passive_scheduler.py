"""Freeze and compare the effective Slurm passive scheduler and job hooks."""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from typing import Any

SCALARS = ("HealthCheckInterval", "HealthCheckProgram", "HealthCheckNodeState")
HOOKS = ("Prolog", "Epilog")
FIELDS = (*SCALARS, *HOOKS)


def _states(value: str) -> str:
    states = value.upper().split(",")
    if not states or any(not re.fullmatch(r"[A-Z_]+", state) for state in states):
        raise ValueError("passive scheduler has invalid health-check node states")
    return ",".join(sorted(set(states)))


def _interval(value: object) -> str:
    text = str(value)
    if isinstance(value, bool) or not text.isdecimal():
        raise ValueError("passive scheduler has an invalid health-check interval")
    return str(int(text))


def _hook(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or any(character in value for character in "*?[]\n\r")
    ):
        raise ValueError("passive hook verification requires an exact absolute executable path")
    return value


def scheduler_from_cluster_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the frozen operator's structured, health, then custom config order."""
    desired: dict[str, Any] = {
        "HealthCheckInterval": "0",
        "HealthCheckProgram": "(null)",
        "HealthCheckNodeState": "ANY",
        "Prolog": [],
        "Epilog": [],
    }
    config = spec.get("slurmConfig") or {}
    if not isinstance(config, Mapping):
        raise ValueError("rendered Slurm configuration must be a mapping")
    for name in HOOKS:
        value = config.get(name.lower())
        if value:
            desired[name].append(_hook(value))
    health = spec.get("healthCheckConfig")
    if health is not None:
        if not isinstance(health, Mapping):
            raise ValueError("rendered passive scheduler must be a mapping")
        program = health.get("healthCheckProgram")
        if not isinstance(program, str) or not program:
            raise ValueError("rendered passive scheduler has no health-check program")
        desired.update(
            HealthCheckInterval=_interval(health.get("healthCheckInterval")),
            HealthCheckProgram=program,
            HealthCheckNodeState=_states(
                ",".join(row["state"] for row in health.get("healthCheckNodeState", []))
            ),
        )
    custom = spec.get("customSlurmConfig") or ""
    if not isinstance(custom, str):
        raise ValueError("custom Slurm configuration must be text")
    names = {name.lower(): name for name in FIELDS}
    for line in custom.splitlines():
        tokens = shlex.split(line, comments=True)
        if not tokens:
            continue
        if re.match(r"(?i)^include(?:\s|=|$)", tokens[0]):
            raise ValueError("cannot freeze passive scheduler with an unresolved custom Include")
        if line.rstrip().endswith("\\"):
            raise ValueError("cannot freeze multiline custom Slurm directives")
        key, separator, raw = line.partition("=")
        directive = names.get(key.strip().lower())
        if directive is None:
            if any(re.search(r"(?i)\b" + field + r"\s*=", " ".join(tokens)) for field in FIELDS):
                raise ValueError("ambiguous custom passive scheduler directive")
            continue
        values = shlex.split(raw, comments=True)
        if not separator or len(values) != 1:
            raise ValueError("ambiguous custom passive scheduler value")
        value = values[0]
        if directive in HOOKS:
            desired[directive].append(_hook(value))
        elif directive == "HealthCheckInterval":
            desired[directive] = _interval(value)
        elif directive == "HealthCheckNodeState":
            desired[directive] = _states(value)
        else:
            desired[directive] = value
    return desired


def verify_scheduler(output: str, desired: Mapping[str, Any]) -> None:
    """Read native indexed hooks without losing extra entries or ambiguous rows."""
    if set(desired) != set(FIELDS):
        raise RuntimeError("frozen passive scheduler contract is incomplete")
    scalars: dict[str, str] = {}
    hooks: dict[str, dict[int, str]] = {name: {} for name in HOOKS}
    for line in output.splitlines():
        match = re.fullmatch(
            r"\s*(HealthCheckInterval|HealthCheckProgram|HealthCheckNodeState|Prolog|Epilog)"
            r"(?:\[(\d+)\])?\s*=\s*(.*?)\s*",
            line,
        )
        if match is None:
            continue
        name, index, value = match.groups()
        if name in SCALARS:
            if index is not None or name in scalars:
                raise RuntimeError("ambiguous live passive scheduler configuration")
            scalars[name] = value
        elif index is None:
            raise RuntimeError("ambiguous live passive hook configuration")
        else:
            number = int(index)
            if number in hooks[name] or index != str(number):
                raise RuntimeError("ambiguous live passive hook configuration")
            hooks[name][number] = value
    if set(scalars) != set(SCALARS):
        raise RuntimeError("live passive scheduler configuration is incomplete")
    try:
        interval = re.fullmatch(r"([0-9]+) sec", scalars["HealthCheckInterval"])
        if interval is None:
            raise ValueError("native health-check interval has an invalid unit")
        observed: dict[str, Any] = {
            **scalars,
            "HealthCheckInterval": _interval(interval[1]),
            "HealthCheckNodeState": _states(scalars["HealthCheckNodeState"]),
        }
    except ValueError as exc:
        raise RuntimeError("live passive scheduler configuration is invalid") from exc
    for name in HOOKS:
        indexed = hooks[name]
        if set(indexed) != set(range(len(indexed))):
            raise RuntimeError("live passive hook indices are incomplete")
        observed[name] = [indexed[index] for index in range(len(indexed))]
    if observed != dict(desired):
        raise RuntimeError("desired passive scheduler or operational hook wiring has not converged")
