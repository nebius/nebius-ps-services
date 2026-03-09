from __future__ import annotations

from unittest.mock import patch

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
