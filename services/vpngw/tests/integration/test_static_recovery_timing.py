"""A slow synchronous pass must retain a freshly successful real native proof."""

import os

import pytest
from test_static_recovery_lifecycle import native_paths as native_paths
from test_static_recovery_lifecycle import scenario as scenario

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("VPNGW_ISOLATED_SYSTEMD_TEST") != "1",
        reason="requires the explicit disposable Linux systemd/XFRM fixture",
    ),
    pytest.mark.parametrize("native_paths", ["static-lifecycle"], indirect=True),
]


@pytest.mark.parametrize("scenario", [11], indirect=True)
def test_slow_backup_proof_survives_the_next_controller_pass(scenario):
    scenario.fault("preferred", blocked=True)

    def confirmed(sample):
        record = sample["static_recovery_observations"].get("site", {})
        return record.get("healthy", {}).get("backup", {}).get("count", 0) >= 1

    first = scenario.wait(confirmed, seconds=80)
    first_record = first["static_recovery_observations"]["site"]
    assert first["tick_seconds"] >= 11, "The intended slow controller pass was not exercised"
    scenario.mark("delayed-backup-proof", tick_seconds=first["tick_seconds"])
    following = scenario.wait(lambda sample: sample["at"] > first["at"], seconds=20)
    record = following["static_recovery_observations"].get("site", {})
    scenario.mark(
        "following-pass-observed",
        prior_since=first_record["since"],
        current_since=record.get("since"),
        healthy=record.get("healthy"),
    )
    assert record.get("since") == first_record["since"], (
        "Slow synchronous work reset the continuous-loss observation"
    )
    assert record.get("healthy", {}).get("backup", {}).get("count", 0) >= 1, (
        "The next pass discarded a fresh successful backup proof"
    )
