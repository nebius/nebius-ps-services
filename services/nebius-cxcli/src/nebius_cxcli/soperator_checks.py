"""Operation-bound execution of native upstream Soperator health checks."""

from __future__ import annotations

import copy
import json
import re
import shlex
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .soperator_checks_allocation import SCRIPT_ANNOTATION, SCRIPT_VOLUME, allocation_script
from .soperator_checks_allocation_recovery import retire_unpinned_jobs
from .soperator_checks_contract import (
    job_execution_digest,
    verify_check_spec,
    verify_native_template,
)
from .soperator_checks_policy import (
    CHECKS_POLICY_ENV,
    CheckRule,
    SoperatorChecksPolicy,
    checks_digest,
)
from .soperator_checks_verdict import read_native_verdict
from .soperator_receipt_io import read_owner_only_json, write_owner_only_json
from .soperator_slurm_recovery import _record_fields

if TYPE_CHECKING:
    from .soperator_checks_lifecycle import ChecksLifecycle

_SCHEMA = "nebius-cxcli.soperator-checks-execution.v2"
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]+")
_JOB_ID = re.compile(r"[1-9][0-9]*")
_LABEL = "cxcli.nebius.ai/check-operation"


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise RuntimeError("invalid Soperator check resource identity")
    return value


def native_acceptance_job(
    cronjob: Mapping[str, Any],
    *,
    rule: CheckRule,
    operation_id: str,
    reservation: str,
    worker: str = "",
    job_name: str = "",
    gpu_count: int = 0,
    sbatch_script: str | None = None,
) -> dict[str, Any]:
    """Copy upstream execution verbatim except deterministic identity and allocation."""
    _identifier(reservation)
    if worker:
        _identifier(worker)
    template = copy.deepcopy(cronjob["spec"]["jobTemplate"])
    allocation = None
    identity = [operation_id, rule.name, worker]
    if rule.check_type == "slurmJob":
        if sbatch_script is None:
            raise RuntimeError("native acceptance requires the verified sbatch script")
        allocation = allocation_script(sbatch_script, worker=worker, gpu_count=gpu_count)
        identity.append(checks_digest(allocation))
    token = checks_digest(identity).split(":")[1][:24]
    name = _identifier(job_name) if job_name else "cxcli-check-" + token
    metadata = template.setdefault("metadata", {})
    metadata["labels"] = {
        **cronjob.get("metadata", {}).get("labels", {}),
        **metadata.get("labels", {}),
    }
    metadata.pop("ownerReferences", None)
    metadata.update(name=name, namespace="soperator")
    metadata.setdefault("labels", {})[_LABEL] = operation_id
    metadata.setdefault("annotations", {})["cxcli.nebius.ai/check"] = rule.name
    spec = template["spec"]
    spec["backoffLimit"] = 0
    spec.pop("ttlSecondsAfterFinished", None)
    pod = spec["template"]
    pod.setdefault("metadata", {}).setdefault("labels", {})[_LABEL] = operation_id
    pod["spec"]["restartPolicy"] = "Never"
    containers = pod["spec"].get("containers")
    if not isinstance(containers, list) or len(containers) != 1:
        raise RuntimeError("unsupported native check container topology")
    environment = {
        "RESERVATION_NAME": reservation,
        "SLURM_RESERVATION": reservation,
        "ACTIVE_CHECK_NAME": name,
    }
    if gpu_count:
        environment["SBATCH_GPUS_PER_NODE"] = str(gpu_count)
    container = containers[0]
    if any(
        item.get("name") in {"SBATCH_NODELIST", "SBATCH_NODES"} for item in container.get("env", [])
    ):
        raise RuntimeError("native template contains unsupported sbatch worker selectors")
    env = [item for item in container.get("env", []) if item.get("name") not in environment]
    env.extend({"name": key, "value": value} for key, value in environment.items())
    container["env"] = env
    if allocation is not None:
        annotations = pod.setdefault("metadata", {}).setdefault("annotations", {})
        if SCRIPT_ANNOTATION in annotations:
            raise RuntimeError("native acceptance script annotation is already present")
        annotations[SCRIPT_ANNOTATION] = allocation
        volumes = pod["spec"].get("volumes", [])
        scripts = [v for v in volumes if v.get("name") == "sbatch-volume"]
        if (
            len(scripts) != 1
            or scripts[0].get("configMap", {}).get("name") != "sbatch-script-" + rule.name
        ):
            raise RuntimeError("native acceptance script volume is unavailable")
        volumes[volumes.index(scripts[0])] = copy.deepcopy(SCRIPT_VOLUME)
    return {"apiVersion": "batch/v1", "kind": "Job", **template}


def slurm_acceptance_result(
    text: str,
    *,
    job_id: str,
    name: str,
    reservation: str,
    expected_nodes: tuple[str, ...],
    expand_nodes: Callable[[str], tuple[str, ...]],
    expected_gpus: int = 0,
) -> dict[str, Any] | None:
    """Empty accounting is pending, never evidence of success."""
    _identifier(name)
    if not _JOB_ID.fullmatch(job_id):
        raise RuntimeError("invalid Soperator check Slurm job ID")
    rows = [line.split("|") for line in text.splitlines() if line.strip()]
    jobs = [row for row in rows if row[0] == job_id]
    if not jobs:
        return None
    if len(jobs) != 1 or len(jobs[0]) != 8:
        raise RuntimeError("ambiguous Slurm acceptance accounting")
    _, state, exit_code, nodes, user, actual_name, actual_reservation, tres = jobs[0]
    if (user, actual_name, actual_reservation) != ("soperatorchecks", name, reservation):
        raise RuntimeError("Slurm acceptance identity changed")
    state = state.split()[0].rstrip("+")
    if state in {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "SUSPENDED"}:
        return None
    if state != "COMPLETED" or exit_code != "0:0":
        raise RuntimeError("Soperator acceptance job did not complete successfully")
    actual_nodes = tuple(sorted(expand_nodes(nodes)))
    if not actual_nodes or (expected_nodes and actual_nodes != tuple(sorted(expected_nodes))):
        raise RuntimeError("Soperator acceptance worker coverage differs from the plan")
    resources = dict(item.split("=", 1) for item in tres.split(",") if "=" in item)
    if expected_gpus and resources.get("gres/gpu") != str(expected_gpus):
        raise RuntimeError("Soperator acceptance did not allocate every worker GPU")
    return {
        "jobId": job_id,
        "state": state,
        "exitCode": exit_code,
        "nodes": list(actual_nodes),
        "allocatedGpus": int(resources.get("gres/gpu", "0")),
    }


class SoperatorChecksExecution:
    """Fenced recoverable checks lane. Callers own Kubernetes and Slurm transports."""

    def __init__(
        self,
        *,
        policy: SoperatorChecksPolicy,
        operation_id: str,
        receipt_path: Path,
        kubernetes: Callable[[list[str], Mapping[str, Any] | None], Mapping[str, Any]],
        slurm: Callable[[str], str],
        assert_authority: Callable[[], object],
        handoff_owner: str | None = None,
        emit: Callable[[str], object] = lambda _message: None,
        timeout_seconds: float = 7200,
        poll_seconds: float = 3,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.policy = policy
        self.operation_id = checks_digest(operation_id).split(":")[1][:32]
        self.handoff_owner = self.operation_id if handoff_owner is None else handoff_owner
        self.path = receipt_path
        self.kube = kubernetes
        self.slurm = slurm
        self.authority = assert_authority
        self.emit = emit
        self.timeout = timeout_seconds
        self.poll = poll_seconds
        self.clock = clock
        self.sleep = sleep
        self.lifecycle: ChecksLifecycle | None = None
        identity = {"schema": _SCHEMA, "operation": operation_id, "policy": policy.sha256}
        if receipt_path.exists():
            payload = read_owner_only_json(receipt_path, label="Soperator checks execution")
            if not isinstance(payload, dict) or any(
                payload.get(k) != v for k, v in identity.items()
            ):
                raise RuntimeError("recovery-required: Soperator checks receipt identity differs")
            self.state = payload
        else:
            self.state = {**identity, "jobs": {}, "phase": "planned"}

    def require_lifecycle(self) -> ChecksLifecycle:
        if self.lifecycle is None:
            raise RuntimeError("diagnostic maintenance lifecycle ownership is unavailable")
        return self.lifecycle

    def _save(self) -> None:
        self.authority()
        write_owner_only_json(self.path, self.state)

    def _patch(
        self, kind: str, name: str, namespace: str, patch: Mapping[str, Any], *, uid: str
    ) -> Mapping[str, Any]:
        before = self._get(kind, name, namespace)
        metadata = before.get("metadata", {})
        if metadata.get("uid") != uid or not metadata.get("resourceVersion"):
            raise RuntimeError("Soperator check mutation has no resource identity")
        patch = {
            **patch,
            "metadata": {"uid": metadata["uid"], "resourceVersion": metadata["resourceVersion"]},
        }
        self.authority()
        return self.kube(
            [
                "patch",
                kind,
                _identifier(name),
                "-n",
                _identifier(namespace),
                "--type=merge",
                "-p",
                json.dumps(patch),
                "-o",
                "json",
            ],
            None,
        )

    def _get(self, kind: str, name: str = "", namespace: str = "soperator") -> Mapping[str, Any]:
        args = ["get", kind]
        if name:
            args.append(_identifier(name))
        args.extend(["-n", _identifier(namespace), "-o", "json", "--ignore-not-found"])
        return self.kube(args, None)

    def _until(self, action: Callable[[], Any], description: str) -> Any:
        deadline = self.clock() + self.timeout
        while True:
            self.authority()
            result = action()
            if result:
                return result
            if self.clock() >= deadline:
                raise RuntimeError(
                    f"Soperator checks waiting for {description}; recovery remains available"
                )
            self.emit(description)
            self.sleep(self.poll)

    def quiesce_source(self) -> dict[str, Any]:
        """Pause exact check writers before CR changes and retain write-ahead preimages."""
        if self.state.get("targetApplyIntent"):
            # Source ownership has crossed into the staged target reconciler.
            return {"status": "target-reconciliation-owned"}
        crd = self.kube(
            ["get", "crd", "activechecks.slurm.nebius.ai", "-o", "json", "--ignore-not-found"], None
        )
        if not crd:
            if self.state.get("sourceChecks"):
                raise RuntimeError("source ActiveCheck API disappeared during quiescence")
            return {"status": "not-required"}
        checks = self._get("activechecks.slurm.nebius.ai").get("items", [])
        if "sourceChecks" not in self.state:
            self.state["sourceChecks"] = [
                {
                    "name": c["metadata"]["name"],
                    "uid": c["metadata"]["uid"],
                    "spec": {k: c["spec"].get(k, False) for k in ("suspend", "runAfterCreation")},
                }
                for c in checks
            ]
            self._save()
        if "sourceWriters" not in self.state:
            resources = list(checks)
            if self.policy.auxiliary_pvc:
                resources.append(self._get("cronjob", "run-extensive-check-on-reservations"))
            if self.policy.passive.get("supported"):
                passive_config = self._get("configmap", "slurm-scripts")
                if passive_config:
                    resources.append(passive_config)
            self.state["sourceWriters"] = self._source_writers(resources)
            self._save()
        for writer in self.state["sourceWriters"]:
            live = self._get(writer["kind"], writer["name"], writer["namespace"])
            if live.get("metadata", {}).get("uid") != writer["uid"]:
                raise RuntimeError("source check writer UID changed")
            self._patch(
                writer["kind"],
                writer["name"],
                writer["namespace"],
                {"spec": {"suspend": True}},
                uid=writer["uid"],
            )

        def writers_quiet() -> bool:
            for writer in self.state["sourceWriters"]:
                live = self._get(writer["kind"], writer["name"], writer["namespace"])
                if (
                    live.get("metadata", {}).get("uid") != writer["uid"]
                    or live.get("spec", {}).get("suspend") is not True
                ):
                    raise RuntimeError("source check declarative writer escaped suspension")
                generation = live.get("metadata", {}).get("generation")
                if (
                    not isinstance(generation, int)
                    or generation <= 0
                    or live.get("status", {}).get("observedGeneration") != generation
                ):
                    return False
                if any(
                    c.get("type") == "Reconciling" and c.get("status") == "True"
                    for c in live.get("status", {}).get("conditions", [])
                ):
                    return False
            return True

        self._until(writers_quiet, "source check declarative writers to quiesce")
        if self.policy.auxiliary_pvc:
            auxiliary = self._get("cronjob", "run-extensive-check-on-reservations")
            if "sourceAuxiliary" not in self.state:
                metadata = auxiliary.get("metadata", {})
                if not metadata.get("uid"):
                    raise RuntimeError("Source auxiliary checks CronJob is missing")
                self.state["sourceAuxiliary"] = {
                    "name": metadata["name"],
                    "uid": metadata["uid"],
                    "suspend": auxiliary.get("spec", {}).get("suspend", False),
                }
                self._save()
            owned_auxiliary = self.state["sourceAuxiliary"]
            self._patch(
                "cronjob",
                owned_auxiliary["name"],
                "soperator",
                {"spec": {"suspend": True}},
                uid=owned_auxiliary["uid"],
            )
        frozen_inventory = {item["name"]: item["uid"] for item in self.state["sourceChecks"]}

        def same_inventory() -> None:
            current = self._get("activechecks.slurm.nebius.ai").get("items", [])
            if {
                item["metadata"]["name"]: item["metadata"].get("uid") for item in current
            } != frozen_inventory:
                raise RuntimeError("source ActiveCheck inventory changed while quiescing writers")

        same_inventory()
        for item in self.state["sourceChecks"]:
            live = self._get("activecheck", item["name"])
            if live.get("metadata", {}).get("uid") != item["uid"]:
                raise RuntimeError("source ActiveCheck identity changed")
            self._patch(
                "activecheck",
                item["name"],
                "soperator",
                {"spec": {"suspend": True, "runAfterCreation": False}},
                uid=item["uid"],
            )

        def quiet() -> bool:
            if not writers_quiet():
                return False
            same_inventory()
            owned_auxiliary = self.state.get("sourceAuxiliary")
            if owned_auxiliary:
                auxiliary = self._get("cronjob", owned_auxiliary["name"])
                if (
                    auxiliary.get("metadata", {}).get("uid") != owned_auxiliary["uid"]
                    or auxiliary.get("spec", {}).get("suspend") is not True
                ):
                    raise RuntimeError("Source auxiliary checks escaped suspension")
            for item in self.state["sourceChecks"]:
                live = self._get("activecheck", item["name"])
                if (
                    live.get("metadata", {}).get("uid") != item["uid"]
                    or live.get("spec", {}).get("suspend") is not True
                    or live.get("spec", {}).get("runAfterCreation") is not False
                ):
                    raise RuntimeError("source check writer reverted quiescence")
                cron = self._get("cronjob", item["name"])
                if cron and cron.get("spec", {}).get("suspend") is not True:
                    return False
            jobs = self._get("jobs").get("items", [])
            return not any(
                not any(
                    c.get("type") in {"Complete", "Failed"} and c.get("status") == "True"
                    for c in j.get("status", {}).get("conditions", [])
                )
                for j in jobs
                if any(
                    ref.get("name")
                    in (
                        set(frozen_inventory)
                        | ({owned_auxiliary["name"]} if owned_auxiliary else set())
                    )
                    and ref.get("kind") == "CronJob"
                    for ref in j.get("metadata", {}).get("ownerReferences", [])
                )
                or j.get("metadata", {}).get("labels", {}).get("component") == "soperatorchecks"
            )

        self._until(quiet, "in-flight upstream checks to finish")
        # Submitted Slurm work outlives the Kubernetes submission Job.
        self._until(
            lambda: quiet() and not self.slurm("squeue -h -u soperatorchecks -o '%i'").strip(),
            "in-flight Slurm diagnostics to finish",
        )
        self.state["sourceQuiesced"] = True
        self.state["phase"] = "source-quiesced"
        self._save()
        return {"status": "quiesced", "count": len(checks)}

    def _source_writers(self, checks: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        releases = self.kube(
            ["get", "helmreleases.helm.toolkit.fluxcd.io", "-A", "-o", "json"], None
        ).get("items", [])
        writers: dict[tuple[str, str, str], dict[str, Any]] = {}
        visiting: set[tuple[str, str, str]] = set()

        def add(resource: Mapping[str, Any], kind: str) -> None:
            metadata = resource["metadata"]
            key = (kind, metadata["namespace"], metadata["name"])
            if key in visiting:
                raise RuntimeError("source check writer ownership cycle")
            if key in writers:
                return
            visiting.add(key)
            parents(resource, require=False)
            visiting.remove(key)
            writers[key] = {
                "kind": kind,
                "namespace": key[1],
                "name": key[2],
                "uid": metadata["uid"],
                "suspend": resource.get("spec", {}).get("suspend", False),
                "contract": checks_digest(
                    {k: v for k, v in resource.get("spec", {}).items() if k != "suspend"}
                ),
                "inventory": [
                    row.get("id")
                    for row in resource.get("status", {}).get("inventory", {}).get("entries", [])
                ],
            }

        def parents(resource: Mapping[str, Any], *, require: bool) -> None:
            metadata = resource.get("metadata", {})
            annotations = metadata.get("annotations", {})
            labels = metadata.get("labels", {})
            owner = annotations.get("meta.helm.sh/release-name")
            namespace = annotations.get("meta.helm.sh/release-namespace")
            if owner:
                matches = [
                    hr
                    for hr in releases
                    if (hr.get("spec", {}).get("releaseName") or hr["metadata"]["name"]) == owner
                    and (hr.get("spec", {}).get("targetNamespace") or hr["metadata"]["namespace"])
                    == namespace
                ]
                if len(matches) != 1:
                    raise RuntimeError("source ActiveChecks Helm writer is ambiguous")
                add(matches[0], "helmrelease")
            elif require:
                raise RuntimeError("source ActiveChecks have no provable Helm writer")
            parent = labels.get("kustomize.toolkit.fluxcd.io/name")
            parent_ns = labels.get("kustomize.toolkit.fluxcd.io/namespace")
            if parent:
                if not parent_ns:
                    raise RuntimeError("source check Kustomization namespace is missing")
                add(
                    self._get("kustomization.kustomize.toolkit.fluxcd.io", parent, parent_ns),
                    "kustomization.kustomize.toolkit.fluxcd.io",
                )

        for check in checks:
            parents(check, require=True)
        allowed_inventory = {
            f"{namespace}_{name}_helm.toolkit.fluxcd.io_HelmRelease"
            if kind == "helmrelease"
            else f"{namespace}_{name}_kustomize.toolkit.fluxcd.io_Kustomization"
            for kind, namespace, name in writers
        }
        for writer in writers.values():
            if writer["kind"] != "helmrelease" and (
                not writer["inventory"] or not set(writer["inventory"]) <= allowed_inventory
            ):
                raise RuntimeError(
                    "source checks share an external Flux writer; separate its ownership before upgrading"
                )
        return list(writers.values())

    def complete_source_handoff(
        self, target_writers: tuple[tuple[str, str], ...]
    ) -> dict[str, Any]:
        """Prove target adoption and explicitly retain retired source parents suspended."""
        dispositions = []
        for writer in self.state.get("sourceWriters", []):
            live = self._get(writer["kind"], writer["name"], writer["namespace"])
            identity = (writer["namespace"], writer["name"])
            if not live:
                if writer["kind"] != "helmrelease" or identity in target_writers:
                    raise RuntimeError("source writer disappeared without target adoption")
                disposition = "retired"
            else:
                if live.get("metadata", {}).get("uid") != writer["uid"]:
                    raise RuntimeError("source writer identity changed during handoff")
                spec = live.get("spec", {})
                if writer["kind"] == "helmrelease":
                    if identity not in target_writers or spec.get("suspend", False):
                        raise RuntimeError(
                            "source Helm writer has not transferred to the target release graph"
                        )
                    disposition = "adopted-target"
                else:
                    if (
                        spec.get("suspend") is not True
                        or checks_digest({k: v for k, v in spec.items() if k != "suspend"})
                        != writer["contract"]
                    ):
                        raise RuntimeError("retired source Flux parent escaped its frozen boundary")
                    disposition = "retired-suspended"
            dispositions.append(
                {
                    "kind": writer["kind"],
                    "namespace": writer["namespace"],
                    "name": writer["name"],
                    "uid": writer["uid"],
                    "disposition": disposition,
                }
            )
        result = {
            "targetWriters": [list(row) for row in sorted(target_writers)],
            "writers": dispositions,
        }
        if self.state.get("sourceHandoff", result) != result:
            raise RuntimeError("source writer disposition changed after handoff")
        self.state["sourceHandoff"] = result
        self._save()
        return result

    def _expand(self, expression: str) -> tuple[str, ...]:
        if not re.fullmatch(r"[A-Za-z0-9_.\[\],-]+", expression):
            raise RuntimeError("invalid Slurm node expression")
        if re.fullmatch(r"[A-Za-z0-9_.-]+", expression):
            return (expression,)
        return tuple(
            sorted(
                filter(
                    None,
                    self.slurm("scontrol show hostnames " + shlex.quote(expression)).splitlines(),
                )
            )
        )

    def _reservation(self, reservation: str) -> dict[str, Any]:
        raw = self.slurm(
            "env SLURM_TIME_FORMAT=standard TZ=UTC scontrol show reservation "
            + _identifier(reservation)
            + " -o"
        )
        fields = _record_fields(raw)
        if fields.get("ReservationName") != reservation:
            raise RuntimeError("acceptance reservation is missing or ambiguous")
        try:
            for key in ("StartTime", "EndTime"):
                value = fields.get(key, "")
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", value):
                    raise ValueError("incomplete timestamp")
                datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
        except ValueError as exc:
            raise RuntimeError(
                "acceptance reservation timestamps are incomplete or invalid"
            ) from exc
        flags = set(fields.get("Flags", "").split(","))
        if not {"MAINT", "IGNORE_JOBS"} <= flags or flags - {
            "MAINT",
            "IGNORE_JOBS",
            "SPEC_NODES",
            "ALL_NODES",
        }:
            raise RuntimeError("acceptance reservation flags changed")
        # Slurm maps an INFINITE/UNLIMITED request to YEAR_SECONDS; scontrol
        # prints the resulting duration, not the input keyword.
        if fields.get("State") != "ACTIVE" or fields.get("Duration") != "365-00:00:00":
            raise RuntimeError("acceptance requires an active unlimited maintenance reservation")
        if any(
            fields.get(key, "(null)") not in {"", "(null)"}
            for key in ("Accounts", "Groups", "PartitionName", "Licenses", "BurstBuffer")
        ):
            raise RuntimeError(
                "acceptance reservation has foreign authorization or partial resource scope"
            )
        users = set(fields.get("Users", "").split(","))
        if users not in ({"root"}, {"root", "soperatorchecks"}):
            raise RuntimeError("acceptance reservation authorizes another user")
        nodes = self._expand(fields.get("Nodes", ""))
        inventory = self._node_inventory()
        if set(nodes) != set(inventory) or fields.get("NodeCnt") != str(len(nodes)):
            raise RuntimeError("acceptance reservation does not cover the full Slurm inventory")
        if fields.get("CoreCnt") != str(sum(row["cores"] for row in inventory.values())):
            raise RuntimeError("acceptance reservation does not cover every worker core")
        record = {key: value for key, value in fields.items() if key != "Users"}
        fingerprint = checks_digest(record)
        if self.state.get("reservationFingerprint", fingerprint) != fingerprint:
            raise RuntimeError("acceptance reservation contract changed")
        return {
            "name": reservation,
            "nodes": list(nodes),
            "users": sorted(users),
            "flags": sorted(fields["Flags"].split(",")),
            "fingerprint": fingerprint,
        }

    def _node_inventory(self) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        for line in self.slurm("scontrol show nodes -o").splitlines():
            fields = dict(re.findall(r"(\w+)=(\S+)", line))
            name = fields.get("NodeName", "")
            if not name or name in result:
                raise RuntimeError("ambiguous Slurm worker inventory")
            try:
                cores = int(fields["CoresPerSocket"]) * int(fields["Sockets"])
                tres = dict(item.split("=", 1) for item in fields["CfgTRES"].split(","))
                gpus = int(tres.get("gres/gpu", "0"))
            except (ValueError, KeyError) as exc:
                raise RuntimeError("Slurm worker resource inventory is incomplete") from exc
            if cores <= 0 or gpus < 0:
                raise RuntimeError("Slurm worker resource inventory is invalid")
            result[_identifier(name)] = {"cores": cores, "gpus": gpus}
        if not result:
            raise RuntimeError("Slurm worker inventory is empty")
        return result

    def prepare_reservation(self, existing: str, *, installing: bool) -> str:
        if self.state.get("scheduleRelease"):
            raise RuntimeError("released check maintenance cannot be entered again")
        if existing:
            observed = self._reservation(existing)
            self.state["reservation"] = existing
            self.state["reservationFingerprint"] = observed["fingerprint"]
            self._save()
            return existing
        if not installing:
            raise RuntimeError("upgrade acceptance requires the retained scheduling reservation")
        name = "cxcli_" + self.operation_id[:16]
        names = set(
            re.findall(r"ReservationName=(\S+)", self.slurm("scontrol show reservations -o"))
        )
        if not self.state.get("installReservationIntent"):
            if name in names:
                raise RuntimeError("initial acceptance reservation collides with existing state")
            self.state["installReservationIntent"] = True
            self.state["reservation"] = name
            self._save()
        if name not in names:
            self.authority()
            self.slurm(
                f"scontrol create ReservationName={name} StartTime=now Duration=UNLIMITED Nodes=ALL Users=root Flags=MAINT,IGNORE_JOBS"
            )
        self.state["reservationFingerprint"] = self._reservation(name)["fingerprint"]
        self._save()
        return name

    def adopt_install_reservation(self, handoff: Mapping[str, Any]) -> None:
        """Carry only sealed maintenance ownership into a fresh install epoch."""
        expected = {
            "predecessorOperation": handoff["operation"],
            "predecessorReceiptSha256": handoff["receiptSha256"],
            "reservation": handoff["reservation"],
            "fingerprint": handoff["fingerprint"],
            "predecessorPolicy": handoff["predecessorPolicy"],
            "policy": handoff["policy"],
        }
        if handoff.get("policy") != self.policy.sha256:
            raise RuntimeError("install maintenance handoff policy changed")
        if self.state.get("installReservationHandoff") == expected:
            if (
                self.state.get("installReservationIntent") is not True
                or self.state.get("reservation") != expected["reservation"]
                or self.state.get("reservationFingerprint") != expected["fingerprint"]
            ):
                raise RuntimeError("install maintenance handoff lost its retained identity")
            return
        if (
            self.state.get("phase") != "planned"
            or self.state.get("jobs") != {}
            or any(
                key in self.state
                for key in ("acceptance", "reservation", "installReservationHandoff")
            )
            or handoff["operation"] == self.state["operation"]
        ):
            raise RuntimeError("install maintenance handoff requires an unstarted successor")
        observed = self._reservation(str(handoff["reservation"]))
        if observed["users"] != ["root"] or observed["fingerprint"] != handoff["fingerprint"]:
            raise RuntimeError("install maintenance handoff reservation changed")
        self.state.update(
            installReservationIntent=True,
            reservation=observed["name"],
            reservationFingerprint=observed["fingerprint"],
            installReservationHandoff=expected,
        )
        self._save()

    @staticmethod
    def _initial_partition_states(partitions: list[Mapping[str, Any]]) -> dict[str, str]:
        states: dict[str, str] = {}
        for partition in partitions:
            name = _identifier(str(partition["name"]))
            matches = re.findall(r"(?:^|\s)State=(\S+)", str(partition.get("config") or ""))
            state = matches[0] if matches else "UP"
            if (
                name in states
                or len(matches) > 1
                or state not in {"UP", "DOWN", "DRAIN", "INACTIVE"}
            ):
                raise RuntimeError("unsupported initial partition restoration")
            states[name] = state
        if not states:
            raise RuntimeError("initial partition restoration has no configured partitions")
        return states

    def prepare_maintenance(
        self, existing: str, *, installing: bool, partitions: list[Mapping[str, Any]]
    ) -> Mapping[str, Any]:
        states = self._initial_partition_states(partitions) if installing else {}
        handoff = self.state.get("installReservationHandoff")
        if handoff:
            if existing and existing != handoff["reservation"]:
                raise RuntimeError("install maintenance reservation ownership differs")
            existing = handoff["reservation"]
        self.prepare_reservation(existing, installing=installing)
        if self.lifecycle is not None:
            self.lifecycle.maintenance()
            return self.verify_maintenance(installing=installing, partitions=partitions)
        for name, state in states.items():
            self.authority()
            self.slurm(f"scontrol update PartitionName={name} State={state}")
        return self.verify_maintenance(installing=installing, partitions=partitions)

    def verify_maintenance(
        self, *, installing: bool, partitions: list[Mapping[str, Any]]
    ) -> Mapping[str, Any]:
        if not self.state.get("reservation") or not self.state.get("reservationFingerprint"):
            raise RuntimeError("check maintenance reservation is not bound")
        reservation = self._reservation(self.state["reservation"])
        states = self._initial_partition_states(partitions) if installing else {}
        if self.lifecycle is not None:
            self.lifecycle.admission.verify()
            states = {
                name: "UP"
                if name == "hidden" and self.lifecycle.admission.state.get("checksOpen")
                else "DOWN"
                for name in self._initial_partition_states(partitions)
            }
        self.verify_partition_states(states)
        return {"reservation": reservation, "partitions": states}

    def verify_partition_states(self, states: Mapping[str, str]) -> None:
        if states:
            observed: dict[str, str] = {}
            for line in self.slurm("scontrol show partition -o").splitlines():
                fields = dict(re.findall(r"(\w+)=(\S+)", line))
                name = fields.get("PartitionName", "")
                if not name or name in observed:
                    raise RuntimeError("ambiguous live Slurm partition inventory")
                observed[name] = fields.get("State", "")
            if observed != states:
                raise RuntimeError("initial Slurm partition restoration is incomplete")

    def release_install_reservation(self, proof: Mapping[str, Any]) -> None:
        if not self.state.get("installReservationIntent"):
            raise RuntimeError("checks executor does not own the installation reservation")
        release = self.state.get("scheduleRelease", {})
        if release.get("status") != "intent" or release.get("binding") != proof:
            raise RuntimeError("initial schedule release intent is missing or changed")
        name = self.state["reservation"]
        names = set(
            re.findall(r"ReservationName=(\S+)", self.slurm("scontrol show reservations -o"))
        )
        if name in names:
            self._reservation(name)
            self.authority()
            self.slurm(f"scontrol delete ReservationName={name}")
        if name in set(
            re.findall(r"ReservationName=(\S+)", self.slurm("scontrol show reservations -o"))
        ):
            raise RuntimeError("initial acceptance reservation release is incomplete")

    def verify_install_reservation_released(self, proof: Mapping[str, Any]) -> None:
        if (
            not self.state.get("installReservationIntent")
            or self.state.get("scheduleRelease", {}).get("binding") != proof
            or proof.get("reservation") != self.state.get("reservation")
        ):
            raise RuntimeError("initial reservation release ownership changed")
        if self.state["reservation"] in set(
            re.findall(r"ReservationName=(\S+)", self.slurm("scontrol show reservations -o"))
        ):
            raise RuntimeError("initial maintenance reservation has not been released")

    def _verify_isolation(self, *, include_pending: bool = False) -> None:
        owned_names = set(self.state["jobs"])
        raw = self.slurm(
            "squeue -h -t RUNNING,COMPLETING,CONFIGURING,SUSPENDED,STOPPED -o '%i|%u|%j'"
        )
        if include_pending:
            raw += "\n" + self.slurm("squeue -h -u soperatorchecks -o '%i|%u|%j'")
        for row in raw.splitlines():
            if not row.strip():
                continue
            fields = row.split("|")
            probe_running = any(
                entry["check"] == "wait-for-soperatorchecks-srun-ready"
                and entry["status"] != "complete"
                for entry in self.state["jobs"].values()
            )
            allowed_probe = (
                len(fields) == 3 and fields[2] == "test-controller-is-ready" and probe_running
            )
            if (
                len(fields) != 3
                or fields[1] != "soperatorchecks"
                or (fields[2] not in owned_names and not allowed_probe)
            ):
                raise RuntimeError("customer or unowned privileged work overlaps check acceptance")
            details = self.slurm("scontrol show job " + _identifier(fields[0]) + " -o")
            if not re.search(
                r"(?:^|\s)Reservation=" + re.escape(self.state["reservation"]) + r"(?:\s|$)",
                details,
            ):
                raise RuntimeError("running check is outside the operation reservation")

    def _jobs(self) -> dict[str, Mapping[str, Any]]:
        return {row["metadata"]["name"]: row for row in self._get("jobs").get("items", [])}

    def _accounting(self, job_ids: list[str]) -> str:
        if any(not _JOB_ID.fullmatch(job_id) for job_id in job_ids):
            raise RuntimeError("invalid Soperator acceptance Slurm IDs")
        ids = sorted(set(job_ids))
        return "\n".join(
            self.slurm(
                "sacct -n -P -j "
                + ",".join(ids[offset : offset + 200])
                + " --format=JobIDRaw,State,ExitCode,NodeList,User,JobName%100,Reservation%100,AllocTRES%300"
            )
            for offset in range(0, len(ids), 200)
        )

    def verify_acceptance(
        self, *, released_parent: SoperatorChecksExecution | None = None
    ) -> dict[str, Any]:
        from .soperator_checks_handoff import released_diagnostic_evidence

        if self.state.get("phase") not in {"accepted", "restored"}:
            raise RuntimeError("fresh check acceptance is incomplete")
        sealed = released_diagnostic_evidence(self, parent=released_parent)
        if self.lifecycle is not None:
            self.lifecycle.passive.verify_acceptance(sealed=sealed)
        allocation = self.state["acceptance"]
        for guard in self.state.get("creationGuards", {}).values():
            live = self._get("job", guard["name"])
            if live.get("metadata", {}).get("uid") != guard["uid"]:
                raise RuntimeError("upstream creation trigger guard disappeared")
        expected: set[tuple[str, str]] = set()
        for rule in self.policy.required:
            eligible = allocation["gpuWorkers"] if rule.requires_gpu else allocation["workers"]
            selected = (
                eligible
                if rule.each_worker
                else eligible[:1]
                if rule.check_type == "slurmJob"
                else [""]
            )
            expected.update((rule.name, worker) for worker in selected)
        if {(entry["check"], entry["worker"]) for entry in self.state["jobs"].values()} != expected:
            raise RuntimeError("acceptance evidence does not cover every required check and worker")
        live_jobs = self._jobs()
        accounting = self._accounting(
            [
                job_id
                for entry in self.state["jobs"].values()
                for job_id in entry.get("slurmIds", [])
            ]
        )
        for name, entry in self.state["jobs"].items():
            job = live_jobs.get(name, {})
            if (
                job.get("metadata", {}).get("uid") != entry.get("uid")
                or entry.get("status") != "complete"
            ):
                raise RuntimeError("acceptance Job identity or completion evidence changed")
            if not any(
                c.get("type") == "Complete" and c.get("status") == "True"
                for c in job.get("status", {}).get("conditions", [])
            ):
                raise RuntimeError("acceptance Job is no longer complete")
            if not self._target_template(
                job.get("spec", {}).get("template", {})
            ) or job_execution_digest(job) != entry.get("execution"):
                raise RuntimeError("acceptance Job executable changed from its reviewed template")
            self._verify_execution_authority(entry["check"], entry["epoch"], generation=False)
            if (
                self.policy.execution_specs[entry["check"]]["checkType"] == "slurmJob"
                and len(entry.get("slurmIds", [])) != 1
            ):
                raise RuntimeError("acceptance Slurm submission evidence is missing or ambiguous")
            for job_id in entry.get("slurmIds", []):
                result = slurm_acceptance_result(
                    accounting,
                    job_id=job_id,
                    name=name,
                    reservation=self.state["acceptance"]["reservation"],
                    expected_nodes=(entry["worker"],) if entry["worker"] else (),
                    expected_gpus=entry.get("gpuCount", 0),
                    expand_nodes=self._expand,
                )
                if result is None or result != entry.get("slurmResult"):
                    raise RuntimeError("acceptance Slurm evidence is unavailable or changed")
                if sealed:
                    verdict = entry.get("diagnosticResult")
                    if (
                        not isinstance(verdict, Mapping)
                        or verdict.get("status") != "PASS"
                        or not re.fullmatch(
                            r"sha256:[0-9a-f]{64}", str(verdict.get("outputSha256"))
                        )
                    ):
                        raise RuntimeError("released native diagnostic evidence is invalid")
                else:
                    verdict = read_native_verdict(
                        self.slurm, check=entry["check"], name=name, result=result
                    )
                    if verdict != entry.get("diagnosticResult"):
                        raise RuntimeError(
                            "acceptance native diagnostic evidence is missing or changed"
                        )
        if not self.state["jobs"]:
            raise RuntimeError("acceptance evidence is empty")
        return {"status": "accepted", "policy": self.policy.sha256, "jobs": len(self.state["jobs"])}

    def _script_identity(self, name: str) -> dict[str, str] | None:
        expected = self.policy.execution_specs.get(name)
        if not expected:
            raise RuntimeError("verified upstream execution contract is unavailable")
        if expected["checkType"] != "slurmJob":
            return None
        script = self._get("configmap", "sbatch-script-" + expected["name"])
        uid = script.get("metadata", {}).get("uid")
        data = script.get("data", {}).get("sbatch.sh")
        if not uid or data != expected["slurmJobSpec"].get("sbatchScript"):
            raise RuntimeError("upstream diagnostic script differs from the verified Helm render")
        return {"uid": uid, "sha256": checks_digest(data)}

    def _verify_execution_authority(
        self, name: str, epoch: Mapping[str, Any], *, generation: bool
    ) -> None:
        check = self._get("activecheck", name)
        metadata = check.get("metadata", {})
        if metadata.get("uid") != epoch["uid"] or (
            generation and metadata.get("generation") != epoch["generation"]
        ):
            raise RuntimeError("ActiveCheck execution epoch changed during acceptance")
        verify_check_spec(self.policy.execution_specs[name], check.get("spec", {}))
        if self._script_identity(name) != epoch.get("script"):
            raise RuntimeError("upstream diagnostic script identity changed")

    def _target_template(self, template: Mapping[str, Any]) -> bool:
        containers = template.get("spec", {}).get("containers", [])
        return len(containers) == 1 and any(
            item.get("name") == CHECKS_POLICY_ENV and item.get("value") == self.policy.sha256
            for item in containers[0].get("env", [])
        )

    def _target_cronjob(self, name: str) -> Mapping[str, Any] | None:
        cron = self._get("cronjob", name)
        if not self._target_template(
            cron.get("spec", {}).get("jobTemplate", {}).get("spec", {}).get("template", {})
        ):
            return None
        if cron.get("spec", {}).get("suspend") is not True:
            raise RuntimeError("target checks schedule is not deferred")
        expected = self.policy.execution_specs.get(name)
        if not expected:
            raise RuntimeError("verified upstream execution contract is unavailable")
        check = self._get("activecheck", name)
        verify_native_template(expected, check, cron["spec"]["jobTemplate"]["spec"]["template"])
        self._script_identity(name)
        return cron

    def verify_deferred_diagnostics(self, *, allow_acceptance: bool = False) -> None:
        from .soperator_checks_scheduling import verify_deferred_diagnostics

        verify_deferred_diagnostics(self, allow_acceptance=allow_acceptance)

    def accept(
        self,
        *,
        reservation: str,
        workers: tuple[str, ...],
        gpu_workers: tuple[str, ...] = (),
        before_jobs: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if self.state.get("phase") in {"accepted", "restored"}:
            return self.verify_acceptance()
        self.verify_deferred_diagnostics(allow_acceptance=self.state.get("phase") == "acceptance")
        if not workers or len(set(workers)) != len(workers):
            raise RuntimeError("acceptance requires an exact nonempty worker inventory")
        workers = tuple(sorted(_identifier(n) for n in workers))
        observed = self._reservation(reservation)
        if not set(workers) <= set(observed["nodes"]):
            raise RuntimeError("acceptance reservation does not cover every worker")
        if not set(gpu_workers) <= set(workers):
            raise RuntimeError("GPU acceptance inventory is outside the worker inventory")
        inventory = self._node_inventory()
        if set(inventory) != set(workers) or {
            name for name, row in inventory.items() if row["gpus"]
        } != set(gpu_workers):
            raise RuntimeError("live Slurm workers or GPU inventory differs from the frozen target")
        expected = {
            "resources": inventory,
            "reservation": reservation,
            "workers": list(workers),
            "gpuWorkers": sorted(gpu_workers),
            "reservationNodes": observed["nodes"],
            "flags": observed["flags"],
        }
        if "acceptance" in self.state and self.state["acceptance"] != expected:
            raise RuntimeError("recovery-required: acceptance allocation changed")
        self.state["acceptance"] = expected
        self.state["phase"] = "acceptance"
        self._save()
        principal = self.slurm("id -u soperatorchecks").strip()
        if not principal.isdecimal() or int(principal) == 0:
            raise RuntimeError("acceptance requires the non-root upstream checks principal")
        if self.state.get("principalUid", principal) != principal:
            raise RuntimeError("acceptance principal identity changed")
        self.state["principalUid"] = principal
        self._save()
        self._verify_isolation()
        self.authority()
        self.slurm(f"scontrol update ReservationName={reservation} Users=root,soperatorchecks")
        if self._reservation(reservation)["users"] != ["root", "soperatorchecks"]:
            raise RuntimeError("acceptance principal authorization did not converge")
        if before_jobs is not None:
            before_jobs()
        for rule in self.policy.required:

            def target_cron(rule: CheckRule = rule) -> Mapping[str, Any] | None:
                return self._target_cronjob(rule.name)

            cron = self._until(
                target_cron,
                f"target {rule.name} template convergence",
            )
            check = self._get("activecheck", rule.name)
            epoch = {
                "uid": check.get("metadata", {}).get("uid"),
                "generation": check.get("metadata", {}).get("generation"),
                "template": checks_digest(cron["spec"]["jobTemplate"]),
                "script": self._script_identity(rule.name),
            }
            if not epoch["uid"] or not epoch["generation"]:
                raise RuntimeError("ActiveCheck target generation is unavailable")
            eligible = gpu_workers if rule.requires_gpu else workers
            if not eligible:
                raise RuntimeError("required check has no eligible workers")
            retire_unpinned_jobs(
                self,
                rule=rule,
                cron=cron,
                epoch=epoch,
                reservation=reservation,
                workers=tuple(eligible),
            )
            selected = (
                eligible
                if rule.each_worker
                else (eligible[0],)
                if rule.check_type == "slurmJob"
                else ("",)
            )
            for offset in range(0, len(selected), rule.concurrency):
                pending: list[tuple[dict[str, Any], str]] = []
                live_jobs = self._jobs()
                submissions: list[dict[str, Any]] = []
                for worker in selected[offset : offset + rule.concurrency]:
                    # The upstream Slurm creation trigger is guarded by this Job's existence,
                    # not by slurmJobsStatus. Use the native initial identity for one real
                    # acceptance execution when no upstream initial Job exists.
                    job_name = ""
                    prior = [
                        (name, entry)
                        for name, entry in self.state["jobs"].items()
                        if entry["check"] == rule.name and entry["worker"] == worker
                    ]
                    if prior:
                        job_name = prior[0][0]
                    elif rule.check_type == "slurmJob" and worker == selected[0]:
                        initial_name = rule.name + "-initial-run"
                        initial = live_jobs.get(initial_name)
                        if not initial:
                            job_name = initial_name
                        else:
                            guards = self.state.setdefault("creationGuards", {})
                            guards[rule.name] = {
                                "name": initial_name,
                                "uid": initial["metadata"]["uid"],
                            }
                            self._save()
                    gpu_count = inventory[worker]["gpus"] if rule.requires_gpu and worker else 0
                    job = native_acceptance_job(
                        cron,
                        rule=rule,
                        operation_id=self.operation_id,
                        reservation=reservation,
                        worker=worker,
                        job_name=job_name,
                        gpu_count=gpu_count,
                        sbatch_script=self.policy.execution_specs[rule.name]
                        .get("slurmJobSpec", {})
                        .get("sbatchScript"),
                    )
                    name = job["metadata"]["name"]
                    entry = self.state["jobs"].get(name)
                    identity = {
                        "check": rule.name,
                        "worker": worker,
                        "gpuCount": gpu_count,
                        "epoch": epoch,
                        "manifest": checks_digest(job),
                        "execution": job_execution_digest(job),
                    }
                    if entry is not None and any(entry.get(k) != v for k, v in identity.items()):
                        raise RuntimeError("recovery-required: acceptance job identity changed")
                    live = live_jobs.get(name)
                    if entry is None:
                        if live:
                            raise RuntimeError("unowned acceptance Job collision")
                        entry = {**identity, "status": "submit-intent"}
                        self.state["jobs"][name] = entry
                        self._save()
                    if not live:
                        if entry.get("uid") or entry.get("submitted"):
                            raise RuntimeError(
                                "recovery-required: submitted acceptance Job is missing"
                            )
                        entry["submitted"] = True
                        submissions.append(job)
                    pending.append((entry, name))
                if submissions:
                    if self.lifecycle is not None:
                        self.lifecycle.admission.verify()
                    # One write-ahead batch and one API request. Partial or uncertain
                    # creation is recovered by exact names; missing Jobs are never resubmitted.
                    self._save()
                    self.authority()
                    self.kube(
                        ["create", "-f", "-", "-o", "json"],
                        {"apiVersion": "v1", "kind": "List", "items": submissions},
                    )
                    live_jobs = self._jobs()
                for entry, name in pending:
                    live = live_jobs.get(name, {})
                    if live.get("metadata", {}).get("labels", {}).get(_LABEL) != self.operation_id:
                        raise RuntimeError("acceptance Job ownership changed")
                    uid = live.get("metadata", {}).get("uid")
                    if not uid or (entry.get("uid") and entry["uid"] != uid):
                        raise RuntimeError("acceptance Job UID changed")
                    if job_execution_digest(live) != entry["execution"]:
                        raise RuntimeError(
                            "created acceptance Job executable differs from reviewed template"
                        )
                    entry["uid"] = uid
                self._save()

                def completed(pending=pending, rule=rule, epoch=epoch) -> bool:
                    self._verify_execution_authority(rule.name, epoch, generation=True)
                    self._verify_isolation()
                    barrier = self._reservation(reservation)
                    if (
                        barrier["nodes"] != expected["reservationNodes"]
                        or barrier["flags"] != expected["flags"]
                        or barrier["users"] != ["root", "soperatorchecks"]
                    ):
                        raise RuntimeError("acceptance reservation drifted")
                    complete = True
                    live_jobs = self._jobs()
                    accounting_ids = [
                        str(
                            live_jobs.get(name, {})
                            .get("metadata", {})
                            .get("annotations", {})
                            .get("slurm-job-id", "")
                        )
                        for _, name in pending
                    ]
                    accounting = (
                        self._accounting(
                            [job_id for job_id in accounting_ids if _JOB_ID.fullmatch(job_id)]
                        )
                        if rule.check_type == "slurmJob"
                        else ""
                    )
                    for entry, name in pending:
                        live = live_jobs.get(name, {})
                        if (
                            live.get("metadata", {}).get("uid") != entry["uid"]
                            or job_execution_digest(live) != entry["execution"]
                        ):
                            raise RuntimeError("acceptance Job disappeared or executable changed")
                        conditions = live.get("status", {}).get("conditions", [])
                        if any(
                            c.get("type") == "Failed" and c.get("status") == "True"
                            for c in conditions
                        ):
                            raise RuntimeError(
                                "acceptance Job failed; inspect exact submission before recovery"
                            )
                        if not any(
                            c.get("type") == "Complete" and c.get("status") == "True"
                            for c in conditions
                        ):
                            complete = False
                            continue
                        if rule.check_type == "slurmJob":
                            raw_ids = (
                                live.get("metadata", {})
                                .get("annotations", {})
                                .get("slurm-job-id", "")
                            )
                            ids = raw_ids.split(",")
                            if len(ids) != 1 or not _JOB_ID.fullmatch(ids[0]):
                                raise RuntimeError(
                                    "recovery-required: acceptance Slurm submission is ambiguous"
                                )
                            if entry.get("slurmIds") and entry["slurmIds"] != ids:
                                raise RuntimeError("acceptance Slurm job identity changed")
                            entry["slurmIds"] = ids
                            self._save()
                            result = slurm_acceptance_result(
                                accounting,
                                job_id=ids[0],
                                name=name,
                                reservation=reservation,
                                expected_nodes=(entry["worker"],) if entry["worker"] else (),
                                expected_gpus=entry.get("gpuCount", 0),
                                expand_nodes=self._expand,
                            )
                            if result is None:
                                complete = False
                                continue
                            verdict = read_native_verdict(
                                self.slurm, check=rule.name, name=name, result=result
                            )
                            if entry.get("status") == "complete" and verdict != entry.get(
                                "diagnosticResult"
                            ):
                                raise RuntimeError(
                                    "acceptance native diagnostic evidence is missing or changed"
                                )
                            entry["diagnosticResult"] = verdict
                            entry["slurmResult"] = result
                            if self.lifecycle is not None:
                                from .soperator_passive_checks import PassivePending

                                try:
                                    self.lifecycle.passive.collect_job(entry)
                                except PassivePending:
                                    complete = False
                                    continue
                        entry["status"] = "complete"
                    self._save()
                    return complete

                self._until(completed, f"fresh {rule.name} acceptance")

            def controller_consumed(rule=rule, epoch=epoch) -> bool:
                self._verify_execution_authority(rule.name, epoch, generation=True)
                check = self._get("activecheck", rule.name)
                names = {
                    name
                    for name, entry in self.state["jobs"].items()
                    if entry["check"] == rule.name
                }
                if rule.check_type == "slurmJob":
                    status = check.get("status", {}).get("slurmJobsStatus", {})
                    ids = {
                        job_id
                        for name, entry in self.state["jobs"].items()
                        if name in names
                        for job_id in entry.get("slurmIds", [])
                    }
                    return bool(
                        status.get("lastTransitionTime")
                        and str(status.get("lastRunId")) in ids
                        and status.get("lastRunName") in names
                        and status.get("lastRunStatus") == "Complete"
                    )
                status = check.get("status", {}).get("k8sJobsStatus", {})
                return bool(
                    status.get("lastTransitionTime")
                    and status.get("lastJobName") in names
                    and status.get("lastJobStatus") == "Complete"
                )

            self._until(
                controller_consumed, f"upstream controller to observe {rule.name} acceptance"
            )
        self.state["phase"] = "accepted"
        self._save()
        return {
            "status": "accepted",
            "policy": self.policy.sha256,
            "workers": list(workers),
            "jobs": len(self.state["jobs"]),
        }

    def close_authorization(self) -> dict[str, Any]:
        if self.state.get("phase") not in {"accepted", "restored"}:
            raise RuntimeError("cannot close check authorization before acceptance")
        acceptance = self.state["acceptance"]
        reservation = acceptance["reservation"]
        live = self._reservation(reservation)
        if live["nodes"] != acceptance["reservationNodes"] or live["flags"] != acceptance["flags"]:
            raise RuntimeError("acceptance reservation changed before restoration")
        if self.slurm("id -u soperatorchecks").strip() != self.state.get("principalUid"):
            raise RuntimeError("acceptance principal changed before authorization closure")
        self.authority()
        self.slurm(f"scontrol update ReservationName={reservation} Users=root")
        if self._reservation(reservation)["users"] != ["root"]:
            raise RuntimeError("acceptance reservation authorization restoration is pending")
        return {"status": "authorization-closed", "policy": self.policy.sha256}
