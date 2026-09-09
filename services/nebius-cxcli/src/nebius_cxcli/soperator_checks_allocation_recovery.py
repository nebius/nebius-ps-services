"""Retain terminal, unpinned submissions before fresh allocation-bound checks."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .soperator_checks_allocation import SCRIPT_ANNOTATION
from .soperator_checks_contract import job_execution_digest, verify_native_template
from .soperator_checks_policy import CheckRule

if TYPE_CHECKING:
    from .soperator_checks import SoperatorChecksExecution


def retire_unpinned_jobs(
    runner: SoperatorChecksExecution,
    *,
    rule: CheckRule,
    cron: Mapping[str, Any],
    epoch: Mapping[str, Any],
    reservation: str,
    workers: tuple[str, ...],
) -> None:
    """Never reuse, relabel or execute an obsolete submission as acceptance."""
    if rule.check_type != "slurmJob":
        return
    entries = {k: v for k, v in runner.state["jobs"].items() if v["check"] == rule.name}
    if not entries:
        return
    live_jobs = runner._jobs()
    obsolete = {}
    for name, entry in entries.items():
        live = live_jobs.get(name, {})
        pod = live.get("spec", {}).get("template", {})
        containers = pod.get("spec", {}).get("containers", [])
        env = containers[0].get("env", []) if len(containers) == 1 else []
        if any(e.get("name") in {"SBATCH_NODELIST", "SBATCH_NODES"} for e in env):
            obsolete[name] = (entry, live)
    if not obsolete:
        return
    if runner.state.get("phase") != "acceptance":
        raise RuntimeError("allocation recovery requires unfinished acceptance")
    runner._verify_execution_authority(rule.name, epoch, generation=True)
    runner._verify_isolation()
    barrier = runner._reservation(reservation)
    if barrier["users"] != ["root", "soperatorchecks"]:
        raise RuntimeError("allocation recovery authorization changed")
    proofs = {}
    for name, (entry, live) in obsolete.items():
        meta = live.get("metadata", {})
        pod = live["spec"]["template"]
        if (
            not entry.get("submitted")
            or not entry.get("uid")
            or meta.get("uid") != entry["uid"]
            or meta.get("deletionTimestamp")
            or meta.get("labels", {}).get("cxcli.nebius.ai/check-operation") != runner.operation_id
            or meta.get("annotations", {}).get("cxcli.nebius.ai/check") != rule.name
            or job_execution_digest(live) != entry.get("execution")
            or entry.get("epoch") != epoch
            or entry.get("worker") not in workers
            or SCRIPT_ANNOTATION in pod.get("metadata", {}).get("annotations", {})
            or not any(
                c.get("type") == "Complete" and c.get("status") == "True"
                for c in live.get("status", {}).get("conditions", [])
            )
        ):
            raise RuntimeError("allocation recovery requires the exact completed native Job")
        controlled = {
            "RESERVATION_NAME": reservation,
            "SLURM_RESERVATION": reservation,
            "ACTIVE_CHECK_NAME": name,
            "SBATCH_NODELIST": entry["worker"],
            "SBATCH_NODES": "1",
        }
        if entry.get("gpuCount"):
            controlled["SBATCH_GPUS_PER_NODE"] = str(entry["gpuCount"])
        native_env = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0].get(
            "env", []
        )
        expected_env = [e for e in native_env if e.get("name") not in controlled]
        expected_env += [{"name": k, "value": v} for k, v in controlled.items()]
        if pod["spec"]["containers"][0].get("env") != expected_env:
            raise RuntimeError("allocation recovery submission environment changed")
        native = copy.deepcopy(pod)
        native["spec"]["containers"][0]["env"] = copy.deepcopy(native_env)
        verify_native_template(
            runner.policy.execution_specs[rule.name], runner._get("activecheck", rule.name), native
        )
        raw_id = meta.get("annotations", {}).get("slurm-job-id", "")
        if not re.fullmatch(r"[1-9][0-9]*", raw_id) or (
            entry.get("slurmIds") and entry["slurmIds"] != [raw_id]
        ):
            raise RuntimeError("allocation recovery submission is ambiguous")
        proofs[name] = {"entry": copy.deepcopy(entry), "slurmId": raw_id}
    if len({p["slurmId"] for p in proofs.values()}) != len(proofs):
        raise RuntimeError("allocation recovery Slurm submissions overlap")
    accounting = runner._accounting([p["slurmId"] for p in proofs.values()])
    # Local import avoids a runtime cycle with the canonical executor.
    from .soperator_checks import slurm_acceptance_result

    for name, proof in proofs.items():
        result = slurm_acceptance_result(
            accounting,
            job_id=proof["slurmId"],
            name=name,
            reservation=reservation,
            expected_nodes=(),
            expected_gpus=proof["entry"].get("gpuCount", 0),
            expand_nodes=runner._expand,
        )
        if result is None or len(result["nodes"]) != 1 or not set(result["nodes"]) <= set(workers):
            raise RuntimeError("allocation recovery requires terminal in-scope Slurm evidence")
        proof["terminalResult"] = result
    runner.authority()
    current = runner._jobs()
    if any(current.get(name) != live for name, (_entry, live) in obsolete.items()):
        raise RuntimeError("allocation recovery Job changed before retirement")
    retired = runner.state.setdefault("allocationRetirements", {})
    if set(retired) & set(proofs):
        raise RuntimeError("allocation recovery retained evidence collides")
    for name, proof in proofs.items():
        retired[name] = proof
        del runner.state["jobs"][name]
    runner._save()
    runner.emit(
        f"Retained {len(proofs)} terminal unpinned {rule.name} submissions; requiring fresh worker-bound jobs"
    )
