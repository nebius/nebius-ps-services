from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from nebius_cxcli.capacity_dashboard import (
    CapacityAdviceAvailability,
    CapacityResourceAdvice,
    capacity_availability,
    capacity_summary_text,
    list_capacity_resource_advice,
)


def test_capacity_summary_text_shows_vm_slots_and_gpu_total() -> None:
    item = CapacityResourceAdvice(
        region="eu-north1",
        platform="gpu-h100-sxm",
        preset="8gpu-128vcpu-1600gb",
        fabric="fabric-1",
        on_demand=CapacityAdviceAvailability(
            available=2,
            limit=10,
            availability_level="AVAILABILITY_LEVEL_MEDIUM",
            data_state="DATA_STATE_FRESH",
        ),
        reserved=CapacityAdviceAvailability(
            available=1,
            limit=1,
            availability_level="AVAILABILITY_LEVEL_HIGH",
            data_state="DATA_STATE_FRESH",
        ),
        preemptible=CapacityAdviceAvailability(
            available=0,
            limit=0,
            availability_level="AVAILABILITY_LEVEL_UNKNOWN",
            data_state="DATA_STATE_FRESH",
        ),
        gpu_count=8,
    )

    assert (
        capacity_summary_text(item)
        == "regular-vm 2 VMs (2 x 8-GPU = 16 GPUs), reserved 1 VM (1 x 8-GPU = 8 GPUs)"
    )


def test_capacity_availability_preserves_lane_data_state() -> None:
    availability = capacity_availability(
        SimpleNamespace(
            available=2,
            limit=4,
            availability_level=SimpleNamespace(name="AVAILABILITY_LEVEL_MEDIUM"),
            data_state=SimpleNamespace(name="DATA_STATE_STALE"),
        )
    )

    assert availability.data_state == "DATA_STATE_STALE"


def test_list_capacity_resource_advice_rejects_repeated_page_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ListResourceAdviceRequest:
        def __init__(self, *, parent_id: str, page_size: int, page_token: str) -> None:
            self.parent_id = parent_id
            self.page_size = page_size
            self.page_token = page_token

    calls: list[str] = []

    class _ResourceAdviceServiceClient:
        def __init__(self, _sdk: object) -> None:
            pass

        def list(self, request: _ListResourceAdviceRequest):  # type: ignore[no-untyped-def]
            calls.append(request.page_token)
            response = SimpleNamespace(items=[], next_page_token="same-token")
            return SimpleNamespace(wait=lambda: response)

    capacity_module = ModuleType("nebius.api.nebius.capacity.v1")
    capacity_module.ListResourceAdviceRequest = _ListResourceAdviceRequest  # type: ignore[attr-defined]
    capacity_module.ResourceAdviceServiceClient = _ResourceAdviceServiceClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nebius.api.nebius.capacity.v1", capacity_module)

    with pytest.raises(RuntimeError, match="repeated pagination token"):
        list_capacity_resource_advice(object(), parent_id="tenant-1")

    assert calls == ["", "same-token"]
