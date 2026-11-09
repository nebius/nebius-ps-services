"""Native coverage assertions must use complete, matching immutable evidence."""

from __future__ import annotations

from dataclasses import replace

import pytest
from tests.systemd.ha_bgp_owner_observation import wait_for_owner_coverage

from nebius_vpngw.agent.vm_ha_controller import LocalReadiness

PREFIX = "10.200.2.1/32"
HEALTHY = LocalReadiness(True, True, True, True, covered_destinations=(PREFIX,))
STALE = replace(HEALTHY, bgp_ready=False, xfrm_ready=False, covered_destinations=())


class Clock:
    now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.mark.parametrize(
    "transient",
    [
        replace(STALE, sample_complete=False),
        replace(STALE, service_healthy=False),
        replace(HEALTHY, bgp_ready=False),
        replace(STALE, local_defects=("daemon-failed:frr",)),
    ],
)
def test_stale_route_oracle_ignores_unknown_and_unrelated_failure(transient):
    clock = Clock()
    samples = iter([transient, HEALTHY, STALE, HEALTHY])
    observed = []

    def sample():
        value = next(samples)
        observed.append(value)
        return value

    result = wait_for_owner_coverage(
        sample,
        retained_route=True,
        required_prefix=PREFIX,
        deadline=10,
        clock=clock.monotonic,
        sleeper=clock.sleep,
    )
    assert result is STALE
    assert observed == [transient, HEALTHY, STALE]


@pytest.mark.parametrize("retained", [False, True])
def test_oracle_returns_the_qualifying_snapshot_without_resampling(retained):
    expected = STALE if retained else replace(HEALTHY, path_degraded=True)
    samples = iter([HEALTHY, expected, replace(STALE, sample_complete=False)])
    clock = Clock()
    result = wait_for_owner_coverage(
        lambda: next(samples),
        retained_route=retained,
        required_prefix=PREFIX,
        deadline=10,
        clock=clock.monotonic,
        sleeper=clock.sleep,
    )
    assert result is expected
    assert not next(samples).sample_complete


@pytest.mark.parametrize("retained", [False, True])
def test_nonmatching_evidence_exhausts_original_window(retained):
    clock = Clock()
    calls = []

    def sample():
        calls.append(clock.now)
        return HEALTHY

    with pytest.raises(AssertionError, match="within 10 seconds"):
        wait_for_owner_coverage(
            sample,
            retained_route=retained,
            required_prefix=PREFIX,
            deadline=10,
            clock=clock.monotonic,
            sleeper=clock.sleep,
        )
    assert calls and max(calls) < 10
    assert 10 <= clock.now < 10.1
