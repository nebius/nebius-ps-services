from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from tests.unit.test_vm_ha_controller import _owned_cloud, _proven_transfer_checkpoint, _snapshot
from tests.unit.test_vm_ha_runtime_ports import (
    FakeDataPlane,
    FakeRouteBackend,
    FakeSDK,
    MemoryReplayStore,
    _fake_credential_bundle,
    _fake_mtls_snapshot,
    _runtime_config,
)
from tests.unit.test_vm_ha_static_selection import Kernel, action, config, runtime

import nebius_vpngw.agent.strongswan_renderer as strongswan
from nebius_vpngw.agent.vm_ha.runtime import (
    BGPExportState,
    CommandResult,
    DataPlaneCommandSet,
    LocalDataPlaneObservation,
    SystemDataPlaneRuntime,
    _named_ike_sa_ids,
    _static_connections_absent,
    build_runtime_ports,
)
from nebius_vpngw.agent.vm_ha_controller import (
    ActionKind,
    ComputeState,
    ControllerAction,
    ControllerCheckpoint,
    DataPlaneMode,
    LocalReadiness,
    TransferIntent,
    VMHAController,
)


def mixed_config() -> dict:
    return {
        "defaults": {"routing": {"mode": "static"}},
        "gateway": {"local_prefixes": ["10.81.0.0/16"]},
        "connections": [
            {
                "name": "uk",
                "remote_prefixes": ["10.80.1.0/24"],
                "tunnels": [
                    {"name": "uk-primary", "remote_public_ip": "192.0.2.1", "psk": "test"},
                    {"name": "uk-backup", "remote_public_ip": "192.0.2.2", "psk": "test"},
                ],
            },
            {
                "name": "home",
                "routing_mode": "bgp",
                "bgp": {"remote_asn": 64560},
                "tunnels": [
                    {
                        "name": "home-bgp",
                        "remote_public_ip": "192.0.2.3",
                        "inner_remote_ip": "169.254.10.2",
                        "psk": "test",
                    }
                ],
            },
        ],
    }


@pytest.fixture
def renderer_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(strongswan, "SWANCTL_CONF", tmp_path / "swanctl.conf")
    monkeypatch.setattr(strongswan, "IPSEC_CONF", tmp_path / "ipsec.conf")
    monkeypatch.setattr(strongswan, "STRONGSWAN_CONF_DIR", tmp_path / "charon")
    monkeypatch.setattr(strongswan, "NETPLAN_DIR", tmp_path / "netplan")
    monkeypatch.setattr(strongswan, "_wait_for_vici_socket", lambda: True)
    calls = []

    def run(argv, **kwargs):
        calls.append(tuple(argv))
        output = ""
        if "--list-sas" in argv:
            output = "list-sa event {home-bgp {uniqueid=9 version=2}}\nlist-sas reply {}"
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(strongswan, "run", run)
    return calls


@pytest.mark.parametrize("reverse", [False, True])
def test_standby_excludes_static_responders_without_renumbering_bgp(renderer_environment, reverse):
    cfg = mixed_config()
    if reverse:
        cfg["connections"].reverse()
    renderer = strongswan.StrongSwanRenderer()
    active = {}
    passive = {}
    renderer.render_and_apply(cfg, rendered_files=active)
    endpoints = renderer.render_and_apply(cfg, standby=True, rendered_files=passive)
    text = passive[strongswan.SWANCTL_CONF]
    assert "uk-primary {" not in text
    assert "uk-backup {" not in text
    assert "home-bgp {" in text
    bgp_id = 100 if reverse else 102
    assert f"if_id_in = {bgp_id}" in text
    assert f"if_id_in = {bgp_id}" in active[strongswan.SWANCTL_CONF]
    assert len(endpoints) == 3
    assert renderer_environment == []


def test_standby_reload_preserves_running_daemon_and_candidate_publish_is_inert(
    renderer_environment,
):
    renderer = strongswan.StrongSwanRenderer()
    renderer.render_and_apply(mixed_config(), standby=True)
    assert not any("restart" in command for command in renderer_environment)
    assert ("swanctl", "--load-all") in renderer_environment
    assert "uk-primary {" not in strongswan.SWANCTL_CONF.read_text()
    renderer_environment.clear()
    renderer.prepare_candidate(mixed_config())
    text = strongswan.SWANCTL_CONF.read_text()
    assert "uk-primary {" in text
    assert "start_action = start" in text
    assert strongswan.SWANCTL_CONF.stat().st_mode & 0o777 == 0o600
    assert renderer_environment == []


def test_mixed_passive_terminates_only_static_sas(renderer_environment, tmp_path):
    calls = []
    forwarding = "1"
    sas = {"uk-primary": "7", "uk-backup": "8", "home-bgp": "9"}

    def raw_sas():
        events = []
        for name, ike_id in sas.items():
            events.append(f"list-sa event {{{name} {{uniqueid={ike_id} version=2}}}}")
        return "\n".join([*events, "list-sas reply {}"])

    def runner(argv, timeout):
        nonlocal forwarding
        calls.append(tuple(argv))
        if argv[0] == DataPlaneCommandSet().systemctl:
            if "reload-or-restart" in argv:
                strongswan.StrongSwanRenderer().render_and_apply(mixed_config(), standby=True)
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().python:
            return CommandResult(0)
        if argv[0] == DataPlaneCommandSet().sysctl:
            if "-w" in argv:
                forwarding = argv[-1].split("=")[-1]
            return CommandResult(0, forwarding)
        if "--list-sas" in argv:
            return CommandResult(0, raw_sas())
        if "--list-conns" in argv:
            return CommandResult(0, "list-conn event {home-bgp {}}\nlist-conns reply {}")
        if "--terminate" in argv:
            ike_id = argv[argv.index("--ike-id") + 1]
            for name in list(sas):
                if sas[name] == ike_id:
                    del sas[name]
        return CommandResult(0)

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        guard_path=tmp_path / "guard.json",
        routing_lock_path=tmp_path / "routing.lock",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest="c" * 64,
        passive_static_tunnels=("uk-primary", "uk-backup"),
        runner=runner,
    )
    snapshot = _snapshot()
    action = ControllerAction(
        ActionKind.ENTER_PASSIVE,
        "passive",
        snapshot.boot_id,
        snapshot.local_node_id,
        "allocation",
        "epoch",
        snapshot.local_generation_id,
        snapshot.local_digests,
    )
    runtime.enter_passive(action)
    runtime.enter_passive(replace(action, operation_id="passive-repeat"))
    assert forwarding == "0"
    assert sas == {"home-bgp": "9"}
    assert sum("--terminate" in command for command in calls) == 2
    assert not any("--load-all" in command for command in calls)
    assert not any("restart" in command for command in renderer_environment)


def mixed_ports(tmp_path, data_plane_factory=FakeDataPlane):
    cfg = _runtime_config(tmp_path)
    cfg.update(mixed_config())
    manifests = {
        "static_routes": [{"connection": "uk", "remote_prefixes": ["10.80.1.0/24"]}],
        "bgp_policy": [{"connection": "home", "remote_prefixes": ["10.90.0.0/24"]}],
    }
    for key, records in manifests.items():
        raw = json.dumps(records, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(raw.encode()).hexdigest()
        cfg["generation"]["logical_manifests"][key + "_json"] = raw
        cfg["generation"]["digests"][key] = digest
        cfg["readiness"]["digests"][key] = digest
        cfg["runtime_binding"][key + "_digest"] = digest
    return build_runtime_ports(
        cfg,
        state_dir=tmp_path / "state",
        replay_store=MemoryReplayStore(),
        sdk_factory=lambda **kwargs: FakeSDK(),
        credential_bundle_factory=_fake_credential_bundle,
        mtls_snapshot_provider=_fake_mtls_snapshot,
        route_backend_factory=lambda sdk: FakeRouteBackend(),
        data_plane_factory=data_plane_factory,
    )


@pytest.mark.parametrize("bgp_up,exports_match", [(True, True), (False, True), (True, False)])
def test_mixed_standby_requires_live_bgp_but_no_static_sas(tmp_path, bgp_up, exports_match):
    ports = mixed_ports(tmp_path)
    try:
        observation = LocalDataPlaneObservation(
            service_healthy=True,
            forwarding_enabled=False,
            static_prefixes=frozenset({"10.90.0.0/24"}),
            configured_bgp_sessions=frozenset({"169.254.10.2"}),
            established_bgp_sessions=frozenset({"169.254.10.2"}) if bgp_up else frozenset(),
            learned_bgp_prefixes=frozenset({"10.90.0.0/24"}),
            usable_xfrm_prefixes=frozenset({"10.90.0.0/24"}),
            observed_bgp_policy_digest=ports.routes.expected_import_policy_digest,
            established_ike_sa_count=1,
            static_tunnels_suspended=True,
            bgp_export_state=BGPExportState.MATCH if exports_match else BGPExportState.DRIFT,
        )
        readiness = ports.routes.readiness_from_observation(observation, DataPlaneMode.PASSIVE)
        assert readiness.candidate_preparation_required
        assert readiness.cold_standby_ready is (bgp_up and exports_match)
        assert readiness.transfer_ready is (bgp_up and exports_match)
        assert not readiness.promotion_ready
        leaked = ports.routes.readiness_from_observation(
            replace(observation, static_tunnels_suspended=False), DataPlaneMode.PASSIVE
        )
        assert not leaked.transfer_ready
        assert not leaked.standby_static_tunnels_ready
    finally:
        ports.close()


def test_non_owner_repairs_static_tunnel_leak_without_starting_takeover():
    readiness = LocalReadiness(True, True, True, True, standby_static_tunnels_ready=False)
    snapshot = _snapshot(readiness=readiness)
    policy = VMHAController(peer_timeout_seconds=10, suspicion_seconds=5)
    result = policy.decide(snapshot, ControllerCheckpoint())
    assert result.action.kind is ActionKind.ENTER_PASSIVE
    assert result.reasons == ("standby-static-tunnels-require-suspension",)
    assert not policy.pending_action_postcondition(result.action, snapshot, result.checkpoint)


def test_static_observation_distinguishes_responder_config_from_established_sa():
    names = frozenset({"uk-primary"})
    raw = "list-sa event {home-bgp {uniqueid=9 version=2}}\nlist-sas reply {}"
    assert _named_ike_sa_ids(raw, names) == ()
    assert not _static_connections_absent(
        CommandResult(0, "list-conn event {uk-primary {}}\nlist-conns reply {}"), names
    )
    with pytest.raises(RuntimeError, match="malformed"):
        _static_connections_absent(CommandResult(0, "partial response"), names)


@pytest.mark.parametrize(
    "changes",
    [
        {"former_owner_compute_state": ComputeState.RUNNING},
        {"former_attachment_absent": False},
        {"candidate_attachment_exact": False},
        {"ownership_re_read_exact": False},
    ],
)
def test_mixed_candidate_cannot_activate_static_tunnels_before_transfer_proof(changes):
    readiness = LocalReadiness(
        True,
        False,
        True,
        False,
        cold_standby_ready=True,
        candidate_preparation_required=True,
    )
    snapshot = _snapshot(
        cloud=replace(_owned_cloud(), **changes),
        readiness=readiness,
        transfer_intent=TransferIntent.PLANNED_FAILOVER,
    )
    policy = VMHAController(peer_timeout_seconds=10, suspicion_seconds=5)
    decision = policy.decide(snapshot, _proven_transfer_checkpoint(snapshot))
    assert decision.action is None or decision.action.kind not in {
        ActionKind.PREPARE_CANDIDATE_DATAPLANE,
        ActionKind.RECONCILE_ROUTES,
        ActionKind.ENABLE_ACTIVE,
    }
    assert not decision.forwarding_enabled


def test_mixed_candidate_activates_all_static_tunnels_under_one_gateway_owner(tmp_path):
    ports = mixed_ports(tmp_path)
    try:
        cold_observation = LocalDataPlaneObservation(
            service_healthy=True,
            forwarding_enabled=False,
            static_prefixes=frozenset(),
            configured_bgp_sessions=frozenset({"169.254.10.2"}),
            established_bgp_sessions=frozenset({"169.254.10.2"}),
            learned_bgp_prefixes=frozenset({"10.90.0.0/24"}),
            usable_xfrm_prefixes=frozenset({"10.90.0.0/24"}),
            observed_bgp_policy_digest=ports.routes.expected_import_policy_digest,
            static_tunnels_suspended=True,
            established_ike_sa_count=1,
            bgp_export_state=BGPExportState.MATCH,
        )
        policy = VMHAController(peer_timeout_seconds=10, suspicion_seconds=5)
        snapshot = _snapshot(
            cloud=_owned_cloud(),
            readiness=ports.routes.readiness_from_observation(
                cold_observation, DataPlaneMode.PASSIVE
            ),
            transfer_intent=TransferIntent.PLANNED_FAILOVER,
        )
        prepared = policy.decide(snapshot, _proven_transfer_checkpoint(snapshot))
        assert prepared.action.kind is ActionKind.PREPARE_CANDIDATE_DATAPLANE
        # A later fresh sample must prove the static path before route ownership
        # reconciliation. The candidate still cannot forward during this step.
        warm_observation = replace(
            cold_observation,
            static_tunnels_suspended=False,
            usable_xfrm_prefixes=frozenset({"10.90.0.0/24", "10.80.1.0/24"}),
        )
        warm = ports.routes.readiness_from_observation(warm_observation, DataPlaneMode.PASSIVE)
        routes = policy.decide(replace(snapshot, readiness=warm), prepared.checkpoint)
        assert routes.action.kind is ActionKind.RECONCILE_ROUTES
        assert not routes.forwarding_enabled
    finally:
        ports.close()


@pytest.mark.parametrize("static_establishes", [True, False])
def test_candidate_waits_for_static_sa_without_restarting_bgp(
    renderer_environment, tmp_path, static_establishes
):
    calls = []
    polls = 0
    renderer = strongswan.StrongSwanRenderer()
    renderer.render_and_apply(mixed_config(), standby=True)
    renderer_environment.clear()

    def runner(argv, timeout):
        nonlocal polls
        calls.append(tuple(argv))
        if argv[0] == DataPlaneCommandSet().sysctl:
            return CommandResult(0, "0")
        if "--load-all" in argv:
            assert "uk-primary {" in strongswan.SWANCTL_CONF.read_text()
            return CommandResult(0)
        if "--list-sas" in argv:
            polls += 1
            events = ["list-sa event {home-bgp {uniqueid=9 version=2}}"]
            if static_establishes and polls > 1:
                events.append("list-sa event {uk-primary {uniqueid=10 version=2}}")
            return CommandResult(0, "\n".join([*events, "list-sas reply {}"]))
        raise AssertionError(argv)

    runtime = SystemDataPlaneRuntime(
        state_path=tmp_path / "data-plane.json",
        guard_path=tmp_path / "guard.json",
        routing_lock_path=tmp_path / "routing.lock",
        configured_bgp_sessions=(),
        expected_bgp_policy_digest="c" * 64,
        passive_static_tunnels=("uk-primary", "uk-backup"),
        candidate_preparer=lambda: renderer.prepare_candidate(mixed_config()),
        passive_materialization_attempts=2,
        passive_materialization_interval=0,
        runner=runner,
    )
    snapshot = _snapshot()
    action = ControllerAction(
        ActionKind.PREPARE_CANDIDATE_DATAPLANE,
        "candidate",
        snapshot.boot_id,
        snapshot.local_node_id,
        "allocation",
        "epoch",
        snapshot.local_generation_id,
        snapshot.local_digests,
    )
    runtime._write_state(action, DataPlaneMode.PASSIVE)
    runtime._write_guard(action, DataPlaneMode.PASSIVE)
    if static_establishes:
        runtime.prepare_candidate(action)
    else:
        with pytest.raises(RuntimeError, match="IKE SA establishment did not converge"):
            runtime.prepare_candidate(action)
    assert polls == 2
    assert renderer_environment == []
    assert runtime.mode() is DataPlaneMode.PASSIVE
    assert not any("net.ipv4.ip_forward=1" in command for command in calls)
    assert not any("--terminate" in command for command in calls)


def test_standby_unknown_service_query_never_restarts_daemon(renderer_environment, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(tuple(argv))
        code = 1 if argv[:2] == ["systemctl", "is-active"] else 0
        return SimpleNamespace(returncode=code, stdout="", stderr="")

    monkeypatch.setattr(strongswan, "run", run)
    with pytest.raises(RuntimeError, match="service state is unavailable"):
        strongswan.StrongSwanRenderer().render_and_apply(mixed_config(), standby=True)
    assert not any("restart" in command for command in calls)


def test_runtime_wires_static_names_from_canonical_inherited_routing(tmp_path):
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return FakeDataPlane(**kwargs)

    ports = mixed_ports(tmp_path, data_plane_factory=factory)
    try:
        assert captured["passive_static_tunnels"] == ("uk-primary", "uk-backup")
        assert captured["tunnel_cold_passive"] is False
    finally:
        ports.close()


def test_standby_bootstrap_without_sas_applies_daemon_settings(renderer_environment, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(tuple(argv))
        output = "list-sas reply {}" if "--list-sas" in argv else ""
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(strongswan, "run", run)
    strongswan.StrongSwanRenderer().render_and_apply(mixed_config(), standby=True)
    assert ("systemctl", "restart", "strongswan-starter") in calls
    assert "uk-primary {" not in strongswan.SWANCTL_CONF.read_text()


def test_active_owner_does_not_depend_on_standby_only_vici_observations(tmp_path):
    kernel = Kernel()
    kernel.forwarding = "1"
    kernel.usable = {"xfrm1"}
    data_plane = runtime(tmp_path, kernel, config(mixed=True))
    data_plane.passive_static_tunnels = frozenset({"uk-us-active", "uk-us-passive"})
    data_plane._write_state(action(), DataPlaneMode.ACTIVE)
    observation = data_plane.observe()
    assert observation.service_healthy
    assert observation.forwarding_enabled
    assert not any(command[0].endswith("swanctl") for command in kernel.calls)
