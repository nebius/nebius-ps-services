from __future__ import annotations

from types import SimpleNamespace

import pytest

from nebius_vpngw.cli import _list_all_instances


class _FakeInstanceService:
    """Returns one page per call, recording the page tokens it was asked for."""

    def __init__(self, pages: list[SimpleNamespace]) -> None:
        self._pages = pages
        self.seen_tokens: list[str] = []

    def list(self, request: object) -> SimpleNamespace:
        self.seen_tokens.append(getattr(request, "page_token", ""))
        page = self._pages[min(len(self.seen_tokens) - 1, len(self._pages) - 1)]
        return SimpleNamespace(wait=lambda: page)


def test_every_page_is_collected() -> None:
    isc = _FakeInstanceService(
        [
            SimpleNamespace(items=["gw-0"], next_page_token="page-2"),
            SimpleNamespace(items=["gw-1"], next_page_token=""),
        ]
    )

    assert _list_all_instances(isc, "project-1") == ["gw-0", "gw-1"]
    assert isc.seen_tokens == ["", "page-2"]


def test_a_repeated_page_token_aborts_instead_of_looping() -> None:
    isc = _FakeInstanceService([SimpleNamespace(items=["gw-0"], next_page_token="same")])

    with pytest.raises(RuntimeError, match="repeated pagination token"):
        _list_all_instances(isc, "project-1")


def test_a_single_page_still_works() -> None:
    isc = _FakeInstanceService([SimpleNamespace(items=["gw-0"], next_page_token="")])

    assert _list_all_instances(isc, "project-1") == ["gw-0"]
    assert isc.seen_tokens == [""]
