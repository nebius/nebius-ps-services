"""User admission remains closed independently of the native checks reservation."""

from __future__ import annotations

import re
import shlex
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .slurm_jobs import (
    SlurmPartitionPauseRecord,
    SlurmPartitionState,
    canonical_slurm_partition_migration_record,
    canonical_slurm_partition_record,
    parse_scontrol_show_job_record,
    slurm_job_control_is_held,
    slurm_partition_migration_fields_match,
    slurm_partition_record_fingerprint,
)
from .soperator_checks_phase import (
    admission_partition_configuration,
    restored_partition_configuration,
)
from .soperator_checks_policy import checks_digest
from .soperator_slurm_recovery import _record_fields

if TYPE_CHECKING:
    from .soperator_checks import SoperatorChecksExecution


def diagnostic_partition_preimages(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[SlurmPartitionPauseRecord, ...]:
    """Transfer the complete frozen inventory, including originally closed partitions."""
    result = []
    for row in rows:
        original = SlurmPartitionState(**row)
        fields = _record_fields(original.record)
        fields["State"] = "DOWN"
        applied = canonical_slurm_partition_record(" ".join(f"{k}={v}" for k, v in fields.items()))
        result.append(
            SlurmPartitionPauseRecord(
                original.name,
                original.state,
                original.record,
                original.record_fingerprint,
                "DOWN",
                applied,
                slurm_partition_record_fingerprint(applied),
            )
        )
    if not result or len({row.partition for row in result}) != len(result):
        raise RuntimeError("diagnostic admission requires a complete unique partition preimage")
    return tuple(sorted(result, key=lambda row: row.partition))


class ChecksAdmission:
    def __init__(
        self,
        checks: SoperatorChecksExecution,
        preimages: Callable[[], tuple[SlurmPartitionPauseRecord, ...]] = lambda: (),
    ) -> None:
        self.checks = checks
        self.preimages = preimages

    @property
    def state(self) -> dict[str, Any]:
        return self.checks.state.setdefault("admission", {})

    def _partitions(self) -> dict[str, dict[str, str]]:
        result = {}
        for line in self.checks.slurm("scontrol show partition -o").splitlines():
            row = _record_fields(line)
            name = row.get("PartitionName", "")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name in result:
                raise RuntimeError("ambiguous admission partition inventory")
            result[name] = row
        if not result or "hidden" not in result:
            raise RuntimeError("native checks admission partition is unavailable")
        return result

    def _principal(self) -> None:
        group = self.checks.slurm("getent group soperatorchecks").strip().split(":")
        uid = self.checks.slurm("id -u soperatorchecks").strip()
        gid = self.checks.slurm("id -g soperatorchecks").strip()
        if (
            len(group) != 4
            or group[0] != "soperatorchecks"
            or group[2] != gid
            or not uid.isdecimal()
            or int(uid) == 0
        ):
            raise RuntimeError("native checks require an exclusive non-root checks group")
        members = set(filter(None, group[3].split(",")))
        passwd = self.checks.slurm("getent passwd")
        for line in passwd.splitlines():
            fields = line.split(":")
            if len(fields) != 7:
                raise RuntimeError("checks group membership cannot be verified")
            if fields[3] == gid:
                members.add(fields[0])
        if members != {"soperatorchecks"}:
            raise RuntimeError("ordinary users share the native checks admission group")

    def _job(self, job_id: str) -> Any:
        if not re.fullmatch(r"[0-9][0-9_+]*", job_id):
            raise RuntimeError("unsupported admission job identity")
        raw = self.checks.slurm("scontrol show job " + job_id + " -o").strip()
        return parse_scontrol_show_job_record(raw) if raw else None

    @staticmethod
    def _owned(row: dict[str, str]) -> dict[str, str]:
        return _record_fields(
            canonical_slurm_partition_migration_record(
                " ".join(f"{key}={value}" for key, value in row.items())
            )
        )

    def _guard(self, current: dict[str, dict[str, str]]) -> None:
        expected = self.state.get("postimages", {})
        if set(current) != set(self.state["partitions"]) or set(expected) != set(current):
            raise RuntimeError("admission partition scope changed")
        release = self._release_configuration()
        for name, row in current.items():
            if release and self._owned(row) in (release["before"][name], release["after"][name]):
                continue
            if name in expected and self._owned(row) != expected[name]:
                intent = self.state.get("partitionIntent", {})
                if intent.get("name") != name or self._owned(row) != intent.get("after"):
                    raise RuntimeError("admission partition was independently changed")

    def _release_configuration_binding(self) -> dict[str, str]:
        return {
            "authorization": checks_digest(self.state.get("authorization")),
            "policy": self.checks.policy.sha256,
            "readyConfiguration": checks_digest(
                restored_partition_configuration(
                    self.checks.policy.partitions, self.state["partitions"]
                )
            ),
        }

    def _release_configuration(self) -> dict[str, Any] | None:
        intent = self.state.get("releaseConfiguration")
        if intent is None:
            return None
        self._verify_authorization()
        if (
            not self.state.get("authorized")
            or intent.get("binding") != self._release_configuration_binding()
        ):
            raise RuntimeError("partition release configuration authorization changed")
        if intent.get("status") == "complete":
            return None
        before, after = intent["before"], intent["after"]
        if (
            intent.get("status") != "intent"
            or set(before) != set(self.state["partitions"])
            or set(after) != set(before)
            or any(
                after[name] != {**row, **self.state["partitions"][name]}
                for name, row in before.items()
            )
        ):
            raise RuntimeError("partition release configuration scope changed")
        return intent

    def prepare_release_configuration(self) -> None:
        if not self.state.get("authorized"):
            raise RuntimeError("partition release configuration lacks admission authorization")
        self._verify_authorization()
        current = self._partitions()
        self._guard(current)
        if self.state.get("releaseConfiguration") is not None:
            return
        self._resolve_partition_intent(current)
        before = {name: self._owned(row) for name, row in current.items()}
        self.state["releaseConfiguration"] = {
            "binding": self._release_configuration_binding(),
            "before": before,
            "after": {
                name: {**row, **self.state["partitions"][name]} for name, row in before.items()
            },
            "status": "intent",
        }
        self.state["status"] = "releasing"
        self.checks._save()

    def complete_release_configuration(self) -> None:
        intent = self._release_configuration()
        if intent is None:
            self.verify()
            return
        current = {name: self._owned(row) for name, row in self._partitions().items()}
        if current != intent["after"]:
            raise RuntimeError("final partition release configuration has not converged")
        self.state["postimages"] = current
        intent["status"] = "complete"
        self.checks._save()

    def _resolve_partition_intent(self, current: dict[str, dict[str, str]]) -> None:
        intent = self.state.get("partitionIntent")
        if intent is None:
            return
        observed = self._owned(current[intent["name"]])
        if observed == intent["after"]:
            self.state["postimages"][intent["name"]] = observed
        elif observed != intent["before"]:
            raise RuntimeError("admission partition intent has an unknown outcome")
        self.state.pop("partitionIntent")
        self.checks._save()

    def _update(self, name: str, changes: dict[str, str]) -> None:
        current = self._partitions()
        self._guard(current)
        self._resolve_partition_intent(current)
        if self._release_configuration():
            self.state["postimages"] = {key: self._owned(row) for key, row in current.items()}
            self.checks._save()
        expected = {**self._owned(current[name]), **changes}
        self.state["partitionIntent"] = {
            "name": name,
            "before": self._owned(current[name]),
            "after": expected,
        }
        self.checks._save()
        self.checks.authority()
        fields = " ".join(f"{key}={shlex.quote(value)}" for key, value in changes.items())
        self.checks.slurm(f"scontrol update PartitionName={name} {fields}")
        observed = self._owned(self._partitions()[name])
        if observed != expected:
            raise RuntimeError("admission partition mutation did not converge")
        self.state["postimages"][name] = observed
        self.state.pop("partitionIntent", None)
        self.checks._save()

    def establish(self, *, checks_open: bool = False) -> dict[str, Any]:
        if self.state.get("authorized"):
            raise RuntimeError("authorized admission cannot re-enter maintenance")
        admission_partition_configuration(self.checks.policy.partitions, checks_open=checks_open)
        current = self._partitions()
        records = self.preimages()
        transfer = [record.as_payload() for record in records]
        if records and (
            len(records) != len(current) or {record.partition for record in records} != set(current)
        ):
            raise RuntimeError("partition ownership transfer does not cover the complete inventory")
        if self.state and self.state.get("transfer") != transfer:
            raise RuntimeError("job-policy partition ownership transfer changed")
        if not self.state:
            desired = {
                row["name"]: dict(re.findall(r"(?:^|\s)(\w+)=(\S+)", row.get("config", "")))
                for row in self.checks.policy.partitions["partitions"]
            }
            if set(current) != set(desired):
                raise RuntimeError("live partitions differ from frozen admission scope")
            for record in records:
                if record.partition not in current or not record.applied_record:
                    raise RuntimeError("partition transfer lacks a complete applied preimage")
                applied = _record_fields(record.applied_record)
                # The initial declarative maintenance overlay already restricts
                # the native partition. Every other customer field stays guarded.
                if (
                    record.partition == "hidden"
                    and current["hidden"].get("AllowGroups") == "soperatorchecks"
                ):
                    applied["AllowGroups"] = "soperatorchecks"
                if not slurm_partition_migration_fields_match(
                    " ".join(f"{k}={v}" for k, v in current[record.partition].items()),
                    " ".join(f"{k}={v}" for k, v in applied.items()),
                ):
                    raise RuntimeError("partition transfer differs from the job-policy postimage")
                previous = _record_fields(record.previous_record)
                desired[record.partition].update(
                    State=previous["State"], AllowGroups=previous.get("AllowGroups", "ALL")
                )
            self.state.update(
                transfer=transfer,
                observedPreimages=current,
                postimages={name: self._owned(row) for name, row in current.items()},
                partitions={
                    name: {
                        "State": row.get("State", "UP"),
                        "AllowGroups": row.get("AllowGroups", "ALL"),
                    }
                    for name, row in desired.items()
                },
                holds={},
                status="intent",
            )
            self.checks._save()
        self._guard(current)
        self._resolve_partition_intent(current)
        # Close every partition before taking the pending-job snapshot. This
        # does not alter running allocations or the selected user job policy.
        for name, row in current.items():
            if row.get("State") != "DOWN":
                self._update(name, {"State": "DOWN"})
        hidden = current["hidden"]
        if hidden.get("RootOnly", "NO") != "NO" or hidden.get("ReqResv", "NO") != "NO":
            raise RuntimeError("hidden partition cannot run native check jobs")
        self._principal()
        self._update("hidden", {"AllowGroups": "soperatorchecks"})
        self._hold_pending(mutate=True)
        if checks_open:
            self.checks._verify_isolation()
            self._update("hidden", {"State": "UP"})
        self.state.update(status="closed", checksOpen=checks_open)
        self.checks._save()
        self.verify()
        return self.state

    def _hold_pending(self, *, mutate: bool) -> None:
        for line in self.checks.slurm("squeue -r -h -t PENDING -o '%i|%u|%P'").splitlines():
            fields = line.split("|")
            if len(fields) != 3:
                raise RuntimeError("ambiguous admission pending-job inventory")
            job_id, user, partitions = fields
            if user == "soperatorchecks" or "hidden" not in partitions.split(","):
                continue
            before = self._job(job_id)
            if before is None:
                continue
            record = self.state["holds"].get(job_id)
            newly_recorded = record is None
            if record is None:
                if slurm_job_control_is_held(before):
                    continue  # Policy/user/admin holds remain with their owner.
                if not mutate:
                    raise RuntimeError("pending job requires admission reconciliation")
                record = {"preimage": before.as_payload(), "status": "intent"}
                self.state["holds"][job_id] = record
                self.checks._save()
            if before.identity_sha256 != record["preimage"]["identity_sha256"]:
                raise RuntimeError("admission job identity changed before hold")
            if (
                record["status"] == "intent"
                and not newly_recorded
                and slurm_job_control_is_held(before)
            ):
                raise RuntimeError(
                    "ambiguous interrupted admission hold; do not release an unproven hold"
                )
            if record["status"] == "applied":
                if before.as_payload() != record["postimage"]:
                    raise RuntimeError("admission hold changed outside its owner")
                continue
            if not slurm_job_control_is_held(before):
                if not mutate:
                    raise RuntimeError("admission hold has not converged")
                latest = self._job(job_id)
                if latest is None:
                    record["status"] = "gone"
                    self.checks._save()
                    continue
                if latest.as_payload() != before.as_payload():
                    raise RuntimeError("admission job changed immediately before hold")
                self.checks.authority()
                self.checks.slurm("scontrol hold " + job_id)
            after = self._job(job_id)
            if after is None:
                record["status"] = "gone"
            elif after.identity_sha256 != before.identity_sha256 or not slurm_job_control_is_held(
                after
            ):
                raise RuntimeError("admission hold did not converge")
            else:
                record.update(status="applied", postimage=after.as_payload())
            self.checks._save()

    def verify(self) -> dict[str, Any]:
        if self.state.get("status") in {"releasing", "released"}:
            self._guard(self._partitions())
            self._verify_authorization()
            return self.state
        if self.state.get("status") != "closed":
            raise RuntimeError("independent user admission is not established")
        current = self._partitions()
        self._guard(current)
        if set(current) != set(self.state["partitions"]):
            raise RuntimeError("admission partition inventory changed")
        for name, row in current.items():
            expected = "UP" if name == "hidden" and self.state["checksOpen"] else "DOWN"
            if row.get("State") != expected or (
                name == "hidden" and row.get("AllowGroups") != "soperatorchecks"
            ):
                raise RuntimeError("user admission escaped the maintenance barrier")
        self._principal()
        if not self.state.get("authorized"):
            self._hold_pending(mutate=False)
        return self.state

    def _authorization(self) -> dict[str, str]:
        return {
            "operation": self.checks.operation_id,
            "policy": self.checks.policy.sha256,
            "handoff": checks_digest(self.checks.state.get("scheduleRelease")),
            "passive": checks_digest(self.checks.state.get("passive", {}).get("acceptance")),
            "partitions": checks_digest(self.state.get("partitions")),
            "transfer": checks_digest(self.state.get("transfer")),
        }

    def _verify_authorization(self) -> None:
        if self.state.get("authorization") != self._authorization():
            raise RuntimeError("durable admission authorization changed")

    def authorize(self) -> None:
        if self.state.get("authorized"):
            self._verify_authorization()
            return
        self.verify()
        if self.checks.state.get("phase") != "restored":
            raise RuntimeError("admission requires restored diagnostic policy")
        self.checks.verify_acceptance()
        self.state["authorization"] = self._authorization()
        self.state["authorized"] = True
        self.checks._save()

    def release_holds(self) -> None:
        if not self.state.get("authorized"):
            raise RuntimeError("user job release lacks final admission authorization")
        self._verify_authorization()
        for job_id, record in self.state["holds"].items():
            if record["status"] in {"gone", "released"}:
                continue
            live = self._job(job_id)
            if live is not None:
                if live.identity_sha256 != record["preimage"]["identity_sha256"]:
                    raise RuntimeError("admission job ID was reused before release")
                if live.as_payload() != record.get("postimage"):
                    if not (record.get("releaseIntent") and not slurm_job_control_is_held(live)):
                        raise RuntimeError("admission hold was independently changed")
                else:
                    record["releaseIntent"] = True
                    self.checks._save()
                    self.checks.authority()
                    self.checks.slurm("scontrol release " + job_id)
                    after = self._job(job_id)
                    if after is not None and (
                        after.identity_sha256 != live.identity_sha256
                        or slurm_job_control_is_held(after)
                    ):
                        raise RuntimeError("admission hold release did not converge")
            record["status"] = "released"
            self.checks._save()

    def restore_partitions(self) -> None:
        self.release_holds()
        self.state["status"] = "releasing"
        self.checks._save()
        done = self.state.setdefault("releasedPartitions", [])
        for name, before in self.state["partitions"].items():
            if name in done:
                current = self._partitions()
                self._guard(current)
                if all(current[name].get(key) == value for key, value in before.items()):
                    continue
                if not self._release_configuration():
                    raise RuntimeError("completed admission partition restoration changed")
            self._update(name, before)
            observed = self._partitions()[name]
            if any(observed.get(key) != value for key, value in before.items()):
                raise RuntimeError("admission partition restoration did not converge")
            if name not in done:
                done.append(name)
            self.checks._save()
        self.state["status"] = "released"
        self.checks._save()
