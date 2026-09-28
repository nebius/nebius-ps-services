"""Historical check observations, independent of current component health."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

CHECK_STYLES = {
    "Complete": "green",
    "Active": "cyan",
    "InProgress": "cyan",
    "Pending": "blue",
    "Failed": "red",
    "Error": "bold red",
    "Cancelled": "yellow",
    "Suspended": "dim white",
    "Unknown": "magenta",
    "No recorded result": "dim white",
    "Unrecognized result": "magenta",
    "Unrecognized check type": "magenta",
}
_RESULTS = {
    "k8sJob": {"Active", "Pending", "Complete", "Failed", "Suspended", "Unknown"},
    "slurmJob": {"InProgress", "Complete", "Failed", "Cancelled", "Error"},
}


@dataclass(frozen=True)
class RecordedCheck:
    name: str
    check_type: str
    result: str
    suspended: bool
    timestamps: tuple[tuple[str, str], ...] = ()

    @property
    def needs_attention(self) -> bool:
        return self.result in {"Failed", "Error", "Cancelled"}

    @property
    def category(self) -> str:
        if self.result == "No recorded result":
            return "suspended without results" if self.suspended else "without recorded results"
        return {
            "Complete": "complete",
            "Active": "running",
            "InProgress": "running",
            "Pending": "pending",
            "Failed": "failures",
            "Error": "failures",
            "Cancelled": "cancelled",
            "Suspended": "suspended results",
        }.get(self.result, "unverified results")


@dataclass(frozen=True)
class CheckHistory:
    state: str = "unavailable"
    detail: str = "History collection was not verified"
    records: tuple[RecordedCheck, ...] = ()

    @property
    def summary(self) -> str:
        if not self.records:
            return "No recorded checks"
        counts = Counter(record.category for record in self.records)
        return " · ".join(
            f"{counts[category]} {category}"
            for category in (
                "complete",
                "running",
                "pending",
                "failures",
                "cancelled",
                "suspended results",
                "unverified results",
                "suspended without results",
                "without recorded results",
            )
            if counts[category] or category == "failures"
        )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def project_check_history(
    collection: Mapping[str, Any], resources: Sequence[Mapping[str, Any]]
) -> CheckHistory:
    state = str(collection.get("state") or "unavailable")
    if state != "collected":
        if state not in {"unavailable", "not_installed", "not_checked"}:
            state = "unavailable"
        return CheckHistory(state, str(collection.get("detail") or "History is unavailable"))
    checks = []
    for resource in resources:
        spec, status = _mapping(resource.get("spec")), _mapping(resource.get("status"))
        check_type = str(spec.get("checkType", "k8sJob"))
        job = _mapping(
            status.get("slurmJobsStatus" if check_type == "slurmJob" else "k8sJobsStatus")
        )
        fields: tuple[tuple[str, str], ...] = (
            (("Submitted", "lastRunSubmitTime"), ("Status updated", "lastTransitionTime"))
            if check_type == "slurmJob"
            else (
                ("Scheduled", "lastJobScheduleTime"),
                ("Last successful", "lastJobSuccessfulTime"),
                ("Status updated", "lastTransitionTime"),
            )
        )
        if check_type not in _RESULTS:
            result = "Unrecognized check type"
            job, fields = {}, ()
        else:
            native = job.get("lastRunStatus" if check_type == "slurmJob" else "lastJobStatus")
            result = (
                "No recorded result"
                if native in (None, "")
                else native
                if isinstance(native, str) and native in _RESULTS[check_type]
                else "Unrecognized result"
            )
        timestamps = []
        for label, key in fields:
            value = job.get(key)
            if not isinstance(value, str):
                continue
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.year > 1 and parsed.tzinfo is not None:
                timestamps.append((label, value))
        checks.append(
            RecordedCheck(
                str(_mapping(resource.get("metadata")).get("name") or "Unnamed check"),
                check_type,
                result,
                spec.get("suspend", True) is True,
                tuple(timestamps),
            )
        )
    return CheckHistory("collected", "", tuple(sorted(checks, key=lambda c: c.name)))
