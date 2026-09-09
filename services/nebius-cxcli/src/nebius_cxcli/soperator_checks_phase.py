"""Phase values compose diagnostics and user admission without changing intent."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ChecksPhase(StrEnum):
    JOB_POLICY = "job-policy"
    MAINTENANCE = "maintenance"
    ACCEPTANCE = "acceptance"
    SCHEDULES = "schedules"
    ADMISSION = "admission"
    READY = "ready"


@dataclass(frozen=True)
class ChecksPhaseContext:
    phase: ChecksPhase
    reservation: str
    passive_fallback: bool = False
    fresh_install: bool = False
    partition_restoration: Mapping[str, Mapping[str, str]] | None = None

    def __post_init__(self) -> None:
        if self.reservation and not re.fullmatch(r"[A-Za-z0-9_.-]+", self.reservation):
            raise ValueError("invalid diagnostic reservation identity")


def restored_partition_configuration(
    value: object, restoration: Mapping[str, Mapping[str, str]]
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("partition restoration requires a structured configuration")
    result = copy.deepcopy(dict(value))
    for row in result["partitions"]:
        for key, setting in restoration.get(row["name"], {}).items():
            if key not in {"State", "AllowGroups"} or not re.fullmatch(
                r"[A-Za-z0-9_,.-]+", setting
            ):
                raise ValueError("invalid owned partition restoration")
            row["config"] = (
                re.sub(r"(?:^|\s)" + key + r"=\S+", "", row.get("config", "")).strip()
                + f" {key}={setting}"
            )
    return result


def admission_partition_configuration(value: object, *, checks_open: bool) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("configType") != "structured":
        raise ValueError("maintenance admission requires structured Slurm partitions")
    result = copy.deepcopy(dict(value))
    rows = result.get("partitions")
    if not isinstance(rows, list) or not rows:
        raise ValueError("maintenance admission requires explicit partitions")
    names: set[str] = set()
    for row in rows:
        name = row.get("name") if isinstance(row, dict) else None
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name in names:
            raise ValueError("ambiguous maintenance partition identity")
        names.add(name)
        config = str(row.get("config") or "")
        keys = re.findall(r"(?:^|\s)(\w+)=", config)
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate maintenance partition fields")
        if name == "hidden":
            fields = dict(re.findall(r"(?:^|\s)(\w+)=(\S+)", config))
            if fields.get("RootOnly", "NO") != "NO" or fields.get("ReqResv", "NO") != "NO":
                raise ValueError("native checks partition forbids the upstream checks principal")
            config = re.sub(r"(?:^|\s)AllowGroups=\S+", "", config)
            config += " AllowGroups=soperatorchecks"
        config = re.sub(r"(?:^|\s)State=\S+", "", config)
        row["config"] = config.strip() + (
            " State=UP" if checks_open and name == "hidden" else " State=DOWN"
        )
    if "hidden" not in names:
        raise ValueError("upstream native checks require the hidden partition")
    return result
