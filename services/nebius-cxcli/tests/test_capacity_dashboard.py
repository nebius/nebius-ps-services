from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from nebius_cxcli.capacity_dashboard import list_capacity_resource_advice


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
