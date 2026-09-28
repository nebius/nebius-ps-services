"""Bounded ordinary-user Slurm smoke with durable, generation-owned submissions."""

from __future__ import annotations

import hashlib
import math
import re
import shlex
import time
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from .slurm_jobs import expand_slurm_hostlist
from .soperator_checks_policy import checks_digest
from .soperator_receipt_io import read_owner_only_json, write_owner_only_json
from .soperator_status_health import active_worker_ordinals

_NAME = re.compile(r"[A-Za-z0-9_.-]+")
_TERMINAL = {
    "COMPLETED",
    "CANCELLED",
    "FAILED",
    "TIMEOUT",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "PREEMPTED",
    "BOOT_FAIL",
    "DEADLINE",
}
# UID is always recorded. Comment is optional in Slurm accounting (and can
# appear only on completion), so it cannot authorize pending-job cleanup.
_FIELDS = (
    "JobIDRaw,JobName%80,User%64,Partition%64,UID,NodeList%65536,State,ExitCode,AllocTRES%1024"
)


class _ProbeDeadlineExpired(TimeoutError):
    """The local deadline rejected a command before its transport was invoked."""


def _literal_absolute_path(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("/")
        and not value.startswith("//")
        and value != "/"
        and str(PurePosixPath(value)) == value
        and ".." not in PurePosixPath(value).parts
        # Slurm output paths interpret filename patterns even when shell-quoted.
        and not any(ord(char) < 32 or ord(char) == 127 or char in "%\\" for char in value)
    )


def _name(value: Any) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError("Fast readiness requires safe, exact worker and partition names")
    return value


def _fields(line: str) -> dict[str, str]:
    return dict(re.findall(r"(?:^|\s)(\w+)=(\S+)", line))


def _ordinary_partitions(values: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    configuration = (
        values.get("slurmCluster", {}).get("overrideValues", {}).get("partitionConfiguration", {})
    )
    if configuration.get("configType") != "structured":
        raise ValueError("Fast readiness requires structured ordinary-user partitions")
    result = []
    names = set()
    for part in configuration.get("partitions", []):
        name = _name(part.get("name"))
        if name in names:
            raise ValueError("Fast readiness requires unique partition names")
        names.add(name)
        flags = _fields(str(part.get("config", "")))
        if (
            name != "hidden"
            and flags.get("Hidden", "NO") == "NO"
            and flags.get("RootOnly", "NO") == "NO"
            and flags.get("State", "UP") == "UP"
        ):
            result.append(part)
    return result


def preflight_fast_workers(values: Mapping[str, Any]) -> None:
    nodesets = values.get("nodesets", {}).get("overrideValues", {}).get("nodesets", [])
    initial = 0
    for node in nodesets:
        _name(node.get("name"))
        maximum = node.get("replicas")
        active = (
            node.get("initialNumberEphemeralNodes", 0) if node.get("ephemeralNodes") else maximum
        )
        if type(maximum) is not int or type(active) is not int or not 0 <= active <= maximum:
            raise ValueError("Fast deployment requires valid initial and maximum worker capacity")
        initial += active
    if initial < 1:
        raise ValueError("Fast deployment requires at least one initially active worker")
    partitions = _ordinary_partitions(values)
    for node in nodesets:
        if not any(
            part.get("isAll") or node["name"] in part.get("nodeSetRefs", []) for part in partitions
        ):
            raise ValueError(
                "Fast readiness requires an enabled ordinary partition for each NodeSet"
            )


def active_workers(
    values: Mapping[str, Any],
    nodesets: Sequence[Mapping[str, Any]],
    power_states: Sequence[Mapping[str, Any]],
) -> dict[str, bool]:
    """Never wake ephemeral maximum capacity just to prove readiness."""
    desired = values.get("nodesets", {}).get("overrideValues", {}).get("nodesets", [])
    observed = {node.get("metadata", {}).get("name"): node for node in nodesets}
    if len(observed) != len(nodesets) or set(observed) != {node["name"] for node in desired}:
        raise RuntimeError("Fast readiness NodeSet inventory differs from the frozen generation")
    result = {}
    for node in desired:
        name = _name(node["name"])
        actual = observed[name]
        spec = actual.get("spec", {})
        if spec.get("replicas") != node["replicas"] or bool(spec.get("ephemeralNodes")) != bool(
            node.get("ephemeralNodes")
        ):
            raise RuntimeError("Fast readiness worker capacity changed")
        ordinals = active_worker_ordinals(actual, {"soperator_resources": list(power_states)})
        if ordinals is None:
            raise RuntimeError("Fast readiness cannot verify active worker power state")
        for ordinal in sorted(ordinals):
            result[f"{name}-{ordinal}"] = node.get("gpu", {}).get("enabled") is True
    if not result:
        raise RuntimeError("Fast readiness requires at least one active worker")
    return result


def verify_registered_workers(workers: Mapping[str, bool], nodes: str) -> dict[str, dict[str, str]]:
    """Prove active registration while maintenance still owns scheduling.

    Drain and reservation restoration has its own ownership checks. Ordinary
    admission must additionally reject those states before submitting smoke jobs.
    """
    live_nodes = {}
    for line in nodes.splitlines():
        row = _fields(line)
        name = row.get("NodeName")
        if not name or name in live_nodes:
            raise RuntimeError("Fast readiness received ambiguous Slurm nodes")
        live_nodes[name] = row
    for name in workers:
        row = live_nodes.get(name, {})
        state = set(re.split(r"[+*~,]", row.get("State", "")))
        if (
            not state & {"IDLE", "MIXED", "ALLOCATED"}
            or "*" in row.get("State", "")
            or state
            & {
                "DOWN",
                "UNKNOWN",
                "FUTURE",
                "NO_RESPOND",
                "INVALID_REG",
                "POWERED_DOWN",
                "POWERING_DOWN",
                "POWERING_UP",
                "FAIL",
                "FAILING",
            }
            or row.get("SlurmdStartTime", "Unknown") in {"Unknown", "None", "N/A"}
        ):
            raise RuntimeError(f"Fast readiness worker {name} is not registered and responsive")
    return {name: live_nodes[name] for name in workers}


def smoke_groups(
    workers: Mapping[str, bool], nodes: str, partitions: str, *, values: Mapping[str, Any]
) -> list[dict[str, Any]]:
    registered = verify_registered_workers(workers, nodes)
    for name, row in registered.items():
        if set(re.split(r"[+*~,]", row["State"])) & {
            "DRAIN",
            "DRAINED",
            "DRAINING",
            "MAINT",
            "MAINTENANCE",
        }:
            raise RuntimeError(f"Fast readiness worker {name} is not schedulable")
    desired = {part["name"]: part for part in _ordinary_partitions(values)}
    regular = []
    observed = set()
    for line in partitions.splitlines():
        row = _fields(line)
        name = _name(row.get("PartitionName"))
        if name in observed:
            raise RuntimeError("Fast readiness received ambiguous Slurm partitions")
        observed.add(name)
        if name not in desired or row.get("Hidden", "NO") != "NO" or row.get("State") != "UP":
            continue
        if row.get("RootOnly", "NO") != "NO":
            continue
        regular.append((name, set(expand_slurm_hostlist(row.get("Nodes", "")))))
    groups: dict[tuple[str, bool], list[str]] = defaultdict(list)
    for name, gpu in sorted(workers.items()):
        nodeset = name.rsplit("-", 1)[0]
        available = sorted(
            partition
            for partition, members in regular
            if name in members
            and (
                desired[partition].get("isAll")
                or nodeset in desired[partition].get("nodeSetRefs", [])
            )
        )
        if not available:
            raise RuntimeError(f"Fast readiness worker {name} has no enabled ordinary partition")
        groups[(available[0], gpu)].append(_name(name))
    return [
        {"partition": partition, "gpu": gpu, "workers": members}
        for (partition, gpu), members in sorted(groups.items())
    ]


class FastSlurmReadiness:
    def __init__(
        self,
        *,
        generation: str,
        receipt_path: Path,
        run: Callable[[str, int], str],
        assert_authority: Callable[[], object],
        confirm_retirement: Callable[[str], bool] | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.generation = generation
        self.path = receipt_path
        self.run = run
        self.authority = assert_authority
        self.confirm_retirement = confirm_retirement
        self.clock = clock
        self.sleep = sleep
        self.state: dict[str, Any] = {}

    def _validate_jobs(self, groups: list[dict[str, Any]]) -> None:
        home = self.state.get("home")
        if home is not None and not _literal_absolute_path(home):
            raise RuntimeError("Fast readiness receipt account home is invalid")
        attempts: dict[int, int] = defaultdict(int)
        for entry in self.state["jobs"]:
            if not isinstance(entry, dict):
                raise RuntimeError("Fast readiness receipt has an invalid job")
            index = entry.get("groupIndex")
            if type(index) is not int or not 0 <= index < len(groups):
                raise RuntimeError("Fast readiness receipt has an invalid group")
            group = groups[index]
            token = checks_digest(
                {"generation": self.generation, "group": group, "attempt": attempts[index]}
            )[7:31]
            name = "cxcli-fast-" + token
            job_id = entry.get("jobId")
            output = entry.get("output")
            # A sealed cancellation before submission authorizes no job or file
            # transport. Retain that unused intent as history, even when home
            # discovery had not completed; every live attempt binds its home.
            retired_unsubmitted = (
                entry.get("cancelIntent") is True
                and ("submissionStarted" not in entry or entry["submissionStarted"] is False)
                and job_id == ""
                and "proof" not in entry
                and _literal_absolute_path(output)
                and PurePosixPath(str(output)).name == name + ".out"
            )
            started = entry.get("startedAtSeconds")
            if isinstance(started, bool) or not isinstance(started, (int, float)):
                raise RuntimeError("Fast readiness receipt job binding is invalid")
            if (
                any(entry.get(key) != value for key, value in group.items())
                or entry.get("name") != name
                or entry.get("comment") != name
                or (
                    not retired_unsubmitted
                    and (home is None or output != f"{home}/.cache/nebius-cxcli/{name}.out")
                )
                or any(
                    flag in entry and type(entry[flag]) is not bool
                    for flag in ("cancelIntent", "submissionStarted", "submissionNotDispatched")
                )
                or (
                    entry.get("submissionNotDispatched") is True
                    and (
                        entry.get("submissionStarted") is not True
                        or entry.get("cancelIntent") is not True
                        or job_id != ""
                        or "proof" in entry
                    )
                )
                or not isinstance(job_id, str)
                or (job_id and not job_id.isdecimal())
                or not re.fullmatch(
                    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}",
                    str(entry.get("since", "")),
                )
                or not math.isfinite(started)
                or entry.get("deadline") != started + 120
            ):
                raise RuntimeError("Fast readiness receipt job binding is invalid")
            attempts[index] += 1
        self._validate_retirements()

    @staticmethod
    def _uncertain_intent(entry: Mapping[str, Any]) -> bool:
        return (
            entry.get("cancelIntent") is True
            and entry.get("submissionStarted") is True
            and not entry.get("submissionNotDispatched")
            and entry.get("jobId") == ""
            and "proof" not in entry
        )

    def _validate_retirements(self) -> None:
        records = self.state.get("retirements", [])
        if not isinstance(records, list):
            raise RuntimeError("Fast readiness retirement history is invalid")
        jobs = {entry["name"]: entry for entry in self.state["jobs"]}
        names = set()
        for record in records:
            if not isinstance(record, dict) or set(record) != {
                "schema",
                "generation",
                "attemptName",
                "attemptSha256",
                "receiptSha256",
                "approvedAtSeconds",
                "observedAtSeconds",
                "disposition",
            }:
                raise RuntimeError("Fast readiness retirement history is invalid")
            name = record["attemptName"]
            entry = jobs.get(name) if isinstance(name, str) else None
            times = [record["approvedAtSeconds"], record["observedAtSeconds"]]
            if (
                entry is None
                or name in names
                or record["schema"] != "nebius-cxcli.fast-smoke-retirement/v1"
                or record["generation"] != self.generation
                or record["attemptSha256"] != checks_digest(entry)
                or record["disposition"] != "outcome-unknown"
                or not self._uncertain_intent(entry)
                or not isinstance(record["receiptSha256"], str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", record["receiptSha256"])
                or any(
                    type(value) not in (int, float) or not math.isfinite(value) for value in times
                )
                or not entry["deadline"] <= times[0] <= times[1]
            ):
                raise RuntimeError("Fast readiness retirement binding is invalid")
            names.add(name)

    def _queue_absent(self, entry: Mapping[str, Any]) -> None:
        command = shlex.join(
            [
                "squeue",
                "--local",
                "--all",
                "--states=all",
                "--noheader",
                "--name=" + entry["name"],
                "--format=%i",
            ]
        )
        if self.run(command, 20).strip():
            raise RuntimeError(
                "Fast readiness retirement found a visible Slurm job; retry after resolution"
            )

    def _retire_uncertain_intent(self, entry: Mapping[str, Any]) -> bool:
        if any(row["attemptName"] == entry["name"] for row in self.state.get("retirements", [])):
            return True
        if not self._uncertain_intent(entry) or self.clock() < entry["deadline"]:
            return False
        preimage = checks_digest(self.state)
        self.authority()
        if self._accounting(entry) is not None:
            return False
        self._queue_absent(entry)
        prompt = (
            f"Smoke attempt {entry['name']} (generation {self.generation}) has no visible job, "
            "but its original submission outcome is unknown. Retire this exact attempt, "
            "preserve its history, and require a new passing smoke job?"
        )
        if self.confirm_retirement is None or self.confirm_retirement(prompt) is not True:
            raise RuntimeError(
                "Unresolved fast smoke intent requires explicit interactive retirement confirmation"
            )
        approved = self.clock()
        self.authority()
        if self._accounting(entry) is not None:
            raise RuntimeError("Fast readiness retirement accounting changed after confirmation")
        self._queue_absent(entry)
        self.authority()
        if (
            checks_digest(self.state) != preimage
            or checks_digest(read_owner_only_json(self.path, label="fast Slurm readiness"))
            != preimage
        ):
            raise RuntimeError("Fast readiness receipt changed during retirement confirmation")
        self.state.setdefault("retirements", []).append(
            {
                "schema": "nebius-cxcli.fast-smoke-retirement/v1",
                "generation": self.generation,
                "attemptName": entry["name"],
                "attemptSha256": checks_digest(entry),
                "receiptSha256": preimage,
                "approvedAtSeconds": approved,
                "observedAtSeconds": self.clock(),
                "disposition": "outcome-unknown",
            }
        )
        self._validate_retirements()
        # A local record alone is insufficient: this must finish its shared
        # execution checkpoint before any successor can reach transport.
        self._save()
        return True

    def _verify_retired_jobs_quiescent(self) -> None:
        names = {row["attemptName"] for row in self.state.get("retirements", [])}
        for entry in self.state["jobs"]:
            if entry["name"] not in names:
                continue
            row = self._accounting(entry)
            if row is not None and row["state"].split()[0] not in _TERMINAL:
                raise RuntimeError(
                    "A retired fast smoke attempt has active Slurm work; acceptance is blocked"
                )
            # Terminal accounting may lag a requeue/current incarnation.
            self._queue_absent(entry)

    def _probe_run(self, command: str, entry: Mapping[str, Any]) -> str:
        remaining = entry["deadline"] - self.clock()
        if remaining <= 0:
            raise _ProbeDeadlineExpired("Fast readiness exceeded two minutes including scheduling")
        return self.run(command, max(1, min(20, math.ceil(remaining))))

    def _save(self) -> None:
        self.authority()
        write_owner_only_json(self.path, self.state)

    def _accounting(
        self, entry: Mapping[str, Any], *, bounded: bool = False
    ) -> dict[str, str] | None:
        args = f"--jobs={entry['jobId']}" if entry.get("jobId") else f"--name={entry['name']}"
        command = f"TZ=UTC sacct -X -n -P --starttime={entry['since']} {args} --format={_FIELDS}"
        output = self._probe_run(command, entry) if bounded else self.run(command, 20)
        rows = []
        for line in output.splitlines():
            fields = line.split("|")
            if len(fields) != 9 or not fields[0].isdigit():
                raise RuntimeError("Fast readiness received malformed accounting evidence")
            row = dict(
                zip(
                    (
                        "id",
                        "name",
                        "user",
                        "partition",
                        "uid",
                        "nodes",
                        "state",
                        "exit",
                        "tres",
                    ),
                    fields,
                    strict=True,
                )
            )
            if (
                row["name"] != entry["name"]
                or row["user"] != "nebius"
                or row["partition"] != entry["partition"]
                or row["uid"] != self.state["uid"]
                or (entry.get("jobId") and row["id"] != entry["jobId"])
            ):
                raise RuntimeError("Fast readiness job ownership changed")
            rows.append(row)
        if len(rows) > 1:
            raise RuntimeError("Fast readiness submission identity is ambiguous")
        return rows[0] if rows else None

    def _cancel(self, entry: dict[str, Any]) -> None:
        # Seal the recovery disposition before transport, even if submission's
        # reply was lost. Never retry an ambiguous submission without finding it.
        entry["cancelIntent"] = True
        self._save()
        if not entry.get("submissionStarted") or entry.get("submissionNotDispatched"):
            return
        row = self._accounting(entry)
        if row is None:
            raise RuntimeError("Cannot cancel fast readiness job without ownership evidence")
        if not entry.get("jobId"):
            entry["jobId"] = row["id"]
            self._save()
        if row["state"].split()[0] not in _TERMINAL:
            self.authority()
            self.run(f"runuser -u nebius -- scancel {entry['jobId']}", 20)

    def _proof(self, entry: Mapping[str, Any], row: Mapping[str, str]) -> dict[str, Any]:
        if row["state"] != "COMPLETED" or row["exit"] != "0:0":
            raise RuntimeError("Fast readiness job did not complete successfully")
        if set(expand_slurm_hostlist(row["nodes"])) != set(entry["workers"]):
            raise RuntimeError("Fast readiness job used different workers")
        if entry["gpu"]:
            allocated = dict(item.split("=", 1) for item in row["tres"].split(",") if "=" in item)
            if allocated.get("gres/gpu") != str(len(entry["workers"])):
                raise RuntimeError("Fast readiness job did not allocate one GPU per worker")
        command = f"runuser -u nebius -- head -c 65537 {shlex.quote(entry['output'])}"
        output = self.run(command, 20) if "proof" in entry else self._probe_run(command, entry)
        if len(output.encode()) > 65536 or sorted(output.splitlines()) != sorted(entry["workers"]):
            raise RuntimeError(
                "Fast readiness hostname output does not cover every worker exactly once"
            )
        return {
            "jobId": row["id"],
            "user": "nebius",
            "uid": row["uid"],
            "partition": entry["partition"],
            "workers": entry["workers"],
            "gpu": entry["gpu"],
            "state": row["state"],
            "exit": row["exit"],
            "allocation": row["tres"],
            "output": output,
            "outputSha256": hashlib.sha256(output.encode()).hexdigest(),
        }

    def verify(self, groups: list[dict[str, Any]], *, verify_only: bool = False) -> dict[str, Any]:
        identity = {
            "schema": "nebius-cxcli.fast-slurm-readiness/v1",
            "generation": self.generation,
            "groups": groups,
            "user": "nebius",
        }
        saved = (
            read_owner_only_json(self.path, label="fast Slurm readiness")
            if self.path.exists()
            else {**identity, "jobs": []}
        )
        if not isinstance(saved, dict) or not isinstance(saved.get("jobs"), list):
            raise RuntimeError("Fast readiness receipt is invalid")
        self.state = saved
        if any(self.state.get(key) != value for key, value in identity.items()):
            raise RuntimeError("Fast readiness receipt belongs to different inputs or workers")
        self._validate_jobs(groups)
        uid = self.run("id -u nebius", 20).strip()
        if not uid.isdecimal() or int(uid) == 0:
            raise RuntimeError("Fast readiness requires the ordinary non-root nebius user")
        if self.state.get("uid", uid) != uid:
            raise RuntimeError("Fast readiness user identity changed")
        account = self.run("getent passwd nebius", 20).strip().split(":")
        if (
            len(account) != 7
            or account[0] != "nebius"
            or account[2] != uid
            or not _literal_absolute_path(account[5])
        ):
            raise RuntimeError("Fast readiness requires the ordinary user's exact account home")
        home = account[5]
        if self.state.get("home", home) != home:
            raise RuntimeError("Fast readiness user home changed")
        self.state["uid"] = uid
        self.state["home"] = home
        workspace = home + "/.cache/nebius-cxcli"
        if verify_only:
            if self.state.get("status") != "passed" or not groups:
                raise RuntimeError("Completed fast readiness has no durable smoke proof")
            proofs = []
            for index in range(len(groups)):
                entries = [entry for entry in self.state["jobs"] if entry["groupIndex"] == index]
                entry = entries[-1] if entries else None
                if (
                    not entry
                    or not entry.get("proof")
                    or not entry.get("jobId")
                    or not entry.get("submissionStarted")
                    or entry.get("cancelIntent")
                ):
                    raise RuntimeError("Completed fast readiness has incomplete smoke proof")
                self.authority()
                row = self._accounting(entry, bounded=False)
                if row is None:
                    raise RuntimeError("Completed fast readiness accounting proof is missing")
                proof = self._proof(entry, row)
                if entry["proof"] != proof:
                    raise RuntimeError("Fast readiness terminal proof changed")
                proofs.append(proof)
            self._verify_retired_jobs_quiescent()
            return {**identity, "status": "passed", "jobs": proofs}
        self._save()
        proofs = []
        for index, group in enumerate(groups):
            entries = [entry for entry in self.state["jobs"] if entry["groupIndex"] == index]
            entry = entries[-1] if entries else None
            if entry and entry.get("cancelIntent"):
                if not self._retire_uncertain_intent(entry):
                    self._cancel(entry)
                    if entry.get("submissionStarted") and not entry.get("submissionNotDispatched"):
                        row = self._accounting(entry)
                        if row is None or row["state"].split()[0] not in _TERMINAL:
                            raise RuntimeError("Interrupted fast readiness job is not yet terminal")
                entry = None
            if entry is None:
                token = checks_digest(
                    {"generation": self.generation, "group": group, "attempt": len(entries)}
                )[7:31]
                name = "cxcli-fast-" + token
                entry = {
                    **group,
                    "groupIndex": index,
                    "name": name,
                    "comment": name,
                    "since": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(self.clock() - 60)),
                    "startedAtSeconds": self.clock(),
                    "deadline": 0,
                    "jobId": "",
                    "output": f"{workspace}/{name}.out",
                }
                entry["deadline"] = entry["startedAtSeconds"] + 120
                self.state["jobs"].append(entry)
                self._save()
            try:
                row = self._accounting(
                    entry,
                    bounded="proof" not in entry
                    and bool(entry.get("submissionStarted") or entry.get("jobId")),
                )
                if row is not None and not entry["jobId"]:
                    entry["jobId"] = row["id"]
                    entry["submissionStarted"] = True
                if not entry.get("submissionStarted"):
                    self.authority()
                    self.run(
                        shlex.join(
                            [
                                "runuser",
                                "-u",
                                "nebius",
                                "--",
                                "mkdir",
                                "-p",
                                "-m",
                                "700",
                                "--",
                                workspace,
                            ]
                        ),
                        20,
                    )
                    argv = [
                        "runuser",
                        "-u",
                        "nebius",
                        "--",
                        "sbatch",
                        "--parsable",
                        "--job-name=" + entry["name"],
                        "--comment=" + entry["comment"],
                        "--partition=" + group["partition"],
                        "--nodes=" + str(len(group["workers"])),
                        "--nodelist=" + ",".join(group["workers"]),
                        "--ntasks-per-node=1",
                        "--time=1",
                        "--chdir=" + workspace,
                        "--output=" + entry["output"],
                        "--error=" + entry["output"],
                    ]
                    if group["gpu"]:
                        argv.append("--gpus-per-node=1")
                    argv += ["--wrap", "srun --mpi=none --ntasks-per-node=1 hostname"]
                    self._verify_retired_jobs_quiescent()
                    # Prepare the workspace and establish authority before the
                    # scheduling/execution budget begins. Checkpoint publication
                    # can block, so revalidate authority before dispatch as well.
                    self.authority()
                    entry["startedAtSeconds"] = self.clock()
                    entry["deadline"] = entry["startedAtSeconds"] + 120
                    entry["submissionStarted"] = True
                    write_owner_only_json(self.path, self.state)
                    self.authority()
                    try:
                        job_id = self._probe_run(shlex.join(argv), entry).strip()
                    except _ProbeDeadlineExpired:
                        # Only this invocation can prove that run was not called.
                        # Preserve the intent and seal that outcome in _cancel.
                        entry["submissionNotDispatched"] = True
                        raise
                    if not job_id.isdecimal():
                        raise RuntimeError(
                            "Fast readiness submission did not return an exact job ID"
                        )
                    entry["jobId"] = job_id
                while True:
                    # Accounting/output reads do not mutate the job. Proof
                    # publication and cancellation each reassert authority and
                    # persist learned IDs. The durable submission intent allows
                    # exact-name recovery if interrupted before that publication.
                    row = self._accounting(entry, bounded="proof" not in entry)
                    if row and not entry["jobId"]:
                        entry["jobId"] = row["id"]
                    if row and row["state"].split()[0] in _TERMINAL:
                        proof = self._proof(entry, row)
                        if entry.get("proof", proof) != proof:
                            raise RuntimeError("Fast readiness terminal proof changed")
                        entry["proof"] = proof
                        proofs.append(proof)
                        self._save()
                        break
                    if self.clock() >= entry["deadline"]:
                        self._cancel(entry)
                        raise RuntimeError(
                            "Fast readiness exceeded two minutes including scheduling"
                        )
                    self.sleep(min(2, max(0, entry["deadline"] - self.clock())))
            except (KeyboardInterrupt, Exception) as exc:
                try:
                    self._cancel(entry)
                except (KeyboardInterrupt, Exception):
                    # The durable intent drives the next recovery; do not mask
                    # the original failure or bypass lost operation authority.
                    exc.add_note("Owned smoke-job cleanup remains pending; resume the deployment.")
                raise
        self._verify_retired_jobs_quiescent()
        self.state["status"] = "passed"
        self._save()
        return {**identity, "status": "passed", "jobs": proofs}


def verify_fast_readiness(
    *,
    generation: str,
    reports_dir: Path,
    policy: Any,
    groups: Callable[[], list[dict[str, Any]]],
    run: Callable[[str, int], str],
    assert_authority: Callable[[], object],
    verify_only: bool = False,
    confirm_retirement: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Record one owning generation's smoke; completed replay never submits a job."""
    from .soperator_acceptance import current_control, validation_contract
    from .soperator_deployment_profile import FAST_DEV_TEST

    assert_authority()
    receipt_path = reports_dir / (
        "soperator-fast-readiness-" + generation.split(":")[-1][:24] + ".json"
    )
    if verify_only:
        saved = read_owner_only_json(receipt_path, label="fast Slurm readiness")
        if not isinstance(saved, dict) or saved.get("status") != "passed":
            raise RuntimeError("Completed fast readiness has no durable smoke proof")
    proof = FastSlurmReadiness(
        generation=generation,
        receipt_path=receipt_path,
        run=run,
        assert_authority=assert_authority,
        confirm_retirement=confirm_retirement,
    ).verify(groups(), verify_only=verify_only)
    current_control().outcomes[generation] = {
        "validation": {
            "contract": validation_contract(policy),
            "readiness": "passed",
            "extended": "waived",
            "profile": FAST_DEV_TEST,
        },
        "smoke": proof,
    }
    return proof
