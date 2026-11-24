import json
import os
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml
from test_enforced_guest import Kernel

from nebius_vpngw import enforced_handoff as handoff
from nebius_vpngw import ha_repair
from nebius_vpngw.agent.enforced_routing import PolicyRouting
from nebius_vpngw.ha_tunnel_policy import connection_policies


@pytest.fixture
def conversion(tmp_path, sample_config, monkeypatch):
    # Guest publication is root-owned. Simulate only ancestor metadata on the
    # non-root development host; actual test files keep their real modes.
    lstat = Path.lstat
    ancestors = set(tmp_path.parents)

    def metadata(path, *args, **kwargs):
        value = lstat(path, *args, **kwargs)
        if path in ancestors:
            fields = list(value)
            fields[0] &= ~0o022
            fields[4] = os.geteuid()
            return os.stat_result(fields)
        return value

    monkeypatch.setattr(Path, "lstat", metadata)
    root = tmp_path / "vm-ha"
    root.mkdir(mode=0o700)
    boot, forwarding = tmp_path / "boot", tmp_path / "forwarding"
    boot.write_text("boot")
    forwarding.write_text("0")
    kernel = Kernel()
    monkeypatch.setattr(
        handoff,
        "policy",
        lambda path, cluster, config: PolicyRouting(
            path, cluster=cluster, policies=connection_policies(config), run=kernel
        ),
    )
    expected = {
        "cluster_id": "cluster",
        "node_id": "node",
        "generation_id": "a" * 64,
        "operation_id": "b" * 64,
    }
    repair = NS(
        STATE=root,
        BOOT=boot,
        FORWARDING=forwarding,
        STATUS_SCHEMA=ha_repair.STATUS_SCHEMA,
        read_json=ha_repair.read_json,
        publish=ha_repair.publish,
        verified_file=ha_repair.verified_file,
        sync_directory=ha_repair.sync_directory,
        exact_lock=lambda value: dict(expected) if value == expected else pytest.fail("wrong lock"),
    )

    def cold_guard(**kwargs):
        forwarding.write_text("0")
        repair.publish(
            root / "guard.json",
            json.dumps(
                {
                    "schema": repair.STATUS_SCHEMA,
                    "guard_boot_id": "boot",
                    "data_plane_mode": "blocked",
                }
            ).encode(),
        )

    repair.cold_guard = cold_guard
    cfg = deepcopy(sample_config)
    cfg["connections"][0]["enforced_routing"] = {
        "source_subnets": ["10.0.1.0/24"],
        "destinations": ["0.0.0.0/0"],
    }
    original = handoff.policy(handoff.source_path(repair), "ordinary", cfg)
    original.install()
    before = handoff.evidence(repair, cfg)
    staged = deepcopy(cfg)
    staged["connections"][0]["enforced_routing"]["source_subnets"].append("10.0.2.0/24")
    staged["vm_ha"] = {
        "cluster_id": "cluster",
        "node": {"node_id": "node"},
        "generation": {"generation_id": "a" * 64},
        "runtime_binding": {"route_runtime_id": "runtime", "shared_allocation_id": "allocation"},
    }
    staged_root = tmp_path / "staged"
    staged_root.mkdir(mode=0o700)
    monkeypatch.setattr(handoff, "STAGED_ROOT", staged_root)
    raw = yaml.safe_dump(staged).encode()
    repair.publish(staged_root / ("a" * 64 + ".yaml"), raw)
    directory = tmp_path / "packages/candidate"
    directory.mkdir(parents=True, mode=0o700)
    directory.parent.chmod(0o700)
    binding_path = directory.parent / "holder.json"
    repair.publish(binding_path, b"{}")
    stopped, effects = set(), []

    def service(action, unit, **kwargs):
        assert action == "stop"
        stopped.add(unit)
        effects.append(unit)

    holder = NS(
        check=lambda: None,
        prepared=True,
        repairing=False,
        directory=directory,
        request={
            "ha": {k: v for k, v in expected.items() if k != "operation_id"},
            "observed": {"enforced_routing": before},
        },
        operation=NS(
            value={"id": "holder"}, manager=NS(stopped=lambda u: u in stopped), service=service
        ),
    )
    payload = {
        "operation": expected["operation_id"],
        "receipt": {"staged_file_sha256": ha_repair.sha(raw)},
        "route_runtime_id": "runtime",
        "allocation_id": "allocation",
        "units": ["controller", "frr"],
    }
    return NS(
        holder=holder,
        payload=payload,
        repair=repair,
        ops=NS(BOOT=boot),
        original=original,
        before=before,
        staged=staged,
        binding_path=binding_path,
        kernel=kernel,
        effects=effects,
        cfg=cfg,
    )


def test_conversion_preserves_guard_and_replays_partial_rule_install(conversion):
    x = conversion
    x.kernel.fail_add = True
    with pytest.raises(RuntimeError, match="mutation"):
        handoff.prepared(x.holder, x.payload, x.repair, x.ops, install=True)
    binding = x.repair.read_json(x.binding_path)
    assert handoff.evidence(x.repair, x.cfg, binding=binding) == x.before
    result = handoff.prepared(x.holder, x.payload, x.repair, x.ops, install=True)
    assert result["boot_id"] == "boot"
    assert x.effects == ["controller", "frr"]
    assert len(x.original.read()) == 2
    handoff.prepared(x.holder, x.payload, x.repair, x.ops, install=False)


def test_conversion_replays_envelope_publication_and_retires_only_source(conversion):
    x = conversion
    handoff.prepared(x.holder, x.payload, x.repair, x.ops, install=True)
    binding = x.repair.read_json(x.binding_path)
    kernel_before = deepcopy((x.kernel.rules, x.kernel.guard))
    publish = x.repair.publish

    def interrupted(path, raw, *args):
        if path == x.binding_path and json.loads(raw)[handoff.KEY]["phase"] == "transferred":
            raise TimeoutError("receipt publication interrupted")
        return publish(path, raw, *args)

    x.repair.publish = interrupted
    with pytest.raises(TimeoutError):
        handoff.transfer(x.holder, binding, x.binding_path, x.staged, x.repair)
    x.repair.publish = publish
    handoff.transfer(
        x.holder, x.repair.read_json(x.binding_path), x.binding_path, x.staged, x.repair
    )
    assert x.original.path.exists()
    handoff.complete(x.holder, x.repair)
    handoff.complete(x.holder, x.repair)
    assert not x.original.path.exists()
    assert (x.kernel.rules, x.kernel.guard) == kernel_before
    handoff.policy(x.repair.STATE / "enforced-routing.json", "cluster", x.staged).verify()


def test_oversized_transfer_fails_before_receipt_or_kernel_changes(conversion):
    x = conversion
    raw = json.dumps({"existing": "x" * (1024 * 1024 - 100)}).encode()
    x.repair.publish(x.binding_path, raw)
    before = deepcopy((x.kernel.rules, x.kernel.guard))
    with pytest.raises(RuntimeError, match="receipt exceeds"):
        handoff.prepared(x.holder, x.payload, x.repair, x.ops, install=True)
    assert x.binding_path.read_bytes() == raw
    assert x.original.read() == x.before
    assert not x.effects
    assert (x.kernel.rules, x.kernel.guard) == before


def test_conversion_rejects_other_generation_and_destination_owner(conversion):
    x = conversion
    changed = {**x.payload, "route_runtime_id": "foreign"}
    with pytest.raises(RuntimeError, match="authority differs"):
        handoff.prepared(x.holder, changed, x.repair, x.ops, install=True)
    handoff.prepared(x.holder, x.payload, x.repair, x.ops, install=True)
    x.repair.publish(x.repair.STATE / "enforced-routing.json", b'{"schema":"foreign"}')
    with pytest.raises(RuntimeError):
        handoff.transfer(
            x.holder, x.repair.read_json(x.binding_path), x.binding_path, x.staged, x.repair
        )
    assert x.original.path.exists()


def test_conversion_transfer_replay_after_source_retirement_requires_exact_destination(conversion):
    x = conversion
    handoff.prepared(x.holder, x.payload, x.repair, x.ops, install=True)
    handoff.transfer(
        x.holder, x.repair.read_json(x.binding_path), x.binding_path, x.staged, x.repair
    )
    handoff.complete(x.holder, x.repair)
    binding = x.repair.read_json(x.binding_path)
    handoff.transfer(x.holder, binding, x.binding_path, x.staged, x.repair)
    assert not x.original.path.exists()
    (x.repair.STATE / "enforced-routing.json").unlink()
    with pytest.raises(RuntimeError, match="destination"):
        handoff.transfer(x.holder, binding, x.binding_path, x.staged, x.repair)


@pytest.mark.parametrize("retain", [True, False])
def test_ha_to_ordinary_preserves_guard_and_replays_before_ha_teardown(conversion, retain):
    x = conversion
    handoff.prepared(x.holder, x.payload, x.repair, x.ops, install=True)
    handoff.transfer(
        x.holder, x.repair.read_json(x.binding_path), x.binding_path, x.staged, x.repair
    )
    handoff.complete(x.holder, x.repair)
    rules = deepcopy(x.kernel.rules)
    ha_path = x.repair.STATE / "enforced-routing.json"
    original_lock = x.repair.exact_lock
    x.repair.exact_lock = lambda expected: original_lock({**expected, "operation_id": "b" * 64})
    publish = x.repair.publish

    def interrupted(path, raw, *args):
        publish(path, raw, *args)
        if path == x.original.path:
            raise TimeoutError("lost target publication reply")

    x.repair.publish = interrupted
    with pytest.raises(TimeoutError):
        handoff.preserve_for_ordinary(
            x.repair, x.staged, x.holder.operation.manager, x.payload["units"]
        )
    assert ha_path.exists() and x.kernel.rules == rules
    x.repair.publish = publish
    handoff.preserve_for_ordinary(
        x.repair, x.staged, x.holder.operation.manager, x.payload["units"]
    )
    ha_path.unlink()  # Existing deactivation owns HA state cleanup after transfer.
    ordinary = deepcopy(x.staged)
    ordinary.pop("vm_ha")
    if not retain:
        ordinary["connections"][0].pop("enforced_routing")
    target = handoff.policy(x.original.path, "ordinary", ordinary)
    target.install()
    assert x.kernel.rules == rules
    target.retire()  # Only the completed ordinary cloud transaction authorizes retirement.
    assert bool(x.kernel.rules) is retain
    target.verify()
