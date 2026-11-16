"""Obsolete metadata shape never substitutes for accepted-effect settlement."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_vpngw.deploy import repair_retirement, vm_ha_lifecycle


@pytest.fixture
def owner(tmp_path, monkeypatch):
    lifecycle = tmp_path / "lifecycle.json"
    saves = []
    value = SimpleNamespace(
        context={},
        admission=SimpleNamespace(
            path=tmp_path / "config.yaml",
            intent={"project": "fixture-project", "gateway": "fixture-gateway"},
        ),
        cloud=SimpleNamespace(sdk=None),
        directory=tmp_path / "repair",
        save=lambda: saves.append(True),
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        vm_ha_lifecycle, "VMHALifecycleStore", lambda _: SimpleNamespace(path=lifecycle)
    )
    return value, lifecycle, saves


@pytest.mark.parametrize("transaction", ["broken", ["broken"], 42, True, None, {}, [], False, 0])
def test_retirement_accepts_opaque_invalid_transaction_shape(owner, transaction):
    value, lifecycle, saves = owner
    original = json.dumps({"transaction": transaction}).encode()
    lifecycle.write_bytes(original)
    lifecycle.chmod(0o600)

    repair_retirement.retire(value)

    assert value.context == {"metadata_retired": True}
    assert saves == [True]
    # Lifecycle publication owns its later replacement; retirement preserves it.
    assert lifecycle.read_bytes() == original


def test_invalid_transaction_shape_does_not_hide_nested_accepted_effect(owner):
    value, lifecycle, saves = owner
    original = json.dumps(
        {
            "transaction": [
                {
                    "status": "accepted",
                    "cloud_operation_id": "fixture-operation",
                    "request_type": "UnsupportedRequest",
                }
            ]
        }
    ).encode()
    lifecycle.write_bytes(original)
    lifecycle.chmod(0o600)

    with pytest.raises(RuntimeError, match="no originating service binding"):
        repair_retirement.retire(value)

    assert value.context == {}
    assert saves == []
    assert lifecycle.read_bytes() == original
