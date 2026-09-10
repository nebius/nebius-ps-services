"""Bind native MLC affinity failure without changing the worker CPU-time quota."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Mapping
from typing import Any

from .soperator_checks import SoperatorChecksExecution, _identifier
from .soperator_checks_contract import job_execution_digest
from .soperator_checks_policy import checks_digest
from .soperator_install_runtime_recovery import slurm_nodes
from .soperator_install_storage_recovery import _accounted, _complete
from .soperator_install_topology_recovery import (
    InstallTopologyRecovery,
    _idle,
    reservation_record,
)
from .soperator_worker_topology import h200_static


def capture_cpu_mask_failure(
    runner: SoperatorChecksExecution, read_worker: Callable[[str, list[str]], str]
) -> dict[str, Any]:
    state = runner.state
    workers = state.get("acceptance", {}).get("gpuWorkers", [])
    selected = [(n, e) for n, e in state.get("jobs", {}).items() if e.get("check") == "mem-perf"]
    if (
        state.get("phase") != "acceptance"
        or not state.get("installReservationHandoff")
        or state.get("topologyReservationTransition", {}).get("status") != "complete"
        or not workers
        or sorted(workers) != sorted(state["acceptance"]["workers"])
        or len(selected) != len(workers)
        or {e.get("worker") for _, e in selected} != set(workers)
        or any(e.get("status") != "submit-intent" for _, e in selected)
    ):
        raise RuntimeError("CPU mask repair requires exact rejected native memory checks")
    _idle(runner)
    reservation = runner._reservation(state["reservation"])
    if reservation["users"] != ["root", "soperatorchecks"] or runner.slurm(
        "id -u soperatorchecks"
    ).strip() != state.get("principalUid"):
        raise RuntimeError("CPU mask repair temporary authorization changed")
    records = {
        "raw": reservation_record(runner, reservation["name"], standard=False),
        "standard": reservation_record(runner, reservation["name"], standard=True),
    }
    if checks_digest(records["raw"]) != reservation["fingerprint"] or any(
        row.get("CoreCnt") != str(64 * len(workers))
        or row.get("TRES") != f"cpu={128 * len(workers)}"
        for row in records.values()
    ):
        raise RuntimeError("CPU mask repair original physical reservation changed")
    if any(
        not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", records["standard"].get(k, ""))
        for k in ("StartTime", "EndTime")
    ):
        raise RuntimeError("CPU mask repair requires complete reservation timestamps")
    jobs, nodes = runner._jobs(), slurm_nodes(runner)
    failures = []
    for name, entry in sorted(selected):
        job = jobs.get(name, {})
        meta = job.get("metadata", {})
        runner._verify_execution_authority("mem-perf", entry["epoch"], generation=True)
        if (
            meta.get("uid") != entry.get("uid")
            or meta.get("deletionTimestamp")
            or meta.get("labels", {}).get("cxcli.nebius.ai/check-operation") != runner.operation_id
            or not _complete(job)
            or job_execution_digest(job) != entry["execution"]
        ):
            raise RuntimeError("CPU mask repair lost its exact native submitter")
        jid = str(meta.get("annotations", {}).get("slurm-job-id", ""))
        if not re.fullmatch(r"[1-9][0-9]*", jid) or entry.get("slurmIds") != [jid]:
            raise RuntimeError("CPU mask repair Slurm submission is ambiguous")
        worker = _identifier(entry["worker"])
        row = _accounted(runner, jid)
        tres = dict(p.split("=", 1) for p in row["AllocTRES"].split(",") if "=" in p)
        expected = {
            "JobState": "COMPLETED",
            "ExitCode": "0:0",
            "JobName": name,
            "User": "soperatorchecks",
            "Reservation": reservation["name"],
            "Partition": "hidden",
            "NodeList": worker,
            "NumNodes": "1",
            "NumCPUs": "32",
        }
        if any(row.get(k) != v for k, v in expected.items()) or tres.get("gres/gpu") != "8":
            raise RuntimeError("CPU mask repair native allocation changed")
        expected_node = {
            "Sockets": "2",
            "CoresPerSocket": "32",
            "ThreadsPerCore": "2",
            "CPUTot": "128",
            "CPUEfctv": "32",
        }
        if any(nodes.get(worker, {}).get(k) != v for k, v in expected_node.items()):
            raise RuntimeError("CPU mask repair predecessor topology changed")
        pod = runner._get("pod", worker)
        pm = pod.get("metadata", {})
        owners = [o for o in pm.get("ownerReferences", []) if o.get("controller") is True]
        if (
            not pm.get("uid")
            or pm.get("deletionTimestamp")
            or pod.get("status", {}).get("phase") != "Running"
            or len(owners) != 1
            or owners[0].get("kind") != "StatefulSet"
        ):
            raise RuntimeError("CPU mask repair worker identity changed")
        sts = runner._get("statefulsets.apps.kruise.io", owners[0]["name"])
        parents = [
            o
            for o in sts.get("metadata", {}).get("ownerReferences", [])
            if o.get("controller") is True
        ]
        if (
            sts.get("metadata", {}).get("uid") != owners[0]["uid"]
            or len(parents) != 1
            or parents[0].get("kind") != "NodeSet"
        ):
            raise RuntimeError("CPU mask repair worker ownership changed")
        nodeset = runner._get("nodeset", parents[0]["name"])
        if nodeset.get("metadata", {}).get("uid") != parents[0]["uid"] or nodeset.get(
            "spec", {}
        ).get("nodeConfig", {}).get("static") != h200_static(32000):
            raise RuntimeError("CPU mask repair NodeSet cause changed")
        hardware = dict(re.findall(r"(\w+)=(\S+)", read_worker(worker, ["slurmd", "-C"])))
        physical = {
            "CPUs": "128",
            "Boards": "1",
            "SocketsPerBoard": "2",
            "CoresPerSocket": "32",
            "ThreadsPerCore": "2",
            "Gres": "gpu:nvidia_h200:8",
        }
        status = read_worker(worker, ["cat", "/proc/self/status"])
        if any(hardware.get(k) != v for k, v in physical.items()) or not all(
            re.search(pattern, status, re.M)
            for pattern in (r"^Cpus_allowed_list:\s+0-127$", r"^Mems_allowed_list:\s+0-1$")
        ):
            raise RuntimeError("CPU mask repair hardware or parent CPU access changed")
        output = runner.slurm(
            f"head -c 4194305 -- /opt/soperator-outputs/slurm_jobs/{worker}.{_identifier(name)}.{jid}.out"
        )
        if len(output.encode()) > 4194304 or output.count("Health checker output:\n") != 1:
            raise RuntimeError("CPU mask repair native output is missing or ambiguous")
        try:
            report, _ = json.JSONDecoder().raw_decode(
                output.split("Health checker output:\n")[1].lstrip()
            )
        except (ValueError, RecursionError) as exc:
            raise RuntimeError("CPU mask repair native report is malformed") from exc
        tests = report.get("tests", [])
        if (
            report.get("status") != "ERROR"
            or output.count("Health checker status: ERROR") != 1
            or len(tests) != 2
            or {t.get("name") for t in tests} != {"mem_bw", "mem_lat"}
            or any(t.get("enable") is not True for t in tests)
        ):
            raise RuntimeError("CPU mask repair requires the exact native memory error")
        by_name = {t["name"]: t for t in tests}
        for key, cmd, code, verdict in (
            ("mem_bw", "mlc --bandwidth_matrix", 1, "EMPTY"),
            ("mem_lat", "mlc --latency_matrix", 0, "PASS"),
        ):
            test = by_name[key]
            checks = test.get("checks")
            if (
                str(test.get("cmd")).strip() != cmd
                or type(test.get("state", {}).get("code")) is not int
                or test["state"]["code"] != code
                or test["state"].get("error") != ""
                or not isinstance(checks, list)
                or len(checks) != 1
                or checks[0].get("name") != "mem_perf"
                or checks[0].get("enable") is not True
                or checks[0].get("state", {}).get("status") != verdict
                or checks[0].get("state", {}).get("error") != ""
            ):
                raise RuntimeError("CPU mask repair memory diagnostic contract changed")
        filename = by_name["mem_bw"]["state"].get("stdout", "")
        if not re.fullmatch(r"mem_bw\.[a-f0-9-]+\.out", filename):
            raise RuntimeError("CPU mask repair diagnostic identity changed")
        error = runner.slurm(
            "head -c 32769 -- /opt/soperator-outputs/health_checker_cmd_stdout/" + filename
        )
        if (
            len(error.encode()) > 32768
            or "Error: unable to bind thread to core 127 with hwid 127" not in error
        ):
            raise RuntimeError("CPU mask repair exact binding error is absent")
        failures.append(
            {
                "job": {"name": name, "uid": entry["uid"], "entry": copy.deepcopy(entry)},
                "slurm": row,
                "worker": {"name": worker, "uid": pm["uid"], "nodeset": parents[0]},
                "hardware": physical,
                "parentCpus": "0-127",
                "parentMemoryNodes": "0-1",
                "outputSha256": checks_digest(output),
                "errorSha256": checks_digest(error),
            }
        )
    return {"failures": failures, "reservation": reservation, "reservationRecords": records}


class InstallCpuMaskRecovery(InstallTopologyRecovery):
    def __init__(self, runner: SoperatorChecksExecution, repair: Mapping[str, Any]) -> None:
        super().__init__(
            runner, repair, failure_key="cpuMaskFailure", quiescence_key="cpuMaskRepairQuiescence"
        )

    def maintenance(self, mutate: bool) -> None:
        if self.runner.state.get("phase") == "restored":
            return
        nodes = slurm_nodes(self.runner)
        names = {f["worker"]["name"] for f in self.proof["failures"]}
        expected = {
            "Sockets": "2",
            "CoresPerSocket": "32",
            "ThreadsPerCore": "2",
            "CPUTot": "128",
            "CPUEfctv": "128",
        }
        if set(nodes) != names or any(
            any(row.get(k) != v for k, v in expected.items()) for row in nodes.values()
        ):
            raise RuntimeError("CPU mask repair full hardware CPU access has not converged")
        name = self.proof["reservation"]["name"]
        live = {
            "raw": reservation_record(self.runner, name, standard=False),
            "standard": reservation_record(self.runner, name, standard=True),
        }
        if live != self.proof["reservationRecords"]:
            raise RuntimeError("CPU mask repair original reservation changed")
        self.runner._reservation(name)
