"""Real routing-lock contention must remain an observation, not a repair fault."""

import json
import os
from types import SimpleNamespace as NS

import pytest

from nebius_vpngw import ha_activation as guest
from nebius_vpngw.agent import main as agent
from nebius_vpngw.agent import routing_guard
from nebius_vpngw.deploy import activation_driver as driver
from nebius_vpngw.deploy.activation_recovery import Recovery
from nebius_vpngw.deploy.apply_diagnostics import ObservationUnavailable


@pytest.mark.parametrize("release_after_first_read", [True, False])
def test_materialization_contention_reobserves_without_a_corrective_attempt(
    tmp_path, monkeypatch, release_after_first_read
):
    clock = [100.0]
    sleeps = []

    def sleep(seconds):
        assert 0 < seconds <= 0.2
        sleeps.append(seconds)
        clock[0] += seconds

    time = NS(monotonic=lambda: clock[0], sleep=sleep)
    monkeypatch.setattr(driver, "time", time)
    monkeypatch.setattr(guest, "time", time)
    monkeypatch.setattr(guest, "STATE", tmp_path)
    monkeypatch.setattr(guest, "require_authority", lambda _: "boot")
    monkeypatch.setattr(
        guest,
        "unit_state",
        lambda *_a, **_k: {"Job": "", "ControlPID": "0", "ActiveState": "active"},
    )
    monkeypatch.setattr(
        agent,
        "_vm_ha_materialization_authorized",
        lambda **_: {"generation": {"generation_id": "c" * 64}, "node": {"node_id": "node"}},
    )
    monkeypatch.setattr(agent, "_boot_id", lambda: "boot")
    monkeypatch.setattr(routing_guard, "LOCK_PATH", tmp_path / "routing.lock")
    lock = {
        "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
        "apply_locked": True,
        "cluster_id": "cluster",
        "node_id": "node",
        "generation_id": "c" * 64,
        "operation_id": "e" * 64,
    }
    (tmp_path / "materialization.json").write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw/vm-ha-materialization-v1",
                "boot_id": "boot",
                "generation_id": "c" * 64,
                "node_id": "node",
            }
        )
    )
    (tmp_path / "activation-complete.json").write_text(
        json.dumps(
            {
                "schema": "nebius-vpngw.activation-complete.v1",
                "boot": "boot",
                "lock": lock,
                "configuration": "d" * 64,
            }
        )
    )
    (tmp_path / "activation-complete.json").chmod(0o600)
    writer = routing_guard.acquire_routing_lock(blocking=False)
    assert writer is not None
    held = [writer]
    observed = []
    deadlines = []

    def control(_target, _instance, _local, request, *, deadline):
        assert request["action"] == "inspect", "Observation must never grant a repair"
        assert request["lock"] == lock
        deadlines.append(deadline)
        if observed and release_after_first_read and held:
            os.close(held.pop())
        value = guest.inspect(request, deadline=deadline)
        observed.append(value["materialized"])
        return value

    owner = Recovery(
        tmp_path / "command", execution="a" * 32, authority="b" * 64, clock=time.monotonic
    )
    owner.initialize()
    owner.initial("activate-node", state="started")
    before = owner.path.read_bytes()

    def run():
        driver.activate(
            NS(activation_control=control),
            "target",
            NS(),
            {},
            recovery=owner,
            staged_receipt=NS(node_id="node", generation_id="c" * 64, staged_file_sha256="d" * 64),
            runtime_binding=NS(cluster_id="cluster"),
            apply_operation_id="e" * 64,
        )

    try:
        if release_after_first_read:
            run()
            assert observed == [None, True]
            assert owner.initial("activate-node") == "verified"
        else:
            with pytest.raises(ObservationUnavailable) as error:
                run()
            assert error.value.retryable
            assert owner.path.read_bytes() == before
            assert observed and set(observed) == {None}
            assert clock[0] == pytest.approx(120)
        assert sleeps and set(deadlines) == {120}
        assert owner.history() == []
    finally:
        for fd in held:
            os.close(fd)
