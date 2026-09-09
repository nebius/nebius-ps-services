"""Bind only native check login hostnames from a verified upstream source tree."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

_LOGIN_SCRIPTS = {
    "create-user-nebius": "scripts/create-user.sh",
    "ssh-check": "scripts/ssh-check.sh",
}
_DEFAULT_LOGIN = "login-0.soperator-login-headless-svc.soperator.svc.cluster.local"


def native_login_commands(source_dir: Path, cluster_name: str) -> dict[str, list[str]]:
    """Derive executable bytes; the caller owns verification of the source tree."""
    chart = source_dir / "helm/soperator-activechecks"
    defaults = yaml.safe_load((chart / "values.yaml").read_text())
    checks = defaults.get("checks") if isinstance(defaults, Mapping) else None
    if not isinstance(checks, Mapping):
        raise ValueError("Native login binding requires the upstream checks contract")
    commands = {}
    for name, filename in _LOGIN_SCRIPTS.items():
        if name not in checks:
            continue
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", cluster_name):
            raise ValueError("Native login binding requires a valid cluster name")
        spec = checks[name].get("k8sJobSpec", {})
        if spec.get("scriptFile") != filename or spec.get("jobContainer", {}).get("command"):
            raise ValueError("Native login script contract changed")
        path = (chart / filename).resolve()
        if not path.is_relative_to(chart.resolve()):
            raise ValueError("Native login script escapes verified chart")
        script = path.read_text()
        if script.count(_DEFAULT_LOGIN) != 1 or "{{" in script:
            raise ValueError("Native login script hostname contract changed")
        host = f"login-0.{cluster_name}-login-headless-svc.soperator.svc.cluster.local"
        commands[name] = ["bash", "-c", script.replace(_DEFAULT_LOGIN, host)]
    return commands


def bind_checks_login(values: Mapping[str, Any], source_dir: Path) -> dict[str, Any]:
    """Materialize exact native commands without admitting user script overrides."""
    result = copy.deepcopy(dict(values))
    active = result.get("soperatorActiveChecks", {})
    if active.get("enabled") is not True:
        return result
    cluster = result["slurmCluster"]["overrideValues"]["clusterName"]
    if active.get("overrideValues") is None:
        active["overrideValues"] = {}
    overrides = active["overrideValues"]
    if not isinstance(overrides, dict):
        raise ValueError("Native login binding requires mapping overrides")
    if overrides.get("slurmClusterRefName", cluster) != cluster:
        raise ValueError("Native login binding conflicts with the checks cluster reference")
    overrides.setdefault("slurmClusterRefName", cluster)
    for name, command in native_login_commands(source_dir, cluster).items():
        check = overrides.setdefault("checks", {}).setdefault(name, {})
        if check.get("enabled") is False:
            continue
        spec = check.setdefault("k8sJobSpec", {})
        if any(key in spec for key in ("script", "scriptRefName", "pythonScript")):
            raise ValueError("Native login binding cannot replace a custom script")
        if "scriptFile" in spec and spec["scriptFile"] != _LOGIN_SCRIPTS[name]:
            raise ValueError("Native login binding cannot replace a custom script file")
        container = spec.setdefault("jobContainer", {})
        if container.get("command") not in (None, command) or container.get("args"):
            raise ValueError("Native login binding cannot replace a custom command")
        container["command"] = command
    return result
