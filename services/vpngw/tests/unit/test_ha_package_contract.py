"""Approved package snapshots agree at the actual guest admission boundary."""

from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from zipfile import ZipFile

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


@pytest.mark.parametrize("retained", [False, True])
@pytest.mark.parametrize("retirement_fails", [False, True])
def test_guarded_package_retires_assets_under_writer_lock_before_completion(
    admission, tmp_path, monkeypatch, retained, retirement_fails
):
    request, _ = admission
    manifest = request["package"]["desired"]
    manifest.update(config_sha256="a" * 64, package_identity="b" * 64,
                    wheel_name="release.whl", assets={},
                    asset_retirement={"predecessor": {"operation": "c" * 32},
                                      "removals": {"obsolete": {}}})
    with ZipFile(tmp_path / "payload" / "release.whl", "w"):
        pass
    events = []
    locked = False

    @contextmanager
    def writer_locks(**_kwargs):
        nonlocal locked
        locked = True
        try:
            yield
        finally:
            locked = False

    def retire(selected):
        assert locked and selected == manifest
        events.append("retire")
        if retirement_fails:
            raise RuntimeError("asset_retirement_changed")

    if retained:
        guest.MARKER.touch()
        forwarding = tmp_path / "forwarding"
        forwarding.write_text("0")
        monkeypatch.setattr(guest.repair, "FORWARDING", forwarding)
        monkeypatch.setattr(guest.repair, "exact_lock", lambda _: None)
        monkeypatch.setattr(guest.repair, "cold_guard", lambda **_: None)
        boot = tmp_path / "boot"
        boot.write_text("current-boot")
        monkeypatch.setattr(guest.ops, "BOOT", boot)
        request["expected"] = {"exact": True}
    monkeypatch.setattr(guest, "initialize_unstarted_writer_layout", lambda: None)
    monkeypatch.setattr(guest.repair, "writer_locks", writer_locks)
    monkeypatch.setattr(guest.ops, "Manager", lambda _: SimpleNamespace(
        network=lambda: {}, unit=lambda _: {"missing": True}, stopped=lambda _: True))
    monkeypatch.setattr(guest.ops, "install_startup_guard", lambda **_: None)
    operation = SimpleNamespace(
        check=lambda: None, phase=events.append, fail=lambda: None,
        reload_manager=lambda: events.append("reload"),
        complete=lambda: events.append("complete"))
    monkeypatch.setattr(guest.ops.Operation, "begin", lambda **_: operation)
    monkeypatch.setattr(guest.remote.migration, "begin", lambda *_: None)
    monkeypatch.setattr(guest.remote.migration, "inhibit", lambda: None)
    monkeypatch.setattr(guest.remote.migration, "install", lambda: None)
    monkeypatch.setattr(guest.remote.migration, "retire_assets", retire)
    monkeypatch.setattr(guest.remote.python_packages, "install_payload", lambda *_: None)
    monkeypatch.setattr(guest, "verify_package", lambda _: {"verified": True})
    if retirement_fails:
        with pytest.raises(RuntimeError, match="asset_retirement_changed"):
            guest.prepare_guarded(request)
        assert "complete" not in events
    else:
        assert guest.prepare_guarded(request) == {"verified": True}
        assert events[-2:] == ["retire", "complete"]
    assert events[:3] == ["install", "publish", "reload"]
    assert not locked
