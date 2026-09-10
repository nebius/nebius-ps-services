"""Bind a native NUMA failure and preserve its full-node maintenance reservation."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Mapping
from typing import Any

from .soperator_checks import SoperatorChecksExecution, _identifier
from .soperator_checks_contract import job_execution_digest
from .soperator_checks_policy import checks_digest
from .soperator_install_runtime_recovery import slurm_fields, slurm_jobs, slurm_nodes
from .soperator_install_storage_recovery import _accounted, _complete
from .soperator_worker_topology import H200_PHYSICAL, H200_QUOTA_DEFAULT

_ACTIVE = {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "SUSPENDED"}
_CHECK = "ib-gpu-perf"
_PHYSICAL = {"Boards": "1", "SocketsPerBoard": "2", "CoresPerSocket": "32", "ThreadsPerCore": "2"}


def _idle(runner: SoperatorChecksExecution) -> None:
    if any(row.get("JobState") in _ACTIVE for row in slurm_jobs(runner)):
        raise RuntimeError("topology recovery cannot overlap active Slurm work")


def reservation_record(
    runner: SoperatorChecksExecution, name: str, *, standard: bool
) -> dict[str, str]:
    prefix = "env SLURM_TIME_FORMAT=standard TZ=UTC " if standard else ""
    raw = runner.slurm(prefix + "scontrol show reservation " + _identifier(name) + " -o")
    if raw.count("ReservationName=") != 1:
        raise RuntimeError("topology recovery reservation is ambiguous")
    # Preserve the original fingerprint contract until that reservation is released.
    fields = slurm_fields(raw) if standard else dict(re.findall(r"(\w+)=(\S+)", raw))
    return {k: v for k, v in fields.items() if k != "Users"}


def expected_reservation(record: Mapping[str, str], workers: int) -> dict[str, str]:
    result = dict(record)
    if record.get("CoreCnt") != str(32 * workers) or record.get("TRES") != f"cpu={32 * workers}":
        raise RuntimeError("topology recovery requires the exact predecessor resource totals")
    result.update(CoreCnt=str(64 * workers), TRES=f"cpu={128 * workers}")
    return result


def capture_topology_failure(
    runner: SoperatorChecksExecution, read_worker: Callable[[str, list[str]], str]
) -> dict[str, Any]:
    state = runner.state
    workers = state.get("acceptance", {}).get("gpuWorkers", [])
    selected = [(n, e) for n, e in state.get("jobs", {}).items() if e.get("check") == _CHECK]
    if (
        state.get("phase") != "acceptance"
        or not state.get("installReservationIntent")
        or not workers
        or sorted(workers) != sorted(state["acceptance"]["workers"])
        or len(selected) != len(workers)
        or {e.get("worker") for _, e in selected} != set(workers)
        or any(e.get("status") != "complete" for _, e in selected)
    ):
        raise RuntimeError("topology repair requires the exact falsely completed native GPU checks")
    _idle(runner)
    reservation = runner._reservation(state["reservation"])
    if reservation["users"] != ["root", "soperatorchecks"]:
        raise RuntimeError("topology repair temporary authorization changed")
    if runner.slurm("id -u soperatorchecks").strip() != state.get("principalUid"):
        raise RuntimeError("topology repair principal identity changed")
    records = {
        "raw": reservation_record(runner, reservation["name"], standard=False),
        "standard": reservation_record(runner, reservation["name"], standard=True),
    }
    if checks_digest(records["raw"]) != reservation["fingerprint"]:
        raise RuntimeError("topology repair reservation fingerprint changed")
    for record in records.values():
        expected_reservation(record, len(workers))
    if any(
        not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", records["standard"].get(k, ""))
        for k in ("StartTime", "EndTime")
    ):
        raise RuntimeError("topology repair requires complete reservation timestamps")
    jobs, nodes = runner._jobs(), slurm_nodes(runner)
    failures = []
    for name, entry in sorted(selected):
        job = jobs.get(name, {})
        meta = job.get("metadata", {})
        runner._verify_execution_authority(_CHECK, entry["epoch"], generation=True)
        if (
            meta.get("uid") != entry.get("uid")
            or meta.get("deletionTimestamp")
            or meta.get("labels", {}).get("cxcli.nebius.ai/check-operation") != runner.operation_id
            or not _complete(job)
            or job_execution_digest(job) != entry["execution"]
        ):
            raise RuntimeError("topology repair lost its exact native submitter")
        jid = str(meta.get("annotations", {}).get("slurm-job-id", ""))
        if not re.fullmatch(r"[1-9][0-9]*", jid) or entry.get("slurmIds") != [jid]:
            raise RuntimeError("topology repair Slurm submission is ambiguous")
        worker = _identifier(entry["worker"])
        row = _accounted(runner, jid)
        tres = dict(p.split("=", 1) for p in row["AllocTRES"].split(",") if "=" in p)
        if (
            row["JobState"] != "COMPLETED"
            or row["ExitCode"] != "0:0"
            or row["JobName"] != name
            or row["User"] != "soperatorchecks"
            or row["Reservation"] != reservation["name"]
            or row["Partition"] != "hidden"
            or row["NodeList"] != worker
            or row["NumNodes"] != "1"
            or row["NumCPUs"] != "32"
            or tres.get("gres/gpu") != "8"
        ):
            raise RuntimeError("topology repair native allocation changed")
        live_node = nodes.get(worker, {})
        if any(
            live_node.get(k) != v
            for k, v in {
                "Sockets": "1",
                "CoresPerSocket": "32",
                "ThreadsPerCore": "1",
                "CPUTot": "32",
            }.items()
        ):
            raise RuntimeError("topology repair predecessor CPU topology changed")
        pod = runner._get("pod", worker)
        pod_meta = pod.get("metadata", {})
        owners = [o for o in pod_meta.get("ownerReferences", []) if o.get("controller") is True]
        if (
            not pod_meta.get("uid")
            or pod_meta.get("deletionTimestamp")
            or pod.get("status", {}).get("phase") != "Running"
            or len(owners) != 1
            or owners[0].get("kind") != "StatefulSet"
        ):
            raise RuntimeError("topology repair worker identity changed")
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
            raise RuntimeError("topology repair worker ownership changed")
        nodeset = runner._get("nodeset", parents[0]["name"])
        if (
            nodeset.get("metadata", {}).get("uid") != parents[0]["uid"]
            or nodeset.get("spec", {}).get("nodeConfig", {}).get("static")
            != H200_QUOTA_DEFAULT + " Gres=gpu:8"
        ):
            raise RuntimeError("topology repair NodeSet cause changed")
        hardware = dict(re.findall(r"(\w+)=(\S+)", read_worker(worker, ["slurmd", "-C"])))
        if (
            any(hardware.get(k) != v for k, v in {**_PHYSICAL, "CPUs": "128"}.items())
            or hardware.get("Gres") != "gpu:nvidia_h200:8"
        ):
            raise RuntimeError("topology repair hardware is not the verified H200 shape")
        numa = [
            read_worker(worker, ["cat", f"/sys/devices/system/node/node{i}/cpulist"]).strip()
            for i in range(2)
        ]
        if numa != ["0-63", "64-127"]:
            raise RuntimeError("topology repair physical NUMA mapping changed")
        output = runner.slurm(
            f"head -c 4194305 -- /opt/soperator-outputs/slurm_jobs/{worker}.{_identifier(name)}.{jid}.out"
        )
        if len(output.encode()) > 4194304 or output.count("Health checker output:\n") != 1:
            raise RuntimeError("topology repair native output is missing or ambiguous")
        try:
            report, _ = json.JSONDecoder().raw_decode(
                output.split("Health checker output:\n")[1].lstrip()
            )
        except (ValueError, RecursionError) as exc:
            raise RuntimeError("topology repair native report is malformed") from exc
        if report.get("status") != "ERROR" or "Health checker status: ERROR" not in output:
            raise RuntimeError("topology repair requires the original native health ERROR")
        tests = report.get("tests", [])
        failed = [
            t for t in tests if t.get("enable") is True and t.get("state", {}).get("code") == 1
        ]
        expected_names = {
            f"{test}_gpu_0_{gpu}"
            for test in ("ib_write_bw", "ib_send_lat", "ib_read_lat")
            for gpu in range(4, 8)
        }
        if len(failed) != 12 or {t.get("name") for t in failed} != expected_names:
            raise RuntimeError("topology repair requires exact cross-NUMA diagnostic failures")
        errors = []
        for test in sorted(failed, key=lambda t: t["name"]):
            filename = test["state"].get("stderr", "")
            if not re.fullmatch(r"[A-Za-z0-9_-]+\.[a-f0-9-]+\.out", filename) or not test.get(
                "cmd", ""
            ).startswith("numactl --cpunodebind=1 --membind=1 "):
                raise RuntimeError("topology repair native diagnostic identity changed")
            stderr = runner.slurm(
                "head -c 16385 -- /opt/soperator-outputs/health_checker_cmd_stdout/" + filename
            )
            if len(stderr.encode()) > 16384 or "sched_setaffinity: Invalid argument" not in stderr:
                raise RuntimeError("topology repair no longer has the exact CPU affinity failure")
            errors.append({"test": test["name"], "outputSha256": checks_digest(stderr)})
        failures.append(
            {
                "job": {"name": name, "uid": entry["uid"], "entry": copy.deepcopy(entry)},
                "slurm": row,
                "worker": {"name": worker, "uid": pod_meta["uid"], "nodeset": parents[0]},
                "hardware": H200_PHYSICAL,
                "numa": numa,
                "outputSha256": checks_digest(output),
                "errors": errors,
            }
        )
    return {"failures": failures, "reservation": reservation, "reservationRecords": records}


class InstallTopologyRecovery:
    def __init__(
        self,
        runner: SoperatorChecksExecution,
        repair: Mapping[str, Any],
        *,
        failure_key: str = "topologyFailure",
        quiescence_key: str = "topologyRepairQuiescence",
    ) -> None:
        self.runner, self.repair = runner, repair
        self.proof = repair[failure_key]
        self.quiescence_key = quiescence_key
        self.identity = checks_digest(repair)

    def close(self) -> None:
        runner = self.runner
        saved = runner.state.get(self.quiescence_key)
        expected = {"repair": self.identity, "status": "complete"}
        if saved:
            if saved != expected:
                raise RuntimeError("topology recovery identity changed")
            return
        _idle(runner)
        for failure in self.proof["failures"]:
            live = runner._get("job", failure["job"]["name"])
            if (
                live.get("metadata", {}).get("uid") != failure["job"]["uid"]
                or not _complete(live)
                or job_execution_digest(live) != failure["job"]["entry"]["execution"]
                or _accounted(runner, failure["slurm"]["JobId"]) != failure["slurm"]
            ):
                raise RuntimeError("topology recovery native failure changed")
        name = self.proof["reservation"]["name"]
        observed = runner._reservation(name)
        if (
            observed["fingerprint"] != self.proof["reservation"]["fingerprint"]
            or reservation_record(runner, name, standard=True)
            != self.proof["reservationRecords"]["standard"]
        ):
            raise RuntimeError("topology recovery original reservation changed")
        runner.authority()
        runner.slurm(f"scontrol update ReservationName={_identifier(name)} Users=root")
        if runner._reservation(name)["users"] != ["root"]:
            raise RuntimeError("topology recovery authorization did not close")
        _idle(runner)
        runner.state[self.quiescence_key] = expected
        runner._save()

    def adopt(self, handoff: Mapping[str, Any]) -> None:
        transition = self.runner.state.get("topologyReservationTransition")
        if not transition:
            self.runner.adopt_install_reservation(handoff)
            return
        expected_handoff = {
            "predecessorOperation": handoff["operation"],
            "predecessorReceiptSha256": handoff["receiptSha256"],
            "reservation": handoff["reservation"],
            "fingerprint": handoff["fingerprint"],
            "predecessorPolicy": handoff["predecessorPolicy"],
            "policy": handoff["policy"],
        }
        if (
            transition.get("repair") != self.identity
            or transition.get("status") != "complete"
            or self.runner.state.get("reservationFingerprint") != transition.get("fingerprint")
            or self.runner.state.get("installReservationHandoff") != expected_handoff
            or handoff["policy"] != self.runner.policy.sha256
        ):
            raise RuntimeError("topology recovery lost its sealed reservation transition")

    def maintenance(self, mutate: bool) -> None:
        runner = self.runner
        count = len(self.proof["failures"])
        expected = {
            k: expected_reservation(v, count) for k, v in self.proof["reservationRecords"].items()
        }
        fingerprint = checks_digest(expected["raw"])
        completed = {"repair": self.identity, "status": "complete", "fingerprint": fingerprint}
        saved = runner.state.get("topologyReservationTransition")
        if saved and saved != completed:
            raise RuntimeError("topology recovery reservation transition changed")
        if runner.state.get("phase") == "restored" and saved == completed:
            return
        if not saved:
            _idle(runner)
        nodes = slurm_nodes(runner)
        names = {f["worker"]["name"] for f in self.proof["failures"]}
        if set(nodes) != names or any(
            any(
                row.get(k) != v
                for k, v in {
                    "Sockets": "2",
                    "CoresPerSocket": "32",
                    "ThreadsPerCore": "2",
                    "CPUTot": "128",
                    "CPUEfctv": "32",
                }.items()
            )
            for row in nodes.values()
        ):
            raise RuntimeError("topology recovery corrected Slurm resources have not converged")
        name = self.proof["reservation"]["name"]

        def observed():
            return {
                "raw": reservation_record(runner, name, standard=False),
                "standard": reservation_record(runner, name, standard=True),
            }

        live = observed()
        if not saved:
            self._root_only(name)
            _idle(runner)
        if live == self.proof["reservationRecords"] and mutate and not saved:
            runner.authority()
            runner.slurm(f"scontrol update ReservationName={_identifier(name)} Nodes=ALL")
            live = observed()
        if live != expected:
            raise RuntimeError(
                "topology recovery changed reservation fields beyond physical totals"
            )
        if saved:
            runner._reservation(name)
            return
        self._root_only(name)
        _idle(runner)
        if not saved:
            if (
                not mutate
                or runner.state.get("reservationFingerprint")
                != self.proof["reservation"]["fingerprint"]
            ):
                raise RuntimeError("topology reservation transition is not durably owned")
            runner.authority()
            runner.state["reservationFingerprint"] = fingerprint
            runner.state["topologyReservationTransition"] = completed
            runner._save()
        runner._reservation(name)

    def _root_only(self, name: str) -> None:
        users = self.runner.slurm("scontrol show reservation " + _identifier(name) + " -o")
        if dict(re.findall(r"(\w+)=(\S+)", users)).get("Users") != "root":
            raise RuntimeError("topology recovery reservation must remain root-only")
