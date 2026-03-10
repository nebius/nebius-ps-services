from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def block_unit_test_network(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    if request.node.get_closest_marker("integration"):
        return

    def _blocked(*args, **kwargs):
        raise AssertionError("Network access is disabled in unit tests.")

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
