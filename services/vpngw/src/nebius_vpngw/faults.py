"""Shared fault vocabulary; absence of a path is never a local-machine defect."""

from enum import Enum


class FaultCategory(str, Enum):
    NONE = "none"
    SAFETY = "ownership-or-guard"
    LOCAL_DEFECT = "local-daemon-or-kernel"
    COVERAGE_LOSS = "peer-or-path"
    UNKNOWN = "unknown"
