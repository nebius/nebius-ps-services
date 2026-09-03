"""Rootfs transition planning for protected Soperator upgrades."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .soperator_jail_mounts import (
    apply_jail_persistent_mount_values,
    jail_rootfs_active_source,
)
from .soperator_populate_jail import (
    active_passive_jail_rootfs_slots,
    switch_active_passive_jail_rootfs_values,
)


def plan_soperator_rootfs_transition(
    current_values: Mapping[str, Any],
    *,
    target_ref: str,
    layout: str,
    legacy_pvc_resolver: Callable[[], str],
) -> tuple[dict[str, Any], dict[str, object]]:
    """Plan the first release transition from one canonical active-rootfs source."""

    current_active_source = jail_rootfs_active_source(current_values)
    if current_active_source == "slot":
        current_slots = active_passive_jail_rootfs_slots(current_values)
        live_jail_pvc = current_slots.active_pvc
        switched_values = switch_active_passive_jail_rootfs_values(current_values)
        switched_rootfs = switched_values.get("jailRootfs")
        switched_rootfs_map = switched_rootfs if isinstance(switched_rootfs, dict) else {}
        switched_adoption = switched_rootfs_map.setdefault("adoption", {})
        if not isinstance(switched_adoption, dict):
            raise RuntimeError("protected Soperator jailRootfs adoption is invalid")
        switched_adoption["activeSource"] = "slot"
        switched_adoption["rollbackSource"] = "slot"
        switched_adoption.pop("legacyPvcName", None)
        switched_slots = active_passive_jail_rootfs_slots(switched_values)
        return switched_values, {
            "currentActiveSource": "slot",
            "currentActiveSlot": current_slots.active_slot,
            "currentInactiveSlot": current_slots.passive_slot,
            "desiredActiveSlot": switched_slots.active_slot,
            "livePvcName": live_jail_pvc,
            "targetPvcName": switched_slots.active_pvc,
            "recycleInactiveSlot": True,
        }

    live_jail_pvc = str(legacy_pvc_resolver() or "").strip()
    if not live_jail_pvc:
        raise RuntimeError("protected Soperator live legacy jail PVC is empty")
    adopted_values = apply_jail_persistent_mount_values(
        current_values,
        target_ref=target_ref,
        layout=layout,
        legacy_active_source=True,
    )
    jail_rootfs = adopted_values.setdefault("jailRootfs", {})
    if not isinstance(jail_rootfs, dict):
        raise RuntimeError("protected Soperator jailRootfs values are invalid")
    adoption = jail_rootfs.setdefault("adoption", {})
    if not isinstance(adoption, dict):
        raise RuntimeError("protected Soperator jailRootfs adoption is invalid")
    adoption["legacyPvcName"] = live_jail_pvc
    switched_values = switch_active_passive_jail_rootfs_values(adopted_values)
    adopted_slots = active_passive_jail_rootfs_slots(adopted_values)
    switched_slots = active_passive_jail_rootfs_slots(switched_values)
    return switched_values, {
        "currentActiveSource": "legacy-rootfs",
        "currentActiveSlot": "legacy-rootfs",
        "currentInactiveSlot": adopted_slots.passive_slot,
        "desiredActiveSlot": switched_slots.active_slot,
        "livePvcName": live_jail_pvc,
        "targetPvcName": switched_slots.active_pvc,
        "recycleInactiveSlot": False,
    }
