"""Approved package snapshots agree at the actual guest admission boundary."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from nebius_vpngw.deploy import handoff_remote as guest
from nebius_vpngw.deploy import vm_ha_package
from nebius_vpngw.ha_package_contract import PACKAGE_FIELDS


class Admitted(Exception):
    pass


@pytest.fixture
def admission(tmp_path, monkeypatch):
    observed = {key: {"verified": key} for key in PACKAGE_FIELDS}
    observed["asset_retirement"] = {"predecessor": None, "removals": {}}
    manifest = {"runtime_plan": {"exact": True}}
    payload = tmp_path / "payload"
    payload.mkdir()
    monkeypatch.setattr(guest.remote, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(guest.remote, "DURABLE_PACKAGES", False)
    monkeypatch.setattr(guest.remote, "inspect", lambda *a, **kw: deepcopy(observed))
    monkeypatch.setattr(guest.remote.migration, "pending", lambda _: None)
    monkeypatch.setattr(guest.remote.migration, "verify_staged", lambda _: tmp_path)
    monkeypatch.setattr(guest.repair, "read_json", lambda _: deepcopy(manifest))
    monkeypatch.setattr(guest, "MARKER", tmp_path / "absent-marker")
    monkeypatch.setattr(guest.repair, "STATE", tmp_path / "unstarted")
    monkeypatch.setattr(guest.ops, "Journal", lambda: SimpleNamespace(read=lambda: None))

    def begin(*_args):
        raise Admitted("all predecessor checks passed; no package mutation performed")

    monkeypatch.setattr(guest.remote.migration, "begin", begin)
    request = {"package": {"desired": manifest,
                           "predecessor": vm_ha_package.package_predecessor(observed)}}
    return request, observed


def test_full_cli_predecessor_reaches_guarded_guest_effect_boundary(admission):
    request, _ = admission
    with pytest.raises(Admitted):
        guest.prepare_guarded(request)


def test_original_omitted_retirement_field_reproduces_incident(admission, monkeypatch):
    request, _ = admission
    monkeypatch.setattr(guest, "package_predecessor", lambda observed: {
        key: observed[key] for key in PACKAGE_FIELDS if key != "asset_retirement"
    })
    with pytest.raises(RuntimeError, match="HA package predecessor changed"):
        guest.prepare_guarded(request)


@pytest.mark.parametrize("field", PACKAGE_FIELDS)
def test_every_approved_field_still_rejects_actual_drift(admission, field):
    request, observed = admission
    observed[field] = {"changed": True}
    with pytest.raises(RuntimeError, match="HA package predecessor changed"):
        guest.prepare_guarded(request)
