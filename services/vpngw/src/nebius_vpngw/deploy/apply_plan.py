"""Immutable command intent and effect-scoped resume decisions.

Execution and cloud-operation receipts remain owned by the existing deployment
adapters. This module describes which effects the customer actually approved.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from ..apply_approval import digest


def _sha(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ValueError("Apply authority requires an exact SHA-256 digest")


class Outcome(str, Enum):
    UNCHANGED = "unchanged"
    APPLIED = "applied"
    RESUMED = "resumed"
    BLOCKED = "blocked"
    PENDING = "pending"
    FAILED = "failed"


class Evidence(str, Enum):
    READY = "ready"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class Progress(str, Enum):
    PLANNED = "planned"
    STARTED = "started"
    VERIFIED = "verified"


@dataclass(frozen=True)
class Effect:
    key: str
    owner: str
    action: str
    targets: tuple[str, ...]
    request_sha256: str
    observation_sha256: str
    disruptive: bool
    requires: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            any(
                not isinstance(value, str) or not value
                for value in (self.key, self.owner, self.action)
            )
            or type(self.targets) is not tuple
            or type(self.requires) is not tuple
            or not self.targets
            or any(not isinstance(value, str) or not value for value in self.targets)
            or len(set(self.targets)) != len(self.targets)
            or type(self.disruptive) is not bool
            or len(set(self.requires)) != len(self.requires)
        ):
            raise ValueError("Invalid apply effect identity")
        _sha(self.request_sha256)
        _sha(self.observation_sha256)

    def same_request(self, other: Effect) -> bool:
        """A partial execution may change observation, never its requested effect."""
        left, right = asdict(self), asdict(other)
        left.pop("observation_sha256")
        right.pop("observation_sha256")
        return left == right


@dataclass(frozen=True)
class CommandPlan:
    project_id: str
    gateway_name: str
    configuration_sha256: str
    artifact_sha256: str
    effects: tuple[Effect, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.project_id, str)
            or not isinstance(self.gateway_name, str)
            or not self.project_id
            or not self.gateway_name
            or type(self.effects) is not tuple
            or any(not isinstance(effect, Effect) for effect in self.effects)
        ):
            raise ValueError("Apply plan requires an exact gateway scope")
        _sha(self.configuration_sha256)
        _sha(self.artifact_sha256)
        seen: set[str] = set()
        for effect in self.effects:
            if effect.key in seen or not set(effect.requires) <= seen:
                raise ValueError("Apply effects must have unique, ordered prerequisites")
            seen.add(effect.key)

    def mapping(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def approval(self) -> str:
        return digest(self.mapping())

    @classmethod
    def parse(cls, value: dict) -> CommandPlan:
        if set(value) != {
            "project_id",
            "gateway_name",
            "configuration_sha256",
            "artifact_sha256",
            "effects",
        }:
            raise ValueError("Invalid persisted apply plan")
        effects = tuple(
            Effect(**dict(item, targets=tuple(item["targets"]), requires=tuple(item["requires"])))
            for item in value["effects"]
        )
        return cls(**dict(value, effects=effects))

    def validate_resume(
        self,
        observed: CommandPlan,
        progress: dict[str, Progress],
        *,
        resumable_children: frozenset[str] = frozenset(),
        satisfied_children: frozenset[str] = frozenset(),
    ) -> None:
        """Prove fresh effects are contained in this original approved command.

        A verified step cannot be reused for newly discovered drift. A started
        step needs the owning adapter's independent exact-child resume proof.
        Unstarted steps retain their original predecessor observation.
        """
        original = self.mapping()
        current = observed.mapping()
        original.pop("effects")
        current.pop("effects")
        if original != current:
            raise RuntimeError("Apply intent changed; a reviewed successor is required")
        approved = {effect.key: effect for effect in self.effects}
        if set(progress) != set(approved) or not (resumable_children | satisfied_children) <= set(
            approved
        ):
            raise ValueError("Apply progress does not match the approved command")
        for effect in observed.effects:
            previous = approved.get(effect.key)
            if previous is None or not previous.same_request(effect):
                raise RuntimeError("Pending apply does not authorize newly discovered effects")
            state = progress[effect.key]
            if effect.key in satisfied_children:
                continue
            if state is Progress.VERIFIED:
                raise RuntimeError("Completed apply authority cannot authorize another effect")
            if state is Progress.STARTED:
                if effect.key not in resumable_children:
                    raise RuntimeError("Started apply effect lacks exact child resume evidence")
            elif state is Progress.PLANNED:
                if previous.observation_sha256 != effect.observation_sha256:
                    raise RuntimeError("Unstarted apply predecessor changed; review a fresh plan")
            else:
                raise ValueError("Invalid apply effect progress")
        # A new no-op observation cannot cancel an uncertain accepted effect.
        for key, state in progress.items():
            if state is Progress.STARTED and key not in resumable_children | satisfied_children:
                raise RuntimeError("Pending apply must settle every started child effect")


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    configuration: Evidence
    local_runtime: Evidence
    authority: Evidence
    connectivity: Evidence
    completed: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()
    inhibition_retained: bool | None = None
    next_command: str | None = None

    def mapping(self) -> dict[str, Any]:
        return {"schema": "nebius-vpngw.apply-result.v1", **asdict(self)}
