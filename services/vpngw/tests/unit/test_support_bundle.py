"""Privacy and bounded observation regressions; no live cloud or SSH access."""

import json
import os
import stat
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

from nebius_vpngw import cli
from nebius_vpngw.support_bundle import collector, probe, projection, worker


def test_transaction_selection_retains_active_operation_and_reports_history_limit(tmp_path):
    transactions = tmp_path / "mtls/transactions"
    transactions.mkdir(parents=True)
    for index in range(12):
        path = transactions / (f"{index:064x}" + ".json")
        path.write_text("{}")
        os.utime(path, (index, index))
    (tmp_path / "mtls/inhibition.json").write_text(json.dumps({"operation_id": "0" * 64}))
    selected, limited = probe.select_transactions(tmp_path)
    assert limited
    assert len(selected) == 8
    assert selected[0].stem == "0" * 64
    assert selected[1].stem == f"{11:064x}"
    assert probe.valid_remote_name("ha-0/mtls/transactions-limit")


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "gateway.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "project_id": "project-example",
                "tenant_id": "tenant-example",
                "gateway_group": {
                    "name": "gateway",
                    "instance_count": 2,
                    "region": "region-example",
                    "external_ips": [["192.0.2.10"], ["192.0.2.11"]],
                    "vm_spec": {"ssh_username": "ubuntu", "ssh_public_key": "SECRET_PUBLIC_VALUE"},
                    "vm_ha": {"enabled": True, "cluster_id": "cluster-example"},
                },
                "connections": [
                    {"name": "connection-one", "tunnels": [{"psk": "SUPER_PRIVATE_PSK"}]}
                ],
            }
        )
    )
    from nebius_vpngw.deploy.ssh_push import SSHPush

    wheel = tmp_path / "nebius_vpngw-0.6.5-py3-none-any.whl"
    wheel.write_bytes(b"fixture-artifact-bytes")
    monkeypatch.setattr(
        SSHPush, "select_vm_ha_agent_wheel", lambda _self: (wheel, "local-prebuilt-wheel")
    )
    return path


def fake_runner(request, _timeout):
    if request["action"] == "artifact":
        return [
            {"probe": "selected-artifact", "data": {"artifact_sha256": "a" * 64}, "outcome": "ok"}
        ], "ok"
    if request["action"] == "status":
        return [{"probe": "status", "data": {}, "outcome": "ok"}], "ok"
    if request["action"] == "cloud":
        return [
            {
                "probe": "cloud/instance/0",
                "data": {"id": "computeinstance-example"},
                "outcome": "ok",
            }
        ], "ok"
    return [
        {
            "probe": "ha-0/native",
            "data": {
                "apply_locked": True,
                "data_plane_mode": "passive",
                "heartbeat": {"peer_v6_fresh": False},
                "reasons": ["apply-lock-held"],
                "password": "SUPER_PRIVATE_PASSWORD",
                "exception": "SUPER_PRIVATE_EXCEPTION",
            },
            "outcome": "ok",
        }
    ], "ok"


def test_complete_bundle_private_and_no_secret_source_or_state_changes(config, tmp_path):
    before = config.read_bytes()
    path, code = collector.collect(config, output=tmp_path / "result.zip", runner=fake_runner)
    assert code == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert config.read_bytes() == before
    with zipfile.ZipFile(path) as archive:
        exported = b"\n".join(archive.read(name) for name in archive.namelist())
        assert b"SUPER_PRIVATE" not in exported
        assert b"SECRET_PUBLIC_VALUE" in archive.read("operator/customer-config.redacted.yaml")
        assert b"computeinstance-example" in exported
        assert b'"apply_locked": true' in exported
        assert json.loads(archive.read("manifest.json"))["schema"] == collector.SCHEMA
        assert not any("gateway.yaml" in name for name in archive.namelist())
    assert not list(tmp_path.glob(".vpngw-support-*"))


def test_partial_worker_preserves_successful_node_and_records_failure(config, tmp_path):
    def run(request, timeout):
        if request.get("hostname") == "gateway-1":
            return [], "timeout"
        return fake_runner(request, timeout)

    path, code = collector.collect(config, output=tmp_path / "partial.zip", runner=run)
    assert code == 1
    with zipfile.ZipFile(path) as archive:
        assert "nodes/0/ha-0/native.json" in archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert any(item["outcome"] == "timeout" for item in manifest["probes"])


@pytest.mark.parametrize("kind", ["existing", "symlink"])
def test_output_never_overwrites(config, tmp_path, kind):
    existing = tmp_path / "existing.zip"
    existing.write_bytes(b"retain")
    selected = existing
    if kind == "symlink":
        selected = tmp_path / "link.zip"
        selected.symlink_to(existing)
    with pytest.raises(ValueError):
        collector.collect(config, output=selected, runner=fake_runner)
    assert existing.read_bytes() == b"retain"


def test_untrusted_probe_name_never_reaches_archive(config, tmp_path):
    def run(request, timeout):
        if request["action"] == "artifact":
            return [
                {
                    "probe": "selected-artifact",
                    "data": {"artifact_sha256": "a" * 64},
                    "outcome": "ok",
                }
            ], "ok"
        if request["action"] == "status":
            return [{"probe": "status", "data": {}, "outcome": "ok"}], "ok"
        if request["action"] == "cloud":
            return [], "ok"
        return [
            {
                "probe": "ha-0/mtls/transactions/customercredential123.json",
                "data": {},
                "outcome": "ok",
            }
        ], "ok"

    path, code = collector.collect(config, output=tmp_path / "names.zip", runner=run)
    assert code == 1
    with zipfile.ZipFile(path) as archive:
        assert b"customercredential123" not in b"".join(archive.read(n) for n in archive.namelist())
        assert not any("customercredential123" in n for n in archive.namelist())


def test_projection_bounds_alias_dag_cycles_and_omits_unknowns():
    shared = {"name": "gateway", "psk": "PRIVATE"}
    for _ in range(10):
        shared = {"members": [shared] * 20}
    omitted = [0]
    data = projection.project(shared, omitted=omitted)
    assert len(json.dumps(data)) < 10000 and omitted[0] > 0
    cyclic = {}
    cyclic["members"] = [cyclic]
    assert projection.project(cyclic) == {"members": []}
    assert projection.project({"uptime": float("nan"), "epoch": 10**500}) == {}


def test_projection_keeps_classified_peer_send_health():
    data = projection.project(
        {
            "heartbeat": {
                "schema_version": 6,
                "peer_v6_fresh": False,
                "peer_health_fresh": True,
                "peer_send_error_kind": "unreachable",
                "peer_send_last_error_age_seconds": 2.5,
                "peer_send_last_success_age_seconds": None,
                "peer_send_consecutive_failures": 12,
            }
        }
    )
    heartbeat = data["heartbeat"]
    assert heartbeat["peer_send_error_kind"] == "unreachable"
    assert heartbeat["peer_send_consecutive_failures"] == 12
    assert heartbeat["peer_send_last_error_age_seconds"] == 2.5
    # A never-succeeded send has no age; projection omits the empty field.
    assert "peer_send_last_success_age_seconds" not in heartbeat
    assert heartbeat["peer_health_fresh"] is True

    bogus = projection.project({"heartbeat": {"peer_send_error_kind": "raw sensitive text"}})
    assert "peer_send_error_kind" not in bogus.get("heartbeat", {})


def test_cloud_routes_and_firewall_payload_are_not_silently_empty():
    assert projection.project(
        {
            "items": [
                {
                    "metadata": {"id": "route-one"},
                    "spec": {"destination": "10.0.0.0/8", "next_hop": "192.0.2.1"},
                }
            ]
        }
    )["items"]
    exported = projection.project(
        {
            "nftables": [
                {
                    "rule": {
                        "family": "ip",
                        "chain": "FORWARD",
                        "expr": [{"counter": {"packets": 42, "bytes": 100}}],
                        "comment": "PRIVATE",
                    }
                }
            ]
        }
    )
    assert "42" in json.dumps(exported) and "PRIVATE" not in json.dumps(exported)


@pytest.mark.parametrize(
    "message,reason",
    [
        ('{"reasons":["apply-lock-held"],"token":"PRIVATE"}', "apply-lock-held"),
        ("Failed to start service PRIVATE", "service-failed"),
        ("received AUTHENTICATION_FAILED PRIVATE", "ipsec-authentication-failed"),
        ("BGP neighbor Down PRIVATE", "bgp-peer-down"),
        ("Out of memory: Killed process 123 PRIVATE", "kernel-oom"),
        ("eth0: Lost carrier PRIVATE", "network-link-down"),
        ("[Agent] Error applying PRIVATE", "agent-or-bootstrap-failed"),
        ("Traceback (most recent call last): PRIVATE", "python-exception"),
    ],
)
def test_known_log_events_keep_codes_and_redacted_messages(message, reason):
    raw = json.dumps(
        {"MESSAGE": message, "__REALTIME_TIMESTAMP": "123456789", "_BOOT_ID": "boot-one"}
    )
    data = projection.project(probe.journal_events(raw))
    assert data["events"][0]["reasons"] == [reason]
    assert (
        "PRIVATE" in json.dumps(data)
        if "token" not in message
        else "PRIVATE" not in json.dumps(data)
    )


def test_only_required_access_placeholders_expand(config, monkeypatch):
    text = (
        config.read_text()
        .replace("SECRET_PUBLIC_VALUE", "${SSH_PUBLIC_KEY}")
        .replace("SUPER_PRIVATE_PSK", "${PSK}")
    )
    config.write_text(text)
    monkeypatch.setenv("SSH_PUBLIC_KEY", "selected-public-key")
    monkeypatch.setenv("PSK", "DO_NOT_READ_THIS_SECRET")
    loaded = worker.scope(config)
    assert loaded["gateway_group"]["vm_spec"]["ssh_public_key"] == "selected-public-key"
    assert loaded["connections"][0]["tunnels"][0]["psk"] == "${PSK}"


def test_operator_identity_propagates_privately(config, tmp_path, monkeypatch):
    from nebius_vpngw.deploy import ssh_client_auth

    identity_path = tmp_path / "private-selector"
    monkeypatch.setattr(
        ssh_client_auth,
        "operator_identity",
        lambda: SimpleNamespace(_key_path=identity_path, _identity_file=None),
    )
    requests = []

    def run(request, timeout):
        requests.append(request)
        return fake_runner(request, timeout)

    path, _code = collector.collect(config, output=tmp_path / "identity.zip", runner=run)
    assert all(
        request["identity"] == str(identity_path)
        for request in requests
        if request["action"] != "artifact"
    )
    assert all("identity" not in request for request in requests if request["action"] == "artifact")
    with zipfile.ZipFile(path) as archive:
        assert b"private-selector" not in b"".join(archive.read(n) for n in archive.namelist())


def test_subprocess_timeout_and_output_limit_are_bounded():
    started = time.monotonic()
    output, status, _code = probe.bounded_command(
        [sys.executable, "-c", "import time;print('ready',flush=True);time.sleep(5)"], timeout=0.2
    )
    assert status == "timeout" and output == b"ready\n"
    assert time.monotonic() - started < 2
    output, status, _code = probe.bounded_command(
        [sys.executable, "-c", "print('x'*100000)"], limit=100
    )
    assert status == "source-limit" and len(output) == 100


def test_window_and_cli_failures_do_not_print_raw_config(config, tmp_path):
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        collector.time_window((now - timedelta(days=8)).isoformat(), now)
    with pytest.raises(ValueError):
        collector.time_window("2026-01-01", now)
    config.write_text("not: [valid SUPER_PRIVATE_PSK")
    result = CliRunner().invoke(
        cli.app, ["support-bundle", "-c", str(config), "--output", str(tmp_path / "bad.zip")]
    )
    assert result.exit_code == 2
    assert "SUPER_PRIVATE_PSK" not in result.output
    assert not (tmp_path / "bad.zip").exists()


def test_standalone_probe_compiles_without_installed_package():
    source = worker.standalone_source()
    namespace = {"__name__": "probe_test"}
    exec(compile(source, "support-probe", "exec"), namespace)
    assert callable(namespace["collect_probe"])


def test_activation_classifier_preserves_acceptance_and_exact_failures(monkeypatch):
    monkeypatch.setattr(cli, "_vm_ha_mtls_agent_evidence_matches", lambda *args: True)
    status = {
        "node_id": "node-0",
        "data_plane_mode": "passive",
        "promotion_ready": False,
        "controller_capabilities": ["independent-heartbeat-v6"],
        "heartbeat": {"schema_version": 6, "peer_v6_fresh": True},
    }

    def check(value, owner=False):
        return cli._vm_ha_activation_failure(
            value,
            transaction=None,
            owner=owner,
            current_owner_node_id="node-0",
            operation_id="operation-one",
        )

    assert check(status) is None
    for field, value, reason in (
        ("data_plane_mode", "active", "data-plane-mode-mismatch"),
        ("controller_capabilities", [], "heartbeat-capability-missing"),
        ("heartbeat", {"schema_version": 5}, "heartbeat-schema-mismatch"),
        ("heartbeat", {"schema_version": 6, "peer_v6_fresh": False}, "peer-heartbeat-not-fresh"),
        (
            "heartbeat",
            {
                "schema_version": 6,
                "peer_v6_fresh": False,
                "peer_send_error_kind": "unreachable",
                "peer_send_consecutive_failures": 12,
            },
            "peer-heartbeat-endpoint-unreachable",
        ),
        (
            "heartbeat",
            {
                "schema_version": 6,
                "peer_v6_fresh": False,
                "peer_send_error_kind": "tls",
                "peer_send_consecutive_failures": 12,
            },
            "peer-heartbeat-tls-failure",
        ),
        ("promotion_ready", True, "promotion-readiness-mismatch"),
    ):
        assert check({**status, field: value}) == reason
    owner = {
        **status,
        "data_plane_mode": "active",
        "promotion_ready": True,
        "observed_owner_node_id": "node-0",
        "transfer_inhibition_operation_id": "operation-one",
        "transfer_inhibition_quiescent": True,
    }
    assert check(owner, True) is None
    for field, reason in (
        ("observed_owner_node_id", "owner-identity-mismatch"),
        ("transfer_inhibition_operation_id", "transfer-inhibition-operation-mismatch"),
        ("transfer_inhibition_quiescent", "transfer-inhibition-not-quiescent"),
    ):
        assert check({**owner, field: None}, True) == reason
    monkeypatch.setattr(cli, "_vm_ha_mtls_agent_evidence_matches", lambda *args: False)
    assert check(status) == "managed-mtls-evidence-mismatch"


def test_streaming_probe_preserves_evidence_before_later_failure(monkeypatch):
    emitted = []
    monkeypatch.setattr(probe, "read_regular", lambda path: b'{"apply_locked":true}')

    def fail_command(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(probe, "bounded_command", fail_command)
    with pytest.raises(KeyboardInterrupt):
        probe.collect_probe({"since": "2026-01-01", "until": "2026-01-02"}, emit=emitted.append)
    assert emitted and emitted[0]["data"]["apply_locked"] is True


def test_exact_provider_operations_extracted_without_local_id_or_free_text():
    value = {
        "operation_id": "local-operation",
        "accepted_cloud_operation_id": "cloud-operation-one",
        "nested": {"provider_operation_id": "cloud-operation-two", "message": "secret=value"},
    }
    value["cycle"] = value
    assert collector.operation_references(value) == ["cloud-operation-one", "cloud-operation-two"]


def test_cloud_reads_real_sdk_shapes_cidr_targets_routes_groups_and_operations(monkeypatch, capsys):
    import nebius.api.nebius.compute.v1 as c
    import nebius.api.nebius.vpc.v1 as v
    from google.protobuf.json_format import ParseDict

    from nebius_vpngw import nebius_auth
    from nebius_vpngw.deploy import security_groups

    def wire(cls, data):
        value = cls()
        ParseDict(data, value.__pb2_message__)
        return value

    closed = []
    monkeypatch.setattr(
        nebius_auth,
        "build_operator_sdk_client",
        lambda **kwargs: SimpleNamespace(sync_close=lambda: closed.append(True)),
    )

    def reply(value):
        return SimpleNamespace(wait=lambda: value)

    instance = wire(
        c.Instance,
        {
            "metadata": {
                "id": "computeinstance-one",
                "parent_id": "project-example",
                "name": "gateway-0",
            },
            "spec": {
                "network_interfaces": [{"name": "eth0", "subnet_id": "subnet-one"}],
                "cloud_init_user_data": "SUPER_PRIVATE_USER_DATA",
            },
            "status": {
                "network_interfaces": [
                    {
                        "name": "eth0",
                        "public_ip_address": {
                            "address": "192.0.2.10/32",
                            "allocation_id": "allocation-public",
                        },
                        "ip_address": {
                            "address": "10.0.0.2/24",
                            "allocation_id": "allocation-private",
                        },
                    }
                ]
            },
        },
    )
    operation = SimpleNamespace(
        id="cloud-operation-one",
        done=lambda: True,
        status=lambda: SimpleNamespace(code=SimpleNamespace(value=(0, "ok"))),
    )
    monkeypatch.setattr(
        c,
        "InstanceServiceClient",
        lambda sdk: SimpleNamespace(
            get_by_name=lambda request, **kwargs: reply(instance),
            operation_service=lambda: SimpleNamespace(
                get=lambda request, **kwargs: reply(operation)
            ),
        ),
    )
    objects = {
        "SubnetServiceClient": (
            v.Subnet,
            {
                "metadata": {"id": "subnet-one"},
                "spec": {"network_id": "network-one", "route_table_id": "table-one"},
            },
        ),
        "NetworkServiceClient": (v.Network, {"metadata": {"id": "network-one"}}),
        "AllocationServiceClient": (
            v.Allocation,
            {
                "metadata": {"id": "allocation-one", "resource_version": "123"},
                "status": {
                    "state": "ALLOCATED",
                    "details": {"allocated_cidr": "10.0.0.1/32"},
                    "assignment": {
                        "network_interface": {"instance_id": "computeinstance-one", "name": "eth0"}
                    },
                },
            },
        ),
        "SecurityGroupServiceClient": (
            v.SecurityGroup,
            {"metadata": {"id": "group-one"}, "spec": {"network_id": "network-one"}},
        ),
    }
    for name, (cls, data) in objects.items():

        def get(request, cls=cls, data=data, **kwargs):
            value = {
                **data,
                "metadata": {**data["metadata"], "id": request.id, "parent_id": "project-example"},
            }
            return reply(wire(cls, value))

        monkeypatch.setattr(v, name, lambda sdk, get=get: SimpleNamespace(get=get))
    routes = wire(
        v.ListRoutesResponse,
        {
            "items": [
                {
                    "metadata": {"id": "route-one"},
                    "spec": {
                        "destination": {"cidr": "10.0.0.0/24"},
                        "next_hop": {"allocation": {"id": "allocation-one"}},
                    },
                }
            ]
        },
    )
    rules = wire(
        v.ListSecurityRulesResponse,
        {
            "items": [
                {
                    "metadata": {"id": "rule-one"},
                    "spec": {
                        "access": "ALLOW",
                        "protocol": "UDP",
                        "ingress": {"destination_ports": [4500]},
                    },
                }
            ]
        },
    )
    monkeypatch.setattr(
        v,
        "RouteServiceClient",
        lambda sdk: SimpleNamespace(list=lambda request, **kwargs: reply(routes)),
    )
    monkeypatch.setattr(
        v,
        "SecurityRuleServiceClient",
        lambda sdk: SimpleNamespace(list=lambda request, **kwargs: reply(rules)),
    )
    monkeypatch.setattr(
        security_groups,
        "capture",
        lambda sdk, item: (
            security_groups.NICSecurityGroups(
                "eth0", "subnet-one", "network-one", (), ("group-one", "group-two")
            ),
        ),
    )
    worker.cloud(
        {
            "project_id": "project-example",
            "gateway_group": {"name": "gateway", "instance_count": 1},
        },
        ["cloud-operation-one"],
    )
    output = capsys.readouterr().out
    records = {item["probe"]: item for item in map(json.loads, output.splitlines())}
    assert records["target/0"]["data"]["address"] == "192.0.2.10"
    assert records["cloud/operation/0"]["data"]["done"] is True
    assert records["cloud/routes/0"]["data"]["items"][0]["metadata"]["id"] == "route-one"
    assert (
        records["cloud/routes/0"]["data"]["items"][0]["spec"]["next_hop"]["allocation"]["id"]
        == "allocation-one"
    )
    assert records["cloud/security-rules/0"]["data"]["items"][0]["spec"]["access"] == "ALLOW"
    assert records["cloud/security-rules/1"]["data"]["items"][0]["spec"]["access"] == "ALLOW"
    assert len(records) == len(output.splitlines())
    allocation = next(
        item["data"] for key, item in records.items() if key.startswith("cloud/allocation/")
    )
    assert allocation["metadata"]["resource_version"] == "123"
    assert allocation["status"]["allocation_details"]["allocated_cidr"] == "10.0.0.1/32"
    assert (
        allocation["status"]["assignment"]["network_interface"]["instance_id"]
        == "computeinstance-one"
    )
    assert (
        records["cloud/instance/0"]["data"]["status"]["network_interfaces"][0]["public_ip_address"][
            "address"
        ]
        == "192.0.2.10/32"
    )
    assert "SUPER_PRIVATE_USER_DATA" not in output
    assert not any(item["outcome"] != "ok" for item in records.values())
    assert closed == [True]

    # A slow auxiliary read must not hide the peer's management address.
    from google.protobuf.json_format import MessageToDict

    clock = [0.0]
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock[0])

    def discover(request, **kwargs):
        raw = MessageToDict(instance.__pb2_message__, preserving_proto_field_name=True)
        raw["metadata"]["name"] = request.name
        raw["metadata"]["id"] = "computeinstance-" + request.name
        return reply(wire(c.Instance, raw))

    monkeypatch.setattr(
        c, "InstanceServiceClient", lambda sdk: SimpleNamespace(get_by_name=discover)
    )

    def slow_auxiliary(*args, **kwargs):
        clock[0] = 100
        raise ConnectionError("PRIVATE_PROVIDER_ERROR")

    monkeypatch.setattr(v, "SubnetServiceClient", lambda sdk: SimpleNamespace(get=slow_auxiliary))
    worker.cloud(
        {"project_id": "project-example", "gateway_group": {"name": "gateway", "instance_count": 2}}
    )
    output = capsys.readouterr().out
    rows = list(map(json.loads, output.splitlines()))
    targets = [item["probe"] for item in rows if item["probe"].startswith("target/")]
    assert targets == ["target/0", "target/1"]
    assert "PRIVATE_PROVIDER_ERROR" not in output


def test_changed_configuration_and_boot_are_reported(config, tmp_path):
    def run(request, timeout):
        if request["action"] == "artifact":
            return [
                {
                    "probe": "selected-artifact",
                    "data": {"artifact_sha256": "a" * 64},
                    "outcome": "ok",
                }
            ], "ok"
        if request["action"] == "status":
            return [{"probe": "status", "data": {}, "outcome": "ok"}], "ok"
        if request["action"] == "cloud":
            return [], "ok"
        config.write_text(config.read_text().replace("connection-one", "connection-two"))
        return [
            {"probe": f"ha-{index}/native", "data": {"boot_id": f"boot-{index}"}, "outcome": "ok"}
            for index in (0, 1)
        ], "ok"

    path, code = collector.collect(config, output=tmp_path / "changed.zip", runner=run)
    assert code == 1
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        changed = {item["file"] for item in manifest["probes"] if item["outcome"] == "changed"}
        assert "nodes/0/observation-change.json" in changed
        assert "operator/topology-end.json" in changed


def test_cloud_source_metadata_survives_projection(config, tmp_path):
    def run(request, timeout):
        if request["action"] == "artifact":
            return [
                {
                    "probe": "selected-artifact",
                    "data": {"artifact_sha256": "a" * 64},
                    "outcome": "ok",
                }
            ], "ok"
        if request["action"] == "status":
            return [{"probe": "status", "data": {}, "outcome": "ok"}], "ok"
        if request["action"] == "cloud":
            return [
                {
                    "probe": "cloud/instance/0",
                    "data": {"id": "instance-one"},
                    "outcome": "ok",
                    "omitted_fields": 9,
                    "observed_at": 123.0,
                    "truncated_fields": 2,
                }
            ], "ok"
        return [], "unavailable"

    path, code = collector.collect(config, output=tmp_path / "metadata.zip", runner=run)
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    row = next(row for row in manifest["probes"] if row["file"] == "operator/cloud/instance/0.json")
    assert row["omitted_fields"] == 9
    assert row["observed_at"] == 123.0
    assert row["outcome"] == "source-limit"
    assert code == 1


def test_current_state_from_both_nodes_precedes_log_budget(config, tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "TOTAL_LIMIT", 20000)

    def run(request, timeout):
        if request["action"] == "artifact":
            return [
                {
                    "probe": "selected-artifact",
                    "data": {"artifact_sha256": "a" * 64},
                    "outcome": "ok",
                }
            ], "ok"
        if request["action"] == "status":
            return [{"probe": "status", "data": {}, "outcome": "ok"}], "ok"
        if request["action"] == "cloud":
            return [], "ok"
        if request["hostname"].endswith("-0"):
            return [
                {
                    "probe": "logs/kernel",
                    "outcome": "ok",
                    "data": probe.journal_events(
                        "\n".join(
                            json.dumps(
                                {"MESSAGE": "apply-lock-held", "__REALTIME_TIMESTAMP": str(index)}
                            )
                            for index in range(2000)
                        )
                    ),
                }
            ], "ok"
        return [
            {
                "probe": "network/routes",
                "outcome": "ok",
                "data": [
                    {"dst": f"10.{index}.0.0/16", "protocol": "static"} for index in range(50)
                ],
            }
        ], "ok"

    path, code = collector.collect(config, output=tmp_path / "priority.zip", runner=run)
    with zipfile.ZipFile(path) as archive:
        assert len(json.loads(archive.read("nodes/1/network/routes.json"))) == 50
    assert code == 1


def test_nonempty_projection_truncation_is_not_complete(config, tmp_path):
    def run(request, timeout):
        if request["action"] == "artifact":
            return [
                {
                    "probe": "selected-artifact",
                    "data": {"artifact_sha256": "a" * 64},
                    "outcome": "ok",
                }
            ], "ok"
        if request["action"] == "status":
            return [{"probe": "status", "data": {}, "outcome": "ok"}], "ok"
        if request["action"] == "cloud":
            return [], "ok"
        return [
            {
                "probe": "network/routes",
                "outcome": "ok",
                "data": [{"dst": "10.0.0.0/24"} for _ in range(5000)],
            }
        ], "ok"

    path, code = collector.collect(config, output=tmp_path / "truncated.zip", runner=run)
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert json.loads(archive.read("nodes/0/network/routes.json"))
    assert code == 1
    assert manifest["complete"] is False
    assert any(row["outcome"] == "source-limit" for row in manifest["probes"])


def test_journal_limit_is_visible_with_redacted_messages(monkeypatch):
    monkeypatch.setattr(probe, "read_regular", lambda path: b"{}")
    event = json.dumps(
        {"MESSAGE": "apply-lock-held PRIVATE_RAW_MESSAGE", "__REALTIME_TIMESTAMP": "123"}
    )

    def bounded(argv, **kwargs):
        if argv[0] == "journalctl":
            return ((event + "\n") * 5001).encode(), "ok", 0
        return b"{}", "ok", 0

    monkeypatch.setattr(probe, "bounded_command", bounded)
    records = probe.collect_probe(
        {"since": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z"}
    )
    logs = [row for row in records if row["probe"].startswith("logs/")]
    assert logs and all(row["outcome"] == "source-limit" for row in logs)
    assert "PRIVATE_RAW_MESSAGE" in json.dumps(records)


def test_cloud_next_hops_survive_safe_projection(config, tmp_path):
    value = {"spec": {"next_hop": {"allocation": {"id": "allocation-one"}}}}
    assert projection.project(value) == value
    value = {"spec": {"next_hop": {"default_egress_gateway": {}}}}
    assert projection.project(value) == value

    def run(request, timeout):
        if request["action"] == "artifact":
            return [
                {
                    "probe": "selected-artifact",
                    "data": {"artifact_sha256": "a" * 64},
                    "outcome": "ok",
                }
            ], "ok"
        if request["action"] == "status":
            return [{"probe": "status", "data": {}, "outcome": "ok"}], "ok"
        if request["action"] == "cloud":
            return [{"probe": "cloud/routes/0", "data": value, "outcome": "ok"}], "ok"
        return [], "unavailable"

    path, _ = collector.collect(config, output=tmp_path / "next-hop.zip", runner=run)
    with zipfile.ZipFile(path) as archive:
        assert json.loads(archive.read("operator/cloud/routes/0.json")) == value


@pytest.mark.parametrize(
    "reason",
    [
        "data-plane-mode-mismatch",
        "heartbeat-capability-missing",
        "heartbeat-schema-mismatch",
        "peer-heartbeat-not-fresh",
        "promotion-readiness-mismatch",
        "owner-identity-mismatch",
        "transfer-inhibition-operation-mismatch",
        "transfer-inhibition-not-quiescent",
        "managed-mtls-evidence-mismatch",
    ],
)
def test_exact_activation_predicate_survives_bundle(config, tmp_path, reason):
    state = config.with_name(config.name + ".vm-ha-lifecycle.json")
    state.write_text(json.dumps({"first": {"phase": "verify-fenced-node-0", "predicate": reason}}))
    path, _ = collector.collect(config, output=tmp_path / "predicate.zip", runner=fake_runner)
    with zipfile.ZipFile(path) as archive:
        assert json.loads(archive.read("operator/state/0.json"))["first"]["predicate"] == reason


def test_remote_record_limit_is_visible(config, tmp_path):
    def run(request, timeout):
        if request["action"] == "artifact":
            return [
                {
                    "probe": "selected-artifact",
                    "data": {"artifact_sha256": "a" * 64},
                    "outcome": "ok",
                }
            ], "ok"
        if request["action"] == "status":
            return [{"probe": "status", "data": {}, "outcome": "ok"}], "ok"
        if request["action"] == "cloud":
            return [], "ok"
        return [{"probe": "host/time", "data": {}, "outcome": "ok"} for _ in range(257)], "ok"

    path, code = collector.collect(config, output=tmp_path / "record-limit.zip", runner=run)
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert code == 1
    assert any(
        row["file"] == "nodes/0/record-limit.json" and row["outcome"] == "source-limit"
        for row in manifest["probes"]
    )


def test_archive_trimming_preserves_newest_events_across_sources():
    def envelope(timestamps):
        return json.dumps(
            {
                "coverage": {"exported": 2, "discarded": 0},
                "events": [{"timestamp": stamp} for stamp in timestamps],
            }
        ).encode()

    entries = {
        "nodes/0/logs/kernel.json": envelope(["30", "10"]),
        "nodes/1/logs/kernel.json": envelope(["40", "20"]),
        "nodes/1/ha-0/native.json": b'{"apply_locked":true}',
    }
    manifest = [{"file": name, "outcome": "ok"} for name in entries]
    assert collector.trim_oldest_logs(entries, manifest)
    assert json.loads(entries["nodes/0/logs/kernel.json"])["events"] == [{"timestamp": "30"}]
    assert json.loads(entries["nodes/1/logs/kernel.json"])["events"] == [{"timestamp": "40"}]
    assert entries["nodes/1/ha-0/native.json"] == b'{"apply_locked":true}'
    assert sum(row.get("truncated_events", 0) for row in manifest) == 2


def test_probe_observes_timer_os_and_newest_journal_first(monkeypatch):
    calls = []

    def read(path):
        if path.name == "os-release":
            return b'ID=ubuntu\nVERSION_ID="24.04"\nSECRET="PRIVATE_OS_TEXT"\n'
        return b"{}"

    monkeypatch.setattr(probe, "read_regular", read)

    def bounded(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "journalctl":
            assert "--reverse" in argv
            return (
                b'{"MESSAGE":"apply-lock-held","__REALTIME_TIMESTAMP":"200"}\n',
                "source-limit",
                0,
            )
        return b"{}", "ok", 0

    monkeypatch.setattr(probe, "bounded_command", bounded)
    # Host files are Linux-only; verify the OS reader independently of host shape
    # through a temporary platform-neutral /proc fixture below.
    monkeypatch.setattr(
        probe.Path,
        "read_text",
        lambda path, *a, **k: (
            "MemTotal: 1024 kB\nMemAvailable: 512 kB\n"
            if str(path) == "/proc/meminfo"
            else "10.0 0.0"
            if str(path) == "/proc/uptime"
            else "boot-one"
            if str(path) == "/proc/sys/kernel/random/boot_id"
            else ""
        ),
    )
    records = probe.collect_probe(
        {"since": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z"}
    )
    facts = next(row["data"] for row in records if row["probe"] == "host/resources")
    assert facts["os_id"] == "ubuntu" and facts["os_version_id"] == "24.04"
    assert any(row["probe"] == "services/nebius-vpngw-fix-routes.timer" for row in records)
    assert all(
        row["data"]["events"][0]["timestamp"] == "200"
        for row in records
        if row["probe"].startswith("logs/")
    )
    assert "PRIVATE_OS_TEXT" not in json.dumps(records)


def test_package_worker_history_keeps_unfinished_worker_and_reports_limit(monkeypatch, tmp_path):
    paths = []
    for index in range(17):
        path = tmp_path / f"{index:064x}" / "transport.json"
        path.parent.mkdir()
        path.write_text(
            json.dumps(
                {
                    "identity": f"{index:064x}",
                    "terminal": None if index == 16 else "complete",
                    "waiting": index == 16,
                    "unit": f"nebius-vpngw-package-{index:064x}-1.service",
                }
            )
        )
        os.utime(path, (index if index < 16 else 0, index if index < 16 else 0))
        paths.append(path)
    monkeypatch.setattr(
        probe.Path,
        "glob",
        lambda path, pattern: (
            iter(paths)
            if str(path) == "/var/lib/nebius-vpngw/runtime-migration/workers"
            else iter(())
        ),
    )
    monkeypatch.setattr(probe, "bounded_command", lambda *a, **k: (b"{}", "ok", 0))
    records = probe.collect_probe(
        {"since": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z"}
    )
    assert any(
        row["probe"] == "state/worker-limit/0" and row["outcome"] == "source-limit"
        for row in records
    )
    assert any(
        row["data"].get("waiting") is True
        for row in records
        if row["probe"].startswith("state/worker/")
    )


def test_bundle_retains_distinct_first_and_latest_apply_failures(config, tmp_path):
    root = tmp_path / ".local/state/nebius-vpngw/apply/project-example/gateway/failures"
    (root / "latest").mkdir(parents=True)
    execution = "1" * 32
    for directory, predicate in (
        (root, "peer-heartbeat-not-fresh"),
        (root / "latest", "status-observation-unavailable"),
    ):
        (directory / (execution + ".json")).write_text(
            json.dumps(
                {
                    "schema": "nebius-vpngw.apply-failure.v1",
                    "execution_id": execution,
                    "failure": {"phase": "verify-fenced-node-0", "predicate": predicate},
                }
            )
        )
    path, code = collector.collect(config, output=tmp_path / "failures.zip", runner=fake_runner)
    assert code == 0
    with zipfile.ZipFile(path) as archive:
        states = [
            json.loads(archive.read(name))
            for name in archive.namelist()
            if name.startswith("operator/state/")
        ]
    assert any(
        row.get("first", {}).get("failure", {}).get("predicate") == "peer-heartbeat-not-fresh"
        for row in states
    )
    assert any(
        row.get("latest", {}).get("failure", {}).get("predicate")
        == "status-observation-unavailable"
        for row in states
    )


@pytest.mark.parametrize("stats_key", ["stats64", "stats"])
def test_link_counter_snapshots_survive_probe_and_archive(config, tmp_path, monkeypatch, stats_key):
    snapshots = 0

    def command(argv, **kwargs):
        nonlocal snapshots
        if argv == ["ip", "-j", "-s", "link", "show"]:
            snapshots += 1
            return (
                json.dumps(
                    [
                        {
                            "ifname": "eth0",
                            stats_key: {
                                "rx": {
                                    "bytes": 100,
                                    "packets": snapshots,
                                    "errors": 2,
                                    "dropped": 3,
                                },
                                "tx": {"bytes": 200, "packets": 4, "errors": 5, "dropped": 6},
                                "secret": "PRIVATE_COUNTER_SENTINEL",
                            },
                        }
                    ]
                ).encode(),
                "ok",
                0,
            )
        return b"{}", "ok", 0

    monkeypatch.setattr(probe, "bounded_command", command)
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)
    records = probe.collect_probe({"since": "2026-01-01", "until": "2026-01-02"})
    links = [record for record in records if record["probe"].endswith("/links")]
    path, _ = collector.collect(
        config,
        output=tmp_path / "counters.zip",
        runner=lambda request, timeout: (
            (links, "ok") if request["action"] == "remote" else ([], "ok")
        ),
    )
    with zipfile.ZipFile(path) as archive:
        for index in (0, 1):
            value = json.loads(archive.read(f"nodes/0/network-{index}/links.json"))[0]
            assert value["rx_packets"] == index + 1
            assert {
                key: value[key] for key in ("rx_errors", "rx_dropped", "tx_errors", "tx_dropped")
            } == {"rx_errors": 2, "rx_dropped": 3, "tx_errors": 5, "tx_dropped": 6}
        assert b"PRIVATE_COUNTER_SENTINEL" not in b"".join(
            archive.read(n) for n in archive.namelist()
        )


def test_package_request_states_survive_projection_and_archive(config, tmp_path):
    requests = {
        f"{index:064x}": {
            "state": state,
            "request": {"action": "install", "psk": "PRIVATE_REQUEST_SENTINEL"},
            "result": {"phase": "complete"},
        }
        for index, state in enumerate(("accepted", "complete", "failed"))
    }
    requests["PRIVATE_REQUEST_KEY"] = {"state": "accepted"}
    raw = {"requests": requests, "waiting": True}
    value = projection.project(raw)
    path, _ = collector.collect(
        config,
        output=tmp_path / "requests.zip",
        runner=lambda request, timeout: (
            ([{"probe": "state/worker/0", "outcome": "ok", "data": value}], "ok")
            if request["action"] == "remote"
            else ([], "ok")
        ),
    )
    with zipfile.ZipFile(path) as archive:
        exported = json.loads(archive.read("nodes/0/state/worker/0.json"))
        assert {key: item["state"] for key, item in exported["requests"].items()} == {
            f"{index:064x}": state for index, state in enumerate(("accepted", "complete", "failed"))
        }
        assert b"PRIVATE_REQUEST" not in b"".join(archive.read(n) for n in archive.namelist())
    # A digest-shaped key is only meaningful inside the product's requests map.
    assert projection.project({"members": {"0" * 64: {"state": "accepted"}}}) == {"members": {}}


@pytest.mark.parametrize("repair", [False, True])
def test_prepared_worker_identity_survives_guest_and_archive(config, tmp_path, monkeypatch, repair):
    identity = "a" * 64
    unit = f"nebius-vpngw-{'repair-' if repair else ''}package-{identity}-1.service"
    transport = {"identity": identity, "unit": unit, "requests": {}, "waiting": True}
    path = tmp_path / "transport.json"
    path.write_text(json.dumps(transport))
    root = (
        "/var/lib/nebius-vpngw-repair/workers"
        if repair
        else "/var/lib/nebius-vpngw/runtime-migration/workers"
    )
    monkeypatch.setattr(
        probe,
        "select_worker_transports",
        lambda selected, deadline: ([path] if str(selected) == root else [], False),
    )
    monkeypatch.setattr(probe, "bounded_command", lambda *a, **k: (b"{}", "ok", 0))
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)
    records = probe.collect_probe({"since": "2026-01-01", "until": "2026-01-02"})
    selected = [record for record in records if record["probe"] == "state/worker/0"]
    assert len(selected) == 1
    assert selected[0]["data"] == transport
    bundle, _ = collector.collect(
        config,
        output=tmp_path / "worker.zip",
        runner=lambda request, timeout: (
            (selected, "ok") if request["action"] == "remote" else ([], "ok")
        ),
    )
    with zipfile.ZipFile(bundle) as archive:
        assert json.loads(archive.read("nodes/0/state/worker/0.json")) == transport


def test_worker_identifiers_reject_unrecognized_text():
    assert projection.project({"identity": "PRIVATE_IDENTITY", "unit": "private.service"}) == {}
    for identity in ("a" * 63, "g" * 64, "a" * 65):
        assert (
            projection.project(
                {"identity": identity, "unit": f"nebius-vpngw-package-{identity}-1.service"}
            )
            == {}
        )
    assert projection.project({"identity": {"node_id": "gateway-0"}}) == {
        "identity": {"node_id": "gateway-0"}
    }


@pytest.mark.parametrize("origin", ["initial", "upgrade"])
def test_archive_typed_checkpoint_preserves_artifact_origin_without_raw_effect(
    config, tmp_path, origin
):
    from dataclasses import replace

    from test_vm_ha_corrective import failed_release

    state = failed_release()
    cfg = yaml.safe_load(config.read_text())
    cfg["project_id"] = state.project_id
    cfg["gateway_group"]["name"] = state.gateway_name
    cfg["gateway_group"]["vm_ha"]["cluster_id"] = state.cluster_id
    config.write_text(yaml.safe_dump(cfg))
    bindings = dict(state.transaction.resource_bindings)
    if origin == "initial":
        bindings["heartbeat-initial-artifact"] = bindings.pop("heartbeat-upgrade-artifact")
    state = replace(
        state,
        transaction=replace(
            state.transaction,
            resource_bindings=tuple(sorted(bindings.items())),
            checkpoint="before-activate-PRIVATE_EFFECT_SENTINEL",
            pending_effect="activate-PRIVATE_EFFECT_SENTINEL",
        ),
    )
    lifecycle = config.with_name(config.name + ".vm-ha-lifecycle.json")
    lifecycle.write_text(json.dumps(state.to_dict()))
    lifecycle.chmod(0o600)
    path, _ = collector.collect(config, output=tmp_path / "checkpoint.zip", runner=fake_runner)
    with zipfile.ZipFile(path) as archive:
        view = json.loads(archive.read("operator/checkpoints/0.json"))
        assert view["origin"] == origin
        assert view[origin + "_artifact_sha256"] == bindings["heartbeat-" + origin + "-artifact"]
        assert view["checkpoint_category"] == "activation"
        assert b"PRIVATE_EFFECT_SENTINEL" not in b"\n".join(
            archive.read(name) for name in archive.namelist()
        )


def test_archive_command_checkpoint_uses_real_owner_and_safe_identifiers(config, tmp_path):
    from nebius_vpngw.apply_approval import digest
    from nebius_vpngw.deploy.apply_command import CommandJournal
    from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect

    intent = {"configured": True}
    effect = Effect(
        "guest:PRIVATE_EFFECT_SENTINEL",
        "ordinary-runtime",
        "apply",
        ("private-target",),
        digest(intent),
        "a" * 64,
        False,
    )
    plan = CommandPlan("project-example", "gateway", "b" * 64, "c" * 64, (effect,))
    journal = CommandJournal("project-example", "gateway")
    with journal:
        journal.begin(plan, intents={effect.key: intent})
    path, _ = collector.collect(
        config, output=tmp_path / "command-checkpoint.zip", runner=fake_runner
    )
    with zipfile.ZipFile(path) as archive:
        views = [
            json.loads(archive.read(name))
            for name in archive.namelist()
            if name.startswith("operator/checkpoints/")
        ]
        view = next(row for row in views if row["source"] == "command")
        assert view["selected_artifact_sha256"] == "c" * 64
        assert view["effects"][0]["category"] == "ordinary-runtime"
        assert view["effects"][0]["progress"] == "planned"
        assert view["unknown_effects"] == 0
        assert "PRIVATE_EFFECT_SENTINEL" not in json.dumps(view)


@pytest.mark.parametrize(
    "failure,direction,assessment",
    [(False, "peer-to-gateway", "authenticated-receive"), (True, "gateway-to-peer", "tls-failure")],
)
@pytest.mark.parametrize("archive_delay", [0, 60])
def test_archive_peer_channel_preserves_observed_direction(
    config, tmp_path, failure, direction, assessment, archive_delay
):
    captured_at = time.time() - archive_delay

    def run(request, timeout):
        rows, outcome = fake_runner(request, timeout)
        if request["action"] == "remote":
            rows = [
                {
                    "probe": "ha-1/native",
                    "outcome": "ok",
                    "observed_at": captured_at,
                    "data": {
                        "guard_boot_id": "boot",
                        "apply_convergence": {"boot_id": "boot", "observed_at": captured_at - 1},
                        "heartbeat": {
                            "peer_v6_fresh": not failure,
                            "peer_send_error_kind": "tls",
                            "peer_send_consecutive_failures": int(failure),
                        },
                    },
                }
            ]
        return rows, outcome

    path, _ = collector.collect(config, output=tmp_path / "channels.zip", runner=run)
    with zipfile.ZipFile(path) as archive:
        view = json.loads(archive.read("nodes/0/network/peer-channel.json"))
        assert view["direction"] == direction
        assert view["assessment"] == assessment
        manifest = json.loads(archive.read("manifest.json"))
        sample = next(
            row for row in manifest["probes"] if row["file"] == "nodes/0/network/peer-channel.json"
        )
        assert sample["observed_at"] == captured_at


@pytest.mark.parametrize("captured_at", [None, "invalid", float("nan"), True, 100])
def test_archive_peer_channel_keeps_unknown_capture_or_stale_evidence_unknown(
    config, tmp_path, captured_at
):
    def run(request, timeout):
        rows, outcome = fake_runner(request, timeout)
        if request["action"] == "remote":
            rows = [
                {
                    "probe": "ha-1/native",
                    "outcome": "ok",
                    "observed_at": captured_at,
                    "data": {
                        "guard_boot_id": "boot",
                        "apply_convergence": {"boot_id": "boot", "observed_at": 80},
                        "heartbeat": {"peer_v6_fresh": True},
                    },
                }
            ]
        return rows, outcome

    path, _ = collector.collect(config, output=tmp_path / "unknown-channel.zip", runner=run)
    with zipfile.ZipFile(path) as archive:
        view = json.loads(archive.read("nodes/0/network/peer-channel.json"))
        assert view["assessment"] == "unknown"
        assert view["direction"] == "unknown"


@pytest.mark.parametrize("code", ["asset_retirement_unproven", "asset_retirement_changed"])
@pytest.mark.parametrize("stage", ["inspect", "stage", "publish"])
def test_archive_retains_closed_asset_retirement_failure(config, tmp_path, code, stage):
    from nebius_vpngw.deploy.recovery import GuestFailure

    failure = GuestFailure(code, stage, "PRIVATE_ERROR_SENTINEL")
    state = config.with_name(config.name + ".vm-ha-lifecycle.json")
    state.write_text(json.dumps({"first": failure.first.mapping(), "message": str(failure)}))
    path, _ = collector.collect(config, output=tmp_path / "retirement.zip", runner=fake_runner)
    with zipfile.ZipFile(path) as archive:
        view = json.loads(archive.read("operator/state/0.json"))
        assert view["first"]["phase"] == "guest-" + stage
        assert view["first"]["predicate"] == "guest-" + code.replace("_", "-")
        assert b"PRIVATE_ERROR_SENTINEL" not in b"\n".join(
            archive.read(name) for name in archive.namelist()
        )
