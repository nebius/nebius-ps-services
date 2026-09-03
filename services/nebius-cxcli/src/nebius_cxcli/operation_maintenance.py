"""Operation-neutral recovery decisions for durable maintenance resources."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

MaintenanceReservationAction = Literal["record-and-create", "create", "reuse"]


def maintenance_reservation_recovery_action(
    *,
    events: Sequence[Mapping[str, Any]],
    live_reservations: Sequence[str],
    reservation_name: str,
    owner: str,
) -> MaintenanceReservationAction:
    """Choose create or reuse only from exact operation intent and live state."""

    normalized_name = str(reservation_name).strip()
    intent_recorded = any(
        event.get("action") == "maintenance-reservation-intent"
        and str(event.get("reservation_name") or "").strip() == normalized_name
        for event in events
    )
    live = normalized_name in {str(value).strip() for value in live_reservations}
    if live and not intent_recorded:
        raise RuntimeError(f"{owner} cannot own a pre-existing Slurm reservation")
    if live:
        return "reuse"
    if intent_recorded:
        return "create"
    return "record-and-create"


__all__ = ["MaintenanceReservationAction", "maintenance_reservation_recovery_action"]
