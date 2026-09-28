"""Explicit target selection at Kubernetes CLI process boundaries.

Lifecycle callers own cluster-ID/UID admission. This boundary ensures their
context cannot be lost or replaced by the workstation's current-context.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

TARGET_CONTEXT_ENV = "NEBIUS_CXCLI_TARGET_KUBE_CONTEXT"


def _true_flag(args: Sequence[str], *flags: str) -> bool:
    values = [
        "true" if arg == flag else arg.split("=", 1)[1].lower()
        for arg in args
        for flag in flags
        if arg == flag or arg.startswith(flag + "=")
    ]
    return bool(values) and all(value == "true" for value in values)


def _flag_values(args: Sequence[str], flag: str) -> list[str]:
    values = []
    for index, arg in enumerate(args):
        if arg == flag:
            value = args[index + 1] if index + 1 < len(args) else ""
            if not value.strip() or value.startswith("-"):
                raise ValueError("Kubernetes target selector must not be empty")
            values.append(value)
        elif arg.startswith(flag + "="):
            value = arg.split("=", 1)[1]
            if not value.strip():
                raise ValueError("Kubernetes target selector must not be empty")
            values.append(value)
    return values


def _client_only(tool: str, args: Sequence[str]) -> bool:
    if not args or _true_flag(args, "--help", "-h"):
        return True
    verb = args[0]
    if tool == "kubectl":
        return verb in {"help", "completion", "kustomize", "config"} or (
            verb == "version" and _true_flag(args, "--client")
        )
    if tool == "helm":
        return verb in {
            "help",
            "completion",
            "version",
            "env",
            "show",
            "pull",
            "push",
            "package",
            "lint",
            "repo",
            "registry",
            "search",
            "dependency",
        } or (
            verb == "template"
            and not any(value == "--validate" or value.startswith("--validate=") for value in args)
            and not any(
                value == "--dry-run=server"
                or (value == "--dry-run" and args[index + 1 : index + 2] == ["server"])
                for index, value in enumerate(args)
            )
        )
    return (
        verb in {"help", "completion"}
        or (verb == "version" and _true_flag(args, "--client"))
        or (verb in {"install", "create"} and _true_flag(args, "--export"))
    )


def target_command(args: Sequence[str], *, env: Mapping[str, str] | None = None) -> list[str]:
    """Bind live Kubernetes commands; never select kubeconfig current-context.

    An explicit context can use the normal kubeconfig search path: Kubernetes
    rejects a missing named context instead of choosing the current context.
    Generated handoffs supply both their context and isolated KUBECONFIG.
    """
    command = [os.fspath(value) for value in args]
    if not command:
        return command
    tool = Path(command[0]).name
    if tool not in {"kubectl", "helm", "flux"}:
        return command
    options = command[1 : command.index("--") if "--" in command else len(command)]
    if _client_only(tool, options):
        return command
    if (
        any(
            arg == flag or arg.startswith(flag + "=")
            for arg in options
            for flag in ("--cluster", "--server", "-s", "--kube-apiserver")
        )
        or (
            tool == "kubectl"
            and any(arg.startswith("-s") and not arg.startswith("--") for arg in options)
        )
        or (tool == "helm" and (os.environ if env is None else env).get("HELM_KUBEAPISERVER"))
    ):
        raise ValueError("Kubernetes cluster/server overrides cannot replace the selected target")
    context_flag = "--kube-context" if tool == "helm" else "--context"
    contexts = _flag_values(options, context_flag)
    target = str((env or {}).get(TARGET_CONTEXT_ENV) or "").strip()
    if target:
        contexts.append(target)
    if not contexts:
        raise ValueError(
            "Kubernetes connection requires an explicit target context or a selected "
            "cluster handoff; refusing to use kubeconfig current-context"
        )
    if len(set(contexts)) != 1:
        raise ValueError("Kubernetes command context conflicts with the selected target")
    configs = _flag_values(options, "--kubeconfig")
    selected_config = str((env or {}).get("KUBECONFIG") or "").strip()
    if len(set(configs)) > 1 or (configs and selected_config and configs[0] != selected_config):
        raise ValueError("Kubernetes command kubeconfig conflicts with the selected target")
    if not _flag_values(options, context_flag):
        command[1:1] = [context_flag, contexts[0]]
    return command


def run(args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """Run a command after binding any Kubernetes connection to its target."""
    from .deployment_timing import timed_phase
    from .owned_process import run as owned_run

    with timed_phase("kubernetes-subprocess"):
        return owned_run(target_command(args, env=kwargs.get("env")), **kwargs)


def popen(args: Sequence[str], **kwargs: Any) -> subprocess.Popen:
    """Start a streaming command with the same target checks as synchronous runs."""
    from .owned_process import popen as owned_popen

    return owned_popen(target_command(args, env=kwargs.get("env")), **kwargs)
