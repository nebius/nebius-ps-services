"""Deployment admission is independent from runtime ownership and readiness."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from nebius_vpngw import routing_semantics as semantics


@pytest.fixture
def guest(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text('{"gateway": {}, "connections": []}')
    path = tmp_path / "routing-semantics.json"
    monkeypatch.setattr(semantics, "PATH", path)
    monkeypatch.setattr(semantics.underlay, "CONFIG", config)
    package = ["b" * 64]
    ready = [True]
    protected = [True]

    def verify(cfg):
        if not ready[0]:
            raise RuntimeError("underlay unavailable")
        return "proof"

    def protection():
        if not protected[0]:
            raise RuntimeError("destination protection unavailable")

    monkeypatch.setattr(semantics, "_package", lambda: package[0])
    monkeypatch.setattr(semantics.underlay, "verify", verify)
    monkeypatch.setattr(semantics, "_protection", lambda cfg: SimpleNamespace(verify=protection))
    return SimpleNamespace(
        config=config, path=path, package=package, ready=ready, protected=protected
    )


def admit(guest):
    semantics.admit_ordinary_locked(
        config_sha256=hashlib.sha256(guest.config.read_bytes()).hexdigest(),
        package_identity=guest.package[0],
        operation="apply-1",
    )


def test_copying_package_and_configuration_cannot_enable_runtime(guest):
    with pytest.raises(RuntimeError, match="normal apply"):
        semantics.require()
    assert not guest.path.exists()
    admit(guest)
    assert len(semantics.require()) == 64


@pytest.mark.parametrize("change", ["config", "package", "underlay", "protection", "revision"])
def test_admission_rechecks_every_owned_boundary(guest, change):
    admit(guest)
    original = guest.path.read_bytes()
    if change == "config":
        guest.config.write_text('{"gateway": {"local_prefixes": ["10.64.0.0/16"]}}')
    elif change == "package":
        guest.package[0] = "c" * 64
    elif change == "underlay":
        guest.ready[0] = False
    elif change == "protection":
        guest.protected[0] = False
    else:
        guest.path.write_text(json.dumps({**json.loads(original), "revision": "unknown"}))
    with pytest.raises(RuntimeError):
        semantics.require()
    if change != "revision":
        assert guest.path.read_bytes() == original


def test_unchanged_apply_preserves_runtime_binding_and_pending_semantic_identity(
    guest, monkeypatch
):
    admit(guest)
    desired = {"generation": hashlib.sha256(guest.config.read_bytes()).hexdigest()}
    monkeypatch.setattr("nebius_vpngw.ordinary_route_binding.validate", lambda value: value)
    semantics.bind_ordinary_locked(desired)
    before = guest.path.read_bytes()
    identity = semantics.require(runtime_binding=desired)
    admit(guest)
    assert guest.path.read_bytes() == before
    assert semantics.require(runtime_binding=desired) == identity
    with pytest.raises(RuntimeError, match="exact deployment"):
        semantics.require(runtime_binding={"generation": "c" * 64})


def test_failed_preparation_cannot_publish_admission(guest):
    guest.ready[0] = False
    with pytest.raises(RuntimeError, match="underlay"):
        admit(guest)
    assert not guest.path.exists()


def test_pending_intent_cannot_be_adopted_or_replayed_under_another_admission(tmp_path):
    store = SimpleNamespace(
        pending_path=tmp_path / "route-pending.json",
        load_pending_mutation=lambda: None,
        _mutation_payload=lambda value: value,
    )
    context = SimpleNamespace(to_dict=lambda: {"operation_id": "op"})
    mutation = {"prefix": "10.0.0.0/8", "route_id": "one"}
    with pytest.raises(RuntimeError, match="lacks semantic"):
        semantics.pending_admission(store, mutation, context, "a" * 64)
    semantics.pending_admission(store, mutation, context, "a" * 64, publish=True)
    semantics.pending_admission(store, mutation, context, "a" * 64)
    for changed, identity in [(mutation, "b" * 64), ({**mutation, "route_id": "two"}, "a" * 64)]:
        with pytest.raises(RuntimeError, match="different routing"):
            semantics.pending_admission(store, changed, context, identity)
    store.load_pending_mutation = lambda: object()
    with pytest.raises(RuntimeError, match="previously pending"):
        semantics.pending_admission(store, mutation, context, "a" * 64, publish=True)


def test_support_projection_retains_routing_proof_without_owned_config_bytes():
    from nebius_vpngw.support_bundle.probe import valid_remote_name
    from nebius_vpngw.support_bundle.projection import project

    assert valid_remote_name("state/underlay")
    assert valid_remote_name("state/routing-admission")
    assert valid_remote_name("ha-0/route-pending-admission.json")
    assert not valid_remote_name("state/../../credentials")
    projected = project(
        {
            "schema": "nebius-vpngw.underlay.v1",
            "phase": "committed",
            "intent": "a" * 64,
            "content": "private configuration bytes",
            "network": {"network_file": "/private/path"},
            "destinations": ["10.64.0.0/16"],
        }
    )
    assert projected["underlay_intent_sha256"] == "a" * 64
    assert projected["destinations"] == ["10.64.0.0/16"]
    assert "content" not in projected
    assert "network_file" not in projected["network"]
