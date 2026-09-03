"""Capability-based Soperator lifecycle strategy selection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .soperator_release import SoperatorVersion


class SoperatorStrategy(StrEnum):
    INSTALL = "install"
    NOOP = "noop"
    IN_PLACE = "in-place"
    PROTECTED_DATA_PLANE = "protected-data-plane"


@dataclass(frozen=True)
class SoperatorStrategyPlan:
    strategy: SoperatorStrategy
    source_release: str
    target_release: str
    source_contract: str
    target_contract: str
    requires_protected_state: bool
    requires_slurm_maintenance: bool


_STRATEGY_GRAPH = {
    ("absent", "upstream-flux-v1"): SoperatorStrategy.INSTALL,
    ("upstream-flux-v1", "upstream-flux-v1"): SoperatorStrategy.IN_PLACE,
    ("protected-data-plane-v1", "upstream-flux-v1"): SoperatorStrategy.PROTECTED_DATA_PLANE,
}


def plan_soperator_strategy(
    *,
    source_release: str | None,
    target_release: str,
    source_contract: str | None,
    target_contract: str,
) -> SoperatorStrategyPlan:
    """Select a lifecycle strategy without dispatching on release numbers."""

    target_version = SoperatorVersion.parse(target_release)
    normalized_source = str(source_release or "").strip().removeprefix("v")
    normalized_source_contract = str(source_contract or "absent").strip()
    normalized_target_contract = str(target_contract or "").strip()
    if normalized_source:
        source_version = SoperatorVersion.parse(normalized_source)
        if target_version < source_version:
            raise ValueError(
                f"Soperator downgrade {source_version} -> {target_version} is not supported"
            )
        if target_version == source_version:
            if normalized_source_contract != normalized_target_contract:
                raise ValueError("equal Soperator versions produced different capability contracts")
            return SoperatorStrategyPlan(
                strategy=SoperatorStrategy.NOOP,
                source_release=str(source_version),
                target_release=str(target_version),
                source_contract=normalized_source_contract,
                target_contract=normalized_target_contract,
                requires_protected_state=False,
                requires_slurm_maintenance=False,
            )
    strategy = _STRATEGY_GRAPH.get((normalized_source_contract, normalized_target_contract))
    if strategy is None:
        raise ValueError(
            "no reviewed Soperator capability strategy exists for "
            f"{normalized_source_contract or '?'} -> {normalized_target_contract or '?'}"
        )
    return SoperatorStrategyPlan(
        strategy=strategy,
        source_release=normalized_source,
        target_release=str(target_version),
        source_contract=normalized_source_contract,
        target_contract=normalized_target_contract,
        requires_protected_state=strategy is SoperatorStrategy.PROTECTED_DATA_PLANE,
        requires_slurm_maintenance=strategy
        in {SoperatorStrategy.IN_PLACE, SoperatorStrategy.PROTECTED_DATA_PLANE},
    )


__all__ = [
    "SoperatorStrategy",
    "SoperatorStrategyPlan",
    "plan_soperator_strategy",
]
