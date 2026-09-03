from __future__ import annotations

import pytest

from nebius_cxcli.operation_maintenance import maintenance_reservation_recovery_action


@pytest.mark.parametrize(
    ("intent_recorded", "live", "expected"),
    (
        (False, False, "record-and-create"),
        (True, False, "create"),
        (True, True, "reuse"),
    ),
)
def test_maintenance_reservation_recovery_action_requires_exact_intent(
    intent_recorded: bool,
    live: bool,
    expected: str,
) -> None:
    name = "cxcli_0123456789abcdef"
    events = (
        ({"action": "maintenance-reservation-intent", "reservation_name": name},)
        if intent_recorded
        else ()
    )

    assert (
        maintenance_reservation_recovery_action(
            events=events,
            live_reservations=(name,) if live else (),
            reservation_name=name,
            owner="test operation",
        )
        == expected
    )


def test_maintenance_reservation_recovery_rejects_foreign_live_name() -> None:
    with pytest.raises(RuntimeError, match="cannot own a pre-existing Slurm reservation"):
        maintenance_reservation_recovery_action(
            events=(),
            live_reservations=("cxcli_0123456789abcdef",),
            reservation_name="cxcli_0123456789abcdef",
            owner="test operation",
        )
