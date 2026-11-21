from __future__ import annotations

import copy
import io
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from rich.console import Console
from test_vm_ha_corrective import failed_release, record

from nebius_vpngw import operational_diagnostics, release_identity, runtime_migration
from nebius_vpngw.deploy import ordinary_remote, release_policy, vm_ha_corrective
from nebius_vpngw.support_bundle import checkpoints, projection


@pytest.mark.parametrize(
    "installed,selected,expected",
    [
        ("0.6.5", "0.6.6", "older"),
        ("0.6.6", "0.6.6", "matched"),
        ("0.6.7", "0.6.6", "newer"),
        ("0.6.6rc1", "0.6.6", "older"),
        ("0.6.6+local.1", "0.6.6", "newer"),
        ("bad version", "0.6.6", "unknown"),
    ],
)
def test_one_release_comparison(installed, selected, expected):
    assert release_policy.classify({"state": "present", "version": installed}, selected) == expected


def test_unknown_never_means_absent_or_permits_newer_recovery():
    with pytest.raises(release_policy.ReleaseBlocked):
        release_policy.require({"state": "unknown", "version": None}, "0.6.6")
    assert (
        release_policy.require({"state": "unknown", "version": None}, "0.6.6", owned_resume=True)
        == "unknown"
    )
    assert release_policy.require({"state": "absent", "version": None}, "0.6.6") == "absent"
    with pytest.raises(release_policy.ReleaseBlocked, match="upgrade the CLI"):
        release_policy.require({"state": "present", "version": "0.6.7"}, "0.6.6", owned_resume=True)


@pytest.mark.parametrize(
    "version,files,expected",
    [
        ("0.5.9", {"package": "exact"}, "upgrade"),
        ("0.6.6", {"package": "drift"}, "upgrade"),
        ("0.6.6", {"package": "exact"}, None),
        ("0.6.7", {"package": "exact"}, "newer"),
        (None, {"package": "exact"}, "unknown"),
    ],
)
def test_direct_candidate_cannot_bypass_ordinary_release_alignment(
    monkeypatch, version, files, expected
):
    from nebius_vpngw.deploy import ordinary_apply

    monkeypatch.setattr(release_policy, "wheel_version", lambda _: "0.6.6")
    monkeypatch.setattr(
        release_policy,
        "inspect",
        lambda *a: {"state": "present" if version else "unknown", "version": version},
    )
    monkeypatch.setattr(
        ordinary_apply, "artifact", lambda *a: {"version": "0.6.6", "files": {"package": "exact"}}
    )
    monkeypatch.setattr(
        ordinary_apply,
        "remote",
        lambda *a: {"observation": {"package_version": version, "files": files}},
    )

    def check():
        release_policy.require_targets(
            None,
            {"retained": "target"},
            (NS(hostname="retained", config_yaml="config"),),
            {},
            Path("unused"),
            ordinary_conversion=frozenset({"retained"}),
        )

    if expected is None:
        check()
    else:
        error = (
            release_policy.ConversionUpgradeRequired
            if expected == "upgrade"
            else release_policy.ReleaseBlocked
        )
        with pytest.raises(error):
            check()


def test_batch_newer_blocks_before_any_unknown_recovery(monkeypatch):
    monkeypatch.setattr(release_policy, "wheel_version", lambda _: "0.6.6")
    observations = {
        "a": {"state": "unknown", "version": None},
        "b": {"state": "present", "version": "0.6.7"},
    }
    monkeypatch.setattr(release_policy, "inspect", lambda _ssh, target, *_: observations[target])
    with pytest.raises(release_policy.ReleaseBlocked) as error:
        release_policy.require_targets(
            None,
            {"a": "a", "b": "b"},
            (NS(hostname="a"), NS(hostname="b")),
            {},
            Path("unused"),
            resume=lambda _: pytest.fail("newer pair entered recovery"),
        )
    assert error.value.first.predicate == "gateway-release-newer"


@pytest.mark.parametrize(
    "versions,expected",
    [
        ([], "absent"),
        (["0.6.6"], "present"),
        (["0.6.5", "0.6.6"], "unknown"),
        (["0.6.6", "0.6.6"], "unknown"),
    ],
)
def test_release_probe_does_not_import_product_or_choose_duplicate_metadata(
    monkeypatch, versions, expected
):
    monkeypatch.setattr(
        release_identity.importlib.metadata,
        "distributions",
        lambda: [NS(metadata={"Name": "nebius-vpngw"}, version=v) for v in versions],
    )
    monkeypatch.setattr(release_identity.importlib.util, "find_spec", lambda _: None)
    monkeypatch.setattr(release_identity, "Path", lambda _: NS(exists=lambda: False))
    assert release_identity.observe()["state"] == expected


def test_partial_install_is_unknown_even_without_metadata(monkeypatch):
    monkeypatch.setattr(release_identity.importlib.metadata, "distributions", lambda: [])
    monkeypatch.setattr(release_identity.importlib.util, "find_spec", lambda _: object())
    assert release_identity.observe() == {"state": "unknown", "version": None}


def test_exact_ordinary_owner_can_resume_unknown_metadata(monkeypatch):
    manifest = {"wheel_sha256": "a" * 64, "package_identity": "b" * 64, "config_sha256": "c" * 64}
    owner = {"id": "d" * 32, "state": "unresolved", "config": "c" * 64, "artifact": "b" * 64}
    monkeypatch.setattr(
        ordinary_remote.migration,
        "pending",
        lambda _: {"operation": "d" * 32, "manifest": copy.deepcopy(manifest)},
    )
    monkeypatch.setattr(ordinary_remote.operations, "Journal", lambda: NS(read=lambda: owner))
    assert ordinary_remote.release_resume(manifest) == {"owned_resume": True}
    owner["artifact"] = "a" * 64
    assert ordinary_remote.release_resume(manifest) == {"owned_resume": False}


def test_completed_owned_manifest_is_frozen_before_successor_overwrite():
    item = {
        "member": "nebius_vpngw/systemd/nebius-vpngw-old.service",
        "mode": 0o644,
        "sha256": "a" * 64,
    }
    old = {
        "phase": "complete",
        "operation": "b" * 32,
        "manifest": {
            "wheel_sha256": "c" * 64,
            "assets": {"/etc/systemd/system/nebius-vpngw-old.service": item},
        },
    }
    planned = runtime_migration.asset_retirement({"assets": {}}, old)
    old["manifest"]["assets"].clear()
    assert planned["removals"]["/etc/systemd/system/nebius-vpngw-old.service"] == item
    assert planned["predecessor"]["wheel_sha256"] == "c" * 64
    assert runtime_migration.asset_retirement({"assets": {}}, None)["removals"] == {}
    assert (
        runtime_migration.asset_retirement({"assets": {}}, {**old, "phase": "installed"})[
            "removals"
        ]
        == {}
    )


@pytest.fixture
def retirement_guest(tmp_path, monkeypatch):
    real_path = Path

    class GuestPath(type(Path())):
        def lstat(self):
            info = super().lstat()
            return NS(st_mode=info.st_mode, st_uid=0, st_gid=0)

    def path(name):
        text = str(name)
        return (
            GuestPath(tmp_path / text.lstrip("/"))
            if text.startswith(("/etc/", "/usr/"))
            else real_path(name)
        )

    monkeypatch.setattr(runtime_migration, "Path", path)
    target = "/etc/systemd/system/nebius-vpngw-old.service"
    actual = path(target)
    actual.parent.mkdir(parents=True)
    actual.write_bytes(b"owned")
    actual.chmod(0o644)
    plan = {
        "predecessor": {"operation": "b" * 32},
        "removals": {
            target: {
                "member": "nebius_vpngw/systemd/nebius-vpngw-old.service",
                "mode": 0o644,
                "sha256": runtime_migration.hashlib.sha256(b"owned").hexdigest(),
            }
        },
    }
    return plan, actual


@pytest.mark.parametrize("change", ["bytes", "mode", "symlink"])
def test_retirement_preserves_changed_or_symlinked_assets(retirement_guest, change):
    plan, actual = retirement_guest
    if change == "bytes":
        actual.write_bytes(b"operator override")
    elif change == "mode":
        actual.chmod(0o600)
    else:
        actual.unlink()
        actual.symlink_to("missing")
    with pytest.raises(RuntimeError, match="asset_retirement_changed"):
        runtime_migration.verify_asset_retirement(plan)
    assert actual.exists() or actual.is_symlink()


def test_retirement_stops_disables_removes_and_resumes_without_repeating_removal(
    retirement_guest, monkeypatch
):
    plan, actual = retirement_guest
    commands = []

    def run(args):
        commands.append(args)
        return "inactive" if "--property=ActiveState" in args else "disabled"

    monkeypatch.setattr(runtime_migration, "run", run)
    monkeypatch.setattr(runtime_migration, "unlink", lambda path: path.unlink())
    runtime_migration.retire_owned_assets(plan)
    assert not actual.exists()
    assert commands == [
        ["systemctl", "stop", actual.name],
        ["systemctl", "show", actual.name, "--property=ActiveState", "--value"],
        ["systemctl", "disable", actual.name],
        ["systemctl", "show", actual.name, "--property=UnitFileState", "--value"],
        ["systemctl", "daemon-reload"],
        ["systemctl", "show", actual.name, "--property=ActiveState", "--value"],
    ]
    runtime_migration.retire_owned_assets(plan)
    assert commands[-2] == ["systemctl", "daemon-reload"]
    assert len(commands) == 12


@pytest.mark.parametrize("origin", ["initial", "upgrade"])
def test_initial_and_upgrade_successors_preserve_original_artifact(origin):
    previous = failed_release()
    authority = record(previous)
    if origin == "initial":
        bindings = dict(previous.transaction.resource_bindings)
        bindings["heartbeat-initial-artifact"] = bindings.pop("heartbeat-upgrade-artifact")
        previous = replace(
            previous,
            transaction=replace(
                previous.transaction, resource_bindings=tuple(sorted(bindings.items()))
            ),
        )
        authority["prior_record"] = previous.record_sha256
        authority["operation"] = vm_ha_corrective.operation_id(previous, authority["artifact"])
    successor = vm_ha_corrective.successor(previous, authority, bindings={})
    assert vm_ha_corrective.predecessor_artifact(previous) == authority["prior_artifact"]
    assert (
        dict(successor.transaction.resource_bindings)["heartbeat-upgrade-artifact"]
        == authority["artifact"]
    )
    assert "heartbeat-initial-artifact" not in dict(successor.transaction.resource_bindings)


def test_checkpoint_projection_keeps_digests_without_free_text():
    state = failed_release()
    state = replace(
        state,
        transaction=replace(
            state.transaction,
            checkpoint="before-activate-private-sentinel",
            pending_effect="activate-private-sentinel",
        ),
    )
    config = {
        "project_id": state.project_id,
        "gateway_group": {"name": state.gateway_name, "vm_ha": {"cluster_id": state.cluster_id}},
    }
    view = checkpoints.project(state.to_dict(), config)
    clean = projection.project(view)
    assert clean["origin"] == "upgrade"
    assert clean["upgrade_artifact_sha256"] == vm_ha_corrective.predecessor_artifact(state)
    assert clean["checkpoint_category"] == "activation"
    assert "private-sentinel" not in json.dumps(clean)
    assert len(clean["effects"]) == len(state.transaction.completed_effects) + 1


@pytest.mark.parametrize("value", ["healthy", "Healthy", "HEALTHY"])
def test_healthy_is_green_and_plain_output_preserves_text(value):
    text = operational_diagnostics.health_text(value)
    assert text.style == "green"
    output = io.StringIO()
    Console(file=output, no_color=True).print(text)
    assert output.getvalue() == value + "\n"


def test_peer_reachability_is_derived_from_current_heartbeat_not_listener():
    current = {
        "guard_boot_id": "boot",
        "apply_convergence": {"observed_at": 100, "boot_id": "boot", "fresh": True},
    }
    received = {**current, "heartbeat": {"peer_v6_fresh": True}}
    assert operational_diagnostics.peer_channel({"listeners": [{"local_port": 9443}]}) == "unknown"
    assert operational_diagnostics.peer_channel(received) == "authenticated-receive"
    assert (
        operational_diagnostics.peer_channel({**received, "reasons": ["current-boot-status-stale"]})
        == "unknown"
    )
    failed = {
        **current,
        "heartbeat": {
            "peer_v6_fresh": False,
            "peer_send_error_kind": "tls",
            "peer_send_consecutive_failures": 1,
        },
    }
    assert operational_diagnostics.peer_channel(failed) == "tls-failure"
    assert operational_diagnostics.peer_direction("tls-failure") == "gateway-to-peer"
    assert operational_diagnostics.peer_direction("authenticated-receive") == "peer-to-gateway"


@pytest.mark.parametrize(
    "returncode,output,expected",
    [
        (0, '{"state":"present","version":"0.6.6"}', "authenticated"),
        (-15, "", "unknown"),
        (255, "", "unknown"),
        (0, "invalid", "authenticated"),
    ],
)
def test_release_access_renderer_never_confuses_signal_or_malformed_release_with_closed_port(
    monkeypatch, returncode, output, expected
):
    from nebius_vpngw import cli

    monkeypatch.setattr(cli, "_status_ssh_target_command", lambda *_a, **_k: ["ssh"])
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *_a, **_k: NS(returncode=returncode, stdout=output)
    )
    stream = io.StringIO()
    cli._render_release_access(
        Console(file=stream, no_color=True, width=180),
        NS(iter_instance_configs=lambda: iter([NS(hostname="gateway")])),
        None,
        {"gateway": "192.0.2.1"},
    )
    rendered = stream.getvalue()
    assert expected in rendered
    assert "Peer SSH is not required" in rendered
    assert "identity loaded by running services" in rendered


@pytest.mark.parametrize(
    "observed", [{"state": "unknown", "version": None}, {"state": "present", "version": "0.6.7"}]
)
def test_repair_release_gate_precedes_guest_admission(monkeypatch, observed):
    from nebius_vpngw.deploy import repair_apply

    coordinator = repair_apply.Coordinator.__new__(repair_apply.Coordinator)
    coordinator.admission = NS(ssh=None, targets={"a": "address"}, local={}, wheel=Path("unused"))
    coordinator.instances = {"a": NS(hostname="a")}
    coordinator.remote = lambda *_a, **_k: pytest.fail("repair admitted before release gate")
    monkeypatch.setattr(release_policy, "inspect", lambda *_: observed)
    monkeypatch.setattr(release_policy, "wheel_version", lambda _: "0.6.6")
    monkeypatch.setattr(repair_apply, "release_resume", lambda *_: False)
    with pytest.raises(release_policy.ReleaseBlocked):
        coordinator.wait_access("a")


@pytest.mark.parametrize("state", ["accepted", "intent", "settled"])
@pytest.mark.parametrize("label", ["create-candidate", "retire-compute"])
def test_release_cloud_observation_never_replays_or_settles(monkeypatch, state, label):
    from nebius.api.nebius.compute import v1 as compute

    from nebius_vpngw.deploy.replacement_cloud import Cloud, effect_key, message_digest

    request = (
        compute.DeleteInstanceRequest(id="source")
        if label == "retire-compute"
        else compute.CreateInstanceRequest()
    )
    operation = NS(
        id="operation", resource_id="candidate", done=lambda: True, successful=lambda: True
    )
    client = NS(operation_service=lambda: NS(get=lambda *_a, **_k: NS(wait=lambda: operation)))
    monkeypatch.setattr(compute, "InstanceServiceClient", lambda _: client)
    key = effect_key("a" * 64, 0, label, None)
    record = {
        "label": label,
        "status": state,
        "resource_id": "candidate",
        "cloud_operation_id": "operation",
        "request_type": type(request).__name__,
        "request_sha256": message_digest(request),
    }
    journal = NS(
        state={"transaction": {"member": 0, "approval": "a" * 64, "operations": {key: record}}},
        save=lambda: pytest.fail("read-only gate wrote receipt"),
    )
    cloud = Cloud(None, journal)
    before = copy.deepcopy(journal.state)
    if state == "intent":
        assert cloud.observe_compute_completion(label, request=request) is None
    else:
        assert cloud.observe_compute_completion(label, request=request) == "candidate"
    assert journal.state == before


def test_release_resume_stream_dispatch_returns_success(monkeypatch, capsys):
    monkeypatch.setattr(
        ordinary_remote.sys,
        "stdin",
        NS(buffer=io.BytesIO(b'{"action":"release-resume","manifest":{}}')),
    )
    monkeypatch.setattr(ordinary_remote.signal, "signal", lambda *_: None)
    monkeypatch.setattr(ordinary_remote.signal, "alarm", lambda *_: None)
    monkeypatch.setattr(ordinary_remote, "release_resume", lambda _: {"owned_resume": True})
    with pytest.raises(SystemExit) as exit_result:
        ordinary_remote.main()
    assert exit_result.value.code == 0
    assert json.loads(capsys.readouterr().out) == {"owned_resume": True}


@pytest.mark.parametrize("changed", [None, "guard", "artifact", "configuration", "owner"])
def test_replacement_resume_returns_only_exact_guarded_manifest(monkeypatch, tmp_path, changed):
    guard = tmp_path / "replacement-guard.json"
    guard.write_text(json.dumps({"operation": "a" * 64}))
    manifest = {"wheel_sha256": "b" * 64, "config_sha256": "c" * 64, "package_identity": "d" * 64}
    request = {"operation": "a" * 64, "artifact": "b" * 64, "configuration": "c" * 64}
    if changed in {"artifact", "configuration"}:
        request[changed] = "f" * 64
    elif changed == "guard":
        request["operation"] = "e" * 64
    monkeypatch.setattr(ordinary_remote, "Path", lambda _: guard)
    monkeypatch.setattr(ordinary_remote.migration, "pending", lambda: {"manifest": manifest})
    monkeypatch.setattr(
        ordinary_remote, "release_resume", lambda _: {"owned_resume": changed != "owner"}
    )
    assert ordinary_remote.replacement_resume(request)["manifest"] == (
        manifest if changed is None else None
    )


@pytest.mark.parametrize(
    "installed,owned", [("0.6.7", False), (None, True), (None, False), ("0.6.5", False)]
)
def test_replacement_gate_discovers_candidate_after_lost_create_receipt(
    monkeypatch, installed, owned
):
    from nebius_vpngw.deploy import replacement_ordinary as module
    from nebius_vpngw.deploy.vm_ha_cloud import InstanceCloudState

    ports = module.OrdinaryReplacement.__new__(module.OrdinaryReplacement)
    ports.sdk, ports.ssh, ports.local, ports.wheel = None, None, {}, Path("unused")
    ports.instances = {"gateway": NS(hostname="gateway")}
    labels = {"exact": "operation"}
    candidate = NS(
        metadata=NS(id="candidate", parent_id="project", labels=labels),
        spec=NS(boot_disk=NS(existing_disk=NS(id="disk"))),
    )
    ports.manager = NS(_get_vm_by_name=lambda *_: candidate)
    ports.access = lambda identifier, logical, **_: (None, NS(address=identifier, hostname=logical))
    ports.package_resume = lambda *_: {"frozen": "manifest"} if owned else None
    cloud = NS(
        observe_compute_completion=lambda *_: None,
        candidate_boot_receipt=lambda: "disk",
        labels=lambda *_: labels,
        get_instance=lambda identifier, **_: candidate,
    )
    monkeypatch.setattr(module, "Cloud", lambda *_a, **_k: cloud)
    monkeypatch.setattr(module, "instance_cloud_state", lambda _: InstanceCloudState.RUNNING)
    monkeypatch.setattr(release_policy, "wheel_version", lambda _: "0.6.6")
    seen = []

    def inspect(_ssh, address, *_):
        seen.append(address)
        return (
            {"state": "present", "version": "0.6.5"}
            if address == "source"
            else {"state": "unknown" if installed is None else "present", "version": installed}
        )

    monkeypatch.setattr(release_policy, "inspect", inspect)
    journal = NS(
        project="project",
        state={
            "transaction": {
                "member": 0,
                "pending": "prepare",
                "results": {},
                "plan": {
                    "members": [
                        {
                            "logical_name": "gateway",
                            "compute_id": "source",
                            "candidate_name": "candidate-name",
                        }
                    ]
                },
            }
        },
    )
    if installed == "0.6.7" or installed is None and not owned:
        with pytest.raises(release_policy.ReleaseBlocked):
            ports.require_release(journal)
    else:
        ports.require_release(journal)
    assert seen == ["source", "candidate"]


@pytest.mark.parametrize("foreign", [False, True])
def test_missing_obsolete_unit_only_removes_its_exact_enablement_link(
    retirement_guest, monkeypatch, foreign
):
    plan, path = retirement_guest
    wants = path.parent / "multi-user.target.wants"
    wants.mkdir()
    link = wants / path.name
    link.symlink_to(path.parent / "foreign.service" if foreign else path)
    path.unlink()

    def run(args):
        if args[1] == "disable":
            raise RuntimeError("Unit file does not exist")
        if "--property=ActiveState" in args:
            return "inactive"
        if "--property=UnitFileState" in args:
            return "enabled" if link.is_symlink() else ""
        return ""

    monkeypatch.setattr(runtime_migration, "run", run)
    monkeypatch.setattr(runtime_migration, "unlink", lambda path: path.unlink())
    if foreign:
        with pytest.raises(RuntimeError, match="asset_retirement_unproven"):
            runtime_migration.retire_owned_assets(plan)
        assert link.is_symlink()
    else:
        runtime_migration.retire_owned_assets(plan)
        assert not link.is_symlink()
        runtime_migration.retire_owned_assets(plan)


def _channel_record():
    return {
        "guard_boot_id": "boot",
        "apply_convergence": {"observed_at": 100, "boot_id": "boot", "fresh": True},
        "heartbeat": {"peer_v6_fresh": True},
    }


def test_channel_capture_survives_slow_render_without_using_operator_wall_clock(monkeypatch):
    import time

    record = _channel_record()
    monkeypatch.setattr(time, "time", lambda: 1_795_104_000)
    captured = operational_diagnostics.observe_peer_channel(record)
    record["apply_convergence"]["fresh"] = False
    monkeypatch.setattr(time, "time", lambda: 1_795_104_060)
    text = operational_diagnostics.peer_channel_text(captured)
    assert text.style == "green"
    assert "Open · mTLS verified" in text.plain
    assert text.plain == "Open · mTLS verified"
    assert operational_diagnostics.peer_channel(record) == "unknown"


@pytest.mark.parametrize("fresh", [None, False, "true", 1, 0, [], {}])
def test_missing_or_invalid_native_freshness_never_becomes_verified(fresh):
    record = _channel_record()
    record["apply_convergence"]["fresh"] = fresh
    observation = operational_diagnostics.observe_peer_channel(record)
    assert observation.assessment == "unknown"
    text = operational_diagnostics.peer_channel_text(observation)
    assert text.style == "yellow"
    assert text.plain.startswith("Unverified · ")
    assert "\n" not in text.plain


@pytest.mark.parametrize(
    "change", ["stale-reason", "wrong-boot", "missing-boot", "missing-fresh", "missing-evidence"]
)
def test_invalid_heartbeat_at_capture_remains_unverified(change):
    record = _channel_record()
    if change == "stale-reason":
        record["reasons"] = ["current-boot-status-stale"]
    elif change == "wrong-boot":
        record["guard_boot_id"] = "other"
    elif change == "missing-boot":
        del record["guard_boot_id"]
    elif change == "missing-fresh":
        del record["apply_convergence"]["fresh"]
    else:
        del record["apply_convergence"]
    observation = operational_diagnostics.observe_peer_channel(record)
    assert observation.assessment == "unknown"
    assert observation.reason


@pytest.mark.parametrize(
    "kind,label",
    [
        ("refused", "Connection refused"),
        ("timeout", "Timeout"),
        ("unreachable", "Unreachable"),
        ("tls", "TLS failed"),
    ],
)
def test_channel_failure_keeps_classified_reason_and_color_without_direction(kind, label):
    record = _channel_record()
    record["heartbeat"] = {
        "peer_v6_fresh": False,
        "peer_send_consecutive_failures": 1,
        "peer_send_error_kind": kind,
    }
    observed = operational_diagnostics.observe_peer_channel(record)
    text = operational_diagnostics.peer_channel_text(observed)
    assert text.plain == label
    assert text.style == "red"
    assert "closed" not in text.plain.lower()


def test_cli_transport_reads_native_freshness_with_one_direct_ssh_command(monkeypatch):
    from nebius_vpngw import cli

    record = _channel_record()
    commands = []
    monkeypatch.setattr(cli, "_build_ssh_base_cmd", lambda *a, **k: ["ssh"])
    monkeypatch.setattr(cli, "_validate_vm_ha_agent_status", lambda value, **k: value)

    def ssh(command, **kwargs):
        commands.append(command)
        assert command[-1] == "sudo /usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-status"
        assert 0 < kwargs["timeout"] <= 15
        return NS(returncode=0, stdout=json.dumps(record), stderr="")

    monkeypatch.setattr(cli.subprocess, "run", ssh)
    actual = cli._fetch_vm_ha_agent_status(
        target="192.0.2.1",
        hostname="gateway",
        username="ubuntu",
        key_path=None,
        ssh_policy=object(),
        inst_cfg=object(),
    )
    assert actual == record
    assert len(commands) == 1
    assert (
        operational_diagnostics.observe_peer_channel(actual).assessment == "authenticated-receive"
    )


def test_release_access_uses_frozen_channel_after_release_ssh_delay(monkeypatch):
    from nebius_vpngw import cli

    observed = operational_diagnostics.observe_peer_channel(_channel_record())
    snapshot = NS(members=(NS(name="gateway", record=_channel_record(), peer_channel=observed),))
    monkeypatch.setattr(cli, "_status_ssh_target_command", lambda *a, **k: ["ssh"])
    monkeypatch.setattr(cli.time, "time", lambda: 1000)
    monkeypatch.setattr(cli.time, "monotonic", lambda: 40)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *a, **k: NS(returncode=0, stdout='{"state":"present","version":"0.6.6"}'),
    )
    stream = io.StringIO()
    cli._render_release_access(
        Console(file=stream, width=180, no_color=True),
        NS(iter_instance_configs=lambda: [NS(hostname="gateway")]),
        None,
        {"gateway": "192.0.2.1"},
        snapshot,
    )
    output = stream.getvalue()
    assert "Open · mTLS verified" in output
    assert "collected" not in output
    assert "Observed" not in output
    assert "Peer channel · TCP 9443" in output
    assert "peer-to-gateway" not in output
    assert "gateway-to-peer" not in output
    assert "not a continuous port probe" in output


@pytest.mark.parametrize("native", ["{", "[]"])
def test_malformed_native_status_stays_permanent(monkeypatch, native):
    from nebius_vpngw import cli

    monkeypatch.setattr(cli, "_build_ssh_base_cmd", lambda *a, **k: ["ssh"])
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *a, **k: NS(returncode=0, stdout=native, stderr="")
    )
    with pytest.raises(cli._VMHAAgentStatusPermanent):
        cli._fetch_vm_ha_agent_status(
            target="192.0.2.1",
            hostname="gateway",
            username="ubuntu",
            key_path=None,
            ssh_policy=object(),
            inst_cfg=object(),
        )
