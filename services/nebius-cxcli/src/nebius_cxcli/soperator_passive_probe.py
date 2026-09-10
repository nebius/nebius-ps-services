"""Worker-side adapter for observing unmodified native diagnostics.

This module uses only the standard library: its source is executed in slurmd.
It never substitutes its own health checks for the upstream runner.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import runpy
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__:
    from .soperator_checks_verdict import health_verdict


def mounted(base: Path, expected: dict) -> tuple[dict, list]:
    hashes = {
        name: hashlib.sha256((base / name).read_bytes().rstrip(b"\n")).hexdigest()
        for name in expected["scripts"]
    }
    return hashes, json.loads((base / "checks.json").read_text())


def running() -> list[str]:
    result = []
    for proc in Path("/proc").glob("[0-9]*"):
        if proc.name == str(os.getpid()):
            continue
        try:
            args = (proc / "cmdline").read_bytes().split(b"\0")
            env = (proc / "environ").read_bytes().split(b"\0")
        except (FileNotFoundError, ProcessLookupError):
            continue
        # Permission errors cannot prove absence. Include descendants even if
        # their original check_runner process has exited.
        if any(Path(os.fsdecode(arg)).name == "check_runner.py" for arg in args if arg) or any(
            item.startswith((b"CHECKS_CONTEXT=", b"CXCLI_PASSIVE_EXECUTION=")) for item in env
        ):
            result.append(proc.name)
    return result


def node_facts(expected: dict) -> dict:
    worker = os.environ["SLURMD_NODENAME"]
    result = subprocess.run(
        ["scontrol", "show", "node", worker, "--json"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    nodes = json.loads(result.stdout)["nodes"]
    if (
        len(nodes) != 1
        or nodes[0].get("name") != worker
        or not nodes[0].get("state")
        or nodes[0].get("real_memory", 0) <= 0
    ):
        raise RuntimeError("authoritative passive node facts are unavailable")
    gpu_count = expected["resources"]["gpus"]
    gpu_names = []
    if gpu_count:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        gpu_names = result.stdout.strip().splitlines()
        if len(gpu_names) != gpu_count:
            raise RuntimeError("passive GPU inventory differs from Slurm")
    node = nodes[0]
    return {
        "state": node["state"],
        "reservation": node.get("reservation", ""),
        "realMemory": node["real_memory"],
        "gpuNames": gpu_names,
    }


def verdicts(output: str, applicable: list) -> list[dict]:
    if not re.search(r"Finished in [0-9.]+ seconds", output) or any(
        marker in output for marker in (": FAIL", "Failed to ", "Unknown error", "exiting")
    ):
        raise RuntimeError("native passive execution did not finish successfully")
    results = []
    for check in applicable:
        if (
            output.count("Running check " + check.name + " (" + check.command + "),") != 1
            or output.count("Check " + check.name + ": OK") != 1
        ):
            raise RuntimeError("native passive PASS evidence is missing or ambiguous")
        results.append({"name": check.name, "command": check.command, "status": "NATIVE_OK"})
    return results


def child_verdict(script: str, output: str) -> dict:
    """Interpret only reviewed native output; native exit zero is not a health verdict."""
    lines = output.splitlines()
    if script == "gpu_health_check.py":
        if not output.startswith("All checks passed\nHealth checker exit code: 0\n"):
            raise RuntimeError("native passive GPU health result is unavailable or failed")
        parts = output.split("\nHealth checker stdout:\n")
        if len(parts) != 2 or parts[1].count("\nHealth checker stderr:\n") != 1:
            raise RuntimeError("native passive GPU health report is missing or ambiguous")
        stdout = parts[1].split("\nHealth checker stderr:\n")[0]
        # Keep the same enabled-test/subcheck rules as native active acceptance.
        # The worker receives this shared validator along with the probe source.
        return health_verdict(
            "Health checker output:\n" + stdout + "\nHealth checker status: PASS\n"
        )
    elif script == "boot_disk_full.sh":
        usage = re.findall(r"(?m)^Node boot disk is ([0-9]+)% full \(threshold 80%\)$", output)
        if len(usage) != 1 or int(usage[0]) > 80 or "Could not determine boot disk usage" in lines:
            raise RuntimeError("native passive disk measurement is unavailable or failed")
    elif script in {"alloc_mem_used.drain.sh", "alloc_mem_used.undrain.sh"}:
        field = "Job allocated memory" if script.endswith(".drain.sh") else "Node real memory"
        available = re.findall(r"(?m)^System available memory: ([0-9]+)$", output)
        required = re.findall(r"(?m)^" + field + r": ([0-9]+)$", output)
        if (
            len(available) != 1
            or len(required) != 1
            or int(required[0]) <= 0
            or int(available[0]) < int(required[0])
            or lines.count("Enough available memory on the node") != 1
        ):
            raise RuntimeError("native passive memory measurement is unavailable or failed")
    elif script == "nvme_raid_health.sh":
        limitation = "native output cannot prove NVMe discovery and every RAID subcheck succeeded"
        for skip in (
            "No NVMe disks detected, skipping",
            "No NVMe-backed RAID arrays detected, skipping",
        ):
            if skip in lines:
                return {"status": "NATIVE_SKIP", "reason": skip, "limitation": limitation}
        if lines.count("NVMe RAID health check passed") != 1:
            raise RuntimeError("native passive NVMe health result is unavailable or failed")
        return {"status": "NATIVE_OK", "limitation": limitation}
    elif script in {"alloc_gpus_busy.drain.sh", "alloc_gpus_busy.undrain.sh"}:
        limitation = "GPU process query success is not reported"
        if "No GPU devices are requested by user" in lines:
            return {"status": "NATIVE_SKIP", "limitation": limitation}
        # These native scripts swallow query failures. Their output proves
        # completion only; never invent a successful process measurement.
        return {"status": "NATIVE_OK", "limitation": limitation}
    else:
        raise RuntimeError("passive diagnostic output contract is unreviewed")
    return {"status": "PASS"}


def log_snapshot(context: str, worker: str, name: str = "check_runner") -> tuple[dict, str]:
    if any(
        not re.fullmatch(r"[A-Za-z0-9_.-]+", value) or value in {".", ".."}
        for value in (context, worker, name)
    ):
        raise RuntimeError("invalid native passive log identity")
    path = Path("/mnt/jail/opt/soperator-outputs/slurm_scripts") / f"{worker}.{name}.{context}.out"
    limit = 2 * 1024 * 1024
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise RuntimeError("native passive evidence is not a regular file")
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        linked = path.stat()
    except FileNotFoundError:
        return {}, ""
    if len(data) > limit:
        raise RuntimeError("native passive evidence exceeds bounded reader")

    def identity(row: os.stat_result) -> tuple[int, ...]:
        return (row.st_dev, row.st_ino, row.st_mtime_ns, row.st_ctime_ns, row.st_size)

    if identity(before) != identity(after) or identity(after) != identity(linked):
        return {}, ""
    return {
        "inode": before.st_ino,
        "mtime": before.st_mtime_ns,
        "sha256": hashlib.sha256(data).hexdigest(),
    }, data.decode()


def periodic_applicable(scheduler: dict, states: list[str]) -> bool:
    """Apply Slurm's health-check state selector to the observed worker state."""
    selected = set(scheduler.get("HealthCheckNodeState", "ANY").upper().split(","))
    if selected - {
        "ANY",
        "ALLOC",
        "IDLE",
        "MIXED",
        "NONDRAINED_IDLE",
        "CYCLE",
        "START_ONLY",
        "REBOOT_ONLY",
    }:
        raise RuntimeError("unrecognized passive scheduler node-state selector")
    if selected & {"START_ONLY", "REBOOT_ONLY"}:
        if len(selected) != 1:
            raise RuntimeError("invalid passive scheduler startup-only selector")
        return False
    selected.discard("CYCLE")
    if not selected or "ANY" in selected:
        return True
    state = set(states)
    return bool(
        "ALLOC" in selected
        and state & {"ALLOC", "ALLOCATED"}
        or "MIXED" in selected
        and "MIXED" in state
        or "IDLE" in selected
        and "IDLE" in state
        or "NONDRAINED_IDLE" in selected
        and "IDLE" in state
        and not state & {"DRAIN", "DRAINED"}
    )


def probe(expected: dict, mode: str) -> dict:
    base = Path("/opt/slurm_scripts")
    hashes, config = mounted(base, expected)
    worker = os.environ["SLURMD_NODENAME"]
    result: dict[str, Any] = {
        "worker": worker,
        "hashes": hashes,
        "config": config,
        "running": running(),
        "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    }
    if mode == "baseline":
        result["observedAt"] = time.time_ns()
        return result
    if hashes != expected["hashes"] or config != expected["config"] or mode == "observe":
        return result
    facts = node_facts(expected)
    result["facts"] = facts
    if mode == "accept":
        result["periodicApplicable"] = periodic_applicable(
            expected.get("scheduler", {}), facts["state"]
        )
        if not result["periodicApplicable"]:
            if mounted(base, expected) != (hashes, config) or node_facts(expected) != facts:
                raise RuntimeError("passive contract changed during observation")
            return result
    context = expected.get("context", "hc_program")
    snapshot, output = log_snapshot(context, worker)
    if mode != "paused":
        starts = re.findall(r"(?m)^\[([0-9-]+ [0-9:.]+) UTC\] INFO: Started$", output)
        started = (
            int(
                datetime.strptime(starts[0], "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC).timestamp()
                * 1_000_000_000
            )
            if len(starts) == 1
            else 0
        )
        if (
            not snapshot
            or started < expected["baseline"]["observedAt"]
            or result["running"]
            or not re.search(r"Finished in [0-9.]+ seconds", output)
        ):
            result["pending"] = True
            return result
        if (
            context != "hc_program"
            and f'Environment SLURM_JOB_ID="{expected["job"]}"' not in output
        ):
            result["pending"] = True
            return result
    if mode == "paused" and facts["reservation"] != expected["reservation"]:
        raise RuntimeError("passive suppression does not own this worker reservation")
    env = dict(
        os.environ,
        CHECKS_CONTEXT=context,
        CHECKS_CONFIG=str(base / "checks.json"),
        CHECKS_OUTPUTS_BASE_DIR="/opt/soperator-outputs",
        CHECKS_RUNNER_OUTPUT="/dev/stdout",
    )
    if context != "hc_program":
        recorded = dict(
            re.findall(
                r'Environment (SLURM_JOB_ID|SLURM_JOB_GPUS|SLURM_JOB_COMMENT|SLURM_RESTART_COUNT)="([^"\n]*)"',
                output,
            )
        )
        if recorded.get("SLURM_JOB_ID") != expected["job"]:
            raise RuntimeError("native passive evidence belongs to another allocation")
        if recorded.get("SLURM_RESTART_COUNT", "0") != expected["attempt"]:
            raise RuntimeError("native passive evidence belongs to another Slurm attempt")
        result["attempt"] = recorded.get("SLURM_RESTART_COUNT", "0")
        env.update(recorded)
    os.environ.update(env)
    logging.disable(logging.CRITICAL)
    runner = runpy.run_path(str(base / "check_runner.py"), run_name="cxcli_native_observer")
    native_node = runner["get_node_info"]()
    if (
        sorted(native_node.state_flags) != sorted(facts["state"])
        or native_node.reservation != facts["reservation"]
        or native_node.real_memory_bytes != facts["realMemory"] * 1024 * 1024
    ):
        raise RuntimeError("native passive applicability lost its node facts")
    tags = runner["get_platform_tags"]()
    if (
        expected["resources"]["gpus"] and str(expected["resources"]["gpus"]) + "xGPU" not in tags
    ) or (not expected["resources"]["gpus"] and tags != ["CPU"]):
        raise RuntimeError("native passive applicability lost its GPU facts")
    applicable = runner["filter_applicable_checks"]([runner["Check"](**entry) for entry in config])
    diagnostics = [
        check
        for check in applicable
        if any(
            check.name == row["name"] and check.command == row["command"]
            for row in expected["diagnostics"]
        )
    ]
    if mode == "paused":
        if diagnostics:
            raise RuntimeError("diagnostic suppression is ineffective on this worker")
        result["suppressed"] = True
        return result
    observations = verdicts(output, diagnostics)
    child_logs = []
    for check, observation in zip(diagnostics, observations, strict=True):
        if check.log != "slurm_scripts/$worker.$name.$context.out":
            raise RuntimeError("native passive child log path differs from reviewed contract")
        child_snapshot, child_output = log_snapshot(context, worker, check.name)
        if not child_snapshot or not started <= child_snapshot["mtime"] <= snapshot["mtime"]:
            result["pending"] = True
            return result
        script = expected["diagnosticScripts"][check.command]
        role = expected["proofRoles"][script]
        verdict = child_verdict(script, child_output)
        if (
            role == "required-measurement"
            and verdict["status"] != "PASS"
            or role == "supporting-only"
            and verdict["status"] not in {"NATIVE_OK", "NATIVE_SKIP"}
            or role not in {"required-measurement", "supporting-only"}
        ):
            raise RuntimeError("native passive evidence does not satisfy its frozen proof role")
        observation.update(verdict, script=script, proofRole=role)
        observation["log"] = child_snapshot
        child_logs.append((check.name, child_snapshot))
    if (
        log_snapshot(context, worker)[0] != snapshot
        or any(log_snapshot(context, worker, name)[0] != saved for name, saved in child_logs)
        or running()
    ):
        result["pending"] = True
        return result
    result.update(
        verdicts=observations,
        coverage={
            "requiredMeasurements": [
                row["script"] for row in observations if row["proofRole"] == "required-measurement"
            ],
            "supportingOnly": [
                row["script"] for row in observations if row["proofRole"] == "supporting-only"
            ],
        },
        notApplicable=[
            row
            for row in expected["diagnostics"]
            if not any(
                check.name == row["name"] and check.command == row["command"]
                for check in diagnostics
            )
        ],
        log=snapshot,
    )
    if mounted(base, expected) != (hashes, config) or node_facts(expected) != facts:
        raise RuntimeError("passive contract changed during observation")
    return result


if __name__ == "__main__":
    print(json.dumps(probe(json.loads(sys.argv[1]), sys.argv[2])))
