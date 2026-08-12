from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from nebius_vpngw.agent.state_store import RENDER_VERSION, StateStore


def test_state_store_round_trips_last_applied_config(tmp_path) -> None:
    state_path = tmp_path / "state" / "last-applied.json"
    store = StateStore(state_path)
    resolved_config = {"connections": [{"name": "peer-a"}]}

    assert store.is_changed(resolved_config)

    with patch("nebius_vpngw.agent.state_store._get_package_version", return_value="1.2.3"):
        store.save_last_applied(resolved_config)

    saved = store.load_last_applied()

    assert saved is not None
    assert saved["package_version"] == "1.2.3"
    assert saved["render_version"] == RENDER_VERSION
    assert saved["resolved_config"] == resolved_config
    assert not store.is_changed(resolved_config)
    assert store.is_changed({"connections": [{"name": "peer-b"}]})


def test_state_store_returns_none_for_invalid_json(tmp_path) -> None:
    state_path = tmp_path / "broken.json"
    state_path.write_text("{not-json", encoding="utf-8")

    store = StateStore(state_path)

    assert store.load_last_applied() is None


def test_state_store_atomic_write_preserves_previous_state_on_replace_failure(tmp_path) -> None:
    state_path = tmp_path / "state" / "last-applied.json"
    store = StateStore(state_path)
    first = {"connections": [{"name": "peer-a"}]}
    second = {"connections": [{"name": "peer-b"}]}
    store.save_last_applied(first)

    with (
        patch.object(os, "replace", side_effect=OSError("injected replace failure")),
        pytest.raises(OSError, match="injected replace failure"),
    ):
        store.save_last_applied(second)

    assert store.load_last_applied()["resolved_config"] == first
    assert list(state_path.parent.glob(".last-applied.json.*")) == []
