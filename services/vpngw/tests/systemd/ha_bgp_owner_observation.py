"""Bounded readiness observation for the native owner-coverage fixture."""

from __future__ import annotations

import time
from collections.abc import Callable

from nebius_vpngw.agent.vm_ha_controller import LocalReadiness
from nebius_vpngw.faults import FaultCategory


def wait_for_owner_coverage(
    observe: Callable[[], LocalReadiness],
    *,
    retained_route: bool,
    required_prefix: str,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> LocalReadiness:
    while clock() < deadline:
        readiness = observe()
        if (
            readiness.sample_complete
            and readiness.fault_category is FaultCategory.COVERAGE_LOSS
            and not readiness.bgp_ready
            and required_prefix not in readiness.covered_destinations
            if retained_route
            else readiness.sample_complete and readiness.promotion_ready and readiness.path_degraded
        ):
            return readiness
        sleeper(0.1)
    raise AssertionError("Native BGP coverage did not reach the expected state within 10 seconds")
