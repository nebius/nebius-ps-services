"""Bounded SSH transport and independently owned Slurm job operations."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

from run_labs_common import canonical


def ssh_argv(env):
    ssh = env["ssh"]
    target = ssh["target"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.:%\[\]-]+", target):
        raise ValueError("Explicit user@host is required in the SSH receipt")
    port = int(ssh.get("port", 22))
    if not 0 < port < 65536:
        raise ValueError("Invalid SSH port")
    argv = [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=2",
        "-p",
        str(port),
    ]
    if ssh.get("identity_file"):
        argv += ["-i", ssh["identity_file"]]
    return argv + [target]


def request(state, unit, stage, action):
    if not unit.get("remote_root"):
        raise ValueError("Prepare and bind the isolated remote workspace first")
    env = state["environment"]
    name = (
        "rl-" + canonical([state["id"], unit["key"], unit["profile"], stage["id"]])[:24]
    )
    body = {
        "action": action,
        "root": unit["remote_root"],
        "campaign": state["id"],
        "source_sha256": state["plan"]["source_sha256"],
        "name": name,
    }
    if action == "cleanup":
        body["jobs"] = [s["dispatch"] for s in unit["stages"] if s.get("dispatch")]
    elif action == "submit":
        body.update(
            argv=stage["argv"],
            environment={**env.get("variables", {}), **stage.get("environment", {})},
        )
        if any("$" in a for a in stage["argv"]):
            raise ValueError("Unresolved prerequisite in frozen stage")
    elif action != "reconcile":
        body["job"] = stage["dispatch"]["job"]
    script = (Path(__file__).parent / "remote_job.py").read_text()
    command = shlex.join(["python3", "-c", script])
    # Only observational requests receive bounded automatic transport retries.
    attempts = 3 if action == "query" else 1
    for attempt in range(attempts):
        try:
            proc = subprocess.run(
                [*ssh_argv(env), command],
                input=json.dumps(body),
                text=True,
                capture_output=True,
                check=False,
                timeout=60,
            )
            if proc.returncode:
                raise ValueError(
                    "Remote operation failed; inspect the private remote receipt before retry"
                )
            result = json.loads(proc.stdout)
            if result.get("error"):
                raise ValueError(result["message"])
            return result
        except (subprocess.TimeoutExpired, ConnectionError):
            if attempt == attempts - 1:
                raise ValueError(
                    "SSH connection failed within the retry bound"
                ) from None
    raise AssertionError("Unreachable")


def cancel_owned(state):
    for unit in state["plan"]["units"]:
        for stage in unit["stages"]:
            if stage.get("intent") and not stage.get("dispatch"):
                receipt = request(state, unit, stage, "reconcile")
                if receipt.get("not_dispatched"):
                    continue
                stage["dispatch"] = receipt
            if stage.get("dispatch") and stage.get("status") != "complete":
                receipt = request(state, unit, stage, "cancel")
                if receipt.get("cancel_requested"):
                    raise ValueError(
                        "Cancellation requested; wait for terminal Slurm state and repeat cancel"
                    )
