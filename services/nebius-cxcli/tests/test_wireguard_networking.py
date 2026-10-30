from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml

from nebius_cxcli import wireguard_bootstrap as bootstrap
from nebius_cxcli import wireguard_networking as network
from nebius_cxcli import wireguard_verification as verification


@pytest.fixture
def guest(tmp_path, monkeypatch):
    path = (
        Path(__file__).resolve().parents[3]
        / "platform-infra/modules/wireguard-gw/wireguard_network.py"
    )
    spec = importlib.util.spec_from_file_location("guest_network", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("STATE_DIR", "WG_DIR", "CLIENTS_DIR"):
        folder = tmp_path / name
        folder.mkdir()
        monkeypatch.setattr(module, name, folder)
    for name, filename in (
        ("CONFIG_PATH", "config.json"),
        ("REGISTRY_PATH", "clients.json"),
        ("LOCK_PATH", "lock"),
    ):
        monkeypatch.setattr(module, name, module.STATE_DIR / filename)
    monkeypatch.setattr(module, "require_root", lambda: None)
    return module


@pytest.mark.parametrize(
    "uplink,requested,expected",
    [(1450, None, 1370), (1500, None, 1420), (9000, None, 1420), (1450, 1280, 1280)],
)
def test_mtu_limits(guest, uplink, requested, expected):
    assert guest.choose_mtu(requested, uplink) == expected


@pytest.mark.parametrize(
    "uplink,requested", [(1350, None), (1450, 1420), (1500, 1279), (1500, 1370.5), (1500, True)]
)
def test_invalid_mtu_fails_before_network_change(guest, uplink, requested):
    with pytest.raises(SystemExit):
        guest.choose_mtu(requested, uplink)


def test_mss_rules_are_scoped_idempotent_and_remove_only_owned_rules(guest, monkeypatch):
    unrelated = "-A FORWARD -j ACCEPT"
    rules = [unrelated]

    def run(args, **kwargs):
        operation = args[5]
        line = " ".join(["-A", *args[6:]])
        if operation == "-S":
            return NS(stdout="\n".join(rules))
        if operation == "-A":
            rules.append(line)
        elif operation == "-D":
            rules.remove(line)
        else:
            pytest.fail(str(args))
        return NS(stdout="", returncode=0)

    monkeypatch.setattr(guest, "run", run)
    guest.mss_rules("up")
    guest.mss_rules("up")
    assert len(rules) == 3
    assert all("--clamp-mss-to-pmtu" in r and "wg0" in r for r in rules[1:])
    rules.append(rules[-1])
    guest.mss_rules("up")
    assert len(rules) == 3
    guest.mss_rules("down")
    assert rules == [unrelated]


def test_reconciliation_preserves_keys_peers_and_resumes_same_revision(guest, monkeypatch):
    guest.CONFIG_PATH.write_text('{"wireguard_tunnel_cidr":"10.0.0.1/24"}')
    guest.REGISTRY_PATH.write_text('{"clients":[]}')
    server = guest.WG_DIR / "wg0.conf"
    server.write_text(
        "[Interface]\nPrivateKey = fixture-private\nAddress = 10.0.0.1/24\nPostUp = existing-firewall\n"
    )
    monkeypatch.setattr(
        guest, "existing_server_keys", lambda: ("fixture-private", "fixture-public")
    )
    monkeypatch.setattr(guest, "validate_clients", lambda registry: [])
    monkeypatch.setattr(guest, "effective_mtu", lambda config: 1370)
    mtu = [1420]
    monkeypatch.setattr(guest, "interface_mtu", lambda interface: mtu[0])

    def run(args, **kwargs):
        if args == ["wg", "show", "wg0", "peers"]:
            return NS(stdout="")
        if args == ["wg", "show", "wg0", "public-key"]:
            return NS(stdout="fixture-public")
        if args[:3] == ["ip", "link", "set"]:
            mtu[0] = int(args[-1])
            return NS(stdout="")
        pytest.fail("Unexpected identity-changing command")

    monkeypatch.setattr(guest, "run", run)
    monkeypatch.setattr(guest, "mss_rules", lambda action: None)
    revision = hashlib.sha256(Path(guest.__file__).read_bytes()).hexdigest()
    request = {"revision": revision, "wireguard_mtu": None}
    real_atomic = guest.atomic

    def interrupted(path, text):
        if path == server:
            raise OSError("interrupted")
        real_atomic(path, text)

    monkeypatch.setattr(guest, "atomic", interrupted)
    with pytest.raises(OSError):
        guest.reconcile_network(request)
    assert (guest.STATE_DIR / "network-rollback.json").exists()
    with pytest.raises(SystemExit):
        guest.assert_no_pending_network()
    with pytest.raises(SystemExit):
        guest.reconcile_network({**request, "wireguard_mtu": 1280})
    monkeypatch.setattr(guest, "atomic", real_atomic)
    guest.reconcile_network(request)
    first = server.read_text()
    guest.reconcile_network(request)
    assert server.read_text() == first
    assert "MTU = 1370" in first and "PrivateKey = fixture-private" in first
    assert "PostUp = existing-firewall" in first
    assert not (guest.STATE_DIR / "network-rollback.json").exists()


def test_missing_registry_is_not_treated_as_no_peers(guest):
    guest.CONFIG_PATH.write_text("{}")
    with pytest.raises(SystemExit):
        guest.reconcile_network(
            {"revision": hashlib.sha256(Path(guest.__file__).read_bytes()).hexdigest()}
        )


def test_first_boot_without_clients_creates_registry_for_reconciliation(guest, monkeypatch):
    guest.CONFIG_PATH.write_text('{"wireguard_tunnel_cidr":"10.0.0.1/24"}')
    monkeypatch.setattr(guest, "ensure_server_keys", lambda: None)
    monkeypatch.setattr(
        guest, "existing_server_keys", lambda: ("fixture-private", "fixture-public")
    )
    monkeypatch.setattr(guest, "ensure_runtime_config", lambda config: None)
    monkeypatch.setattr(guest, "configure_sysctl_and_firewall", lambda config: None)
    monkeypatch.setattr(guest, "ensure_ssh_service", lambda: None)
    monkeypatch.setattr(guest, "detect_wan_interface", lambda: "eth0")
    monkeypatch.setattr(guest, "interface_mtu", lambda interface: 1370)
    monkeypatch.setattr(guest, "effective_mtu", lambda config: 1370)
    monkeypatch.setattr(guest, "mss_rules", lambda action: None)

    def run(args, **kwargs):
        if args == ["wg", "show", "wg0", "public-key"]:
            return NS(stdout="fixture-public")
        if args == ["wg", "show", "wg0", "peers"] or args[:2] in (
            ["wg-quick", "strip"],
            ["systemctl", "enable"],
            ["systemctl", "restart"],
            ["ip", "link"],
        ):
            return NS(stdout="")
        pytest.fail("Unexpected guest command")

    monkeypatch.setattr(guest, "run", run)
    clients_file = guest.STATE_DIR / "bootstrap-clients.json"
    clients_file.write_text("[]")
    guest.init_server(NS())
    guest.bootstrap(NS(clients_file=str(clients_file)))
    request = {"revision": hashlib.sha256(Path(guest.__file__).read_bytes()).hexdigest()}
    result = guest.reconcile_network(request)
    assert result["clients_preserved"] == 0
    assert guest.load_registry() == {"version": 1, "clients": []}
    assert guest.REGISTRY_PATH.stat().st_mode & 0o777 == 0o600
    assert guest.reconcile_network(request) == result


def test_asset_is_bound_to_selected_source_and_digest(tmp_path, monkeypatch):
    (tmp_path / "wireguard_network.py").write_text("print('fixture')\n")
    monkeypatch.setattr(network, "_module_inspection_path", lambda source: (tmp_path, None))
    config = {"infra": {"components": [{"id": "wireguard-gw", "instance_id": "vpn", "inputs": {}}]}}
    sources = [
        {
            "component_id": "wireguard-gw",
            "instance_id": "vpn",
            "module_name": "vpn",
            "source": "fixture",
        }
    ]
    manifest = {
        "render": {
            "module_sources": sources,
            "wireguard_network": network.freeze_network_assets(config, sources),
        }
    }
    assert network.network_asset(manifest, "vpn")["wireguard_mtu"] is None
    manifest["render"]["wireguard_network"]["vpn"]["content"] += "# tampered"
    with pytest.raises(RuntimeError, match="digest"):
        network.network_asset(manifest, "vpn")


def test_exact_applied_bootstrap_identity_and_hash():
    body = "# fixture"
    record = {"connection": {"instance_id": "vm", "boot_disk_id": "disk"}}
    resources = [
        {
            "address": "module.vpn.module.vm.nebius_compute_v1_instance.this",
            "type": "nebius_compute_v1_instance",
            "values": {
                "id": "vm",
                "boot_disk": {"existing_disk": {"id": "disk"}},
                "cloud_init_user_data": body,
            },
        },
        {
            "address": "module.vpn.module.vm.terraform_data.cloud_init_user_data_revision",
            "type": "terraform_data",
            "values": {"input": hashlib.sha256(body.encode()).hexdigest()},
        },
    ]
    state = {"values": {"root_module": {"child_modules": [{"resources": resources}]}}}
    assert bootstrap.payload_from_state(state, "vpn", record) == body
    resources[0]["values"]["boot_disk"]["existing_disk"]["id"] = "other"
    with pytest.raises(RuntimeError, match="disagrees"):
        bootstrap.payload_from_state(state, "vpn", record)


def test_bootstrap_blocks_firstboot_input_changes_but_allows_mtu():
    access = {"generation": "fixture", "ssh_user": "ubuntu"}
    body = yaml.safe_dump(
        {
            "write_files": [
                {"path": "/etc/nebius-wireguard/access.json", "content": json.dumps(access)},
                {
                    "path": "/usr/local/bin/setup-wireguard.sh",
                    "content": 'cat > /etc/nebius-wireguard/config.json <<\'EOF\'\n{"wireguard_tunnel_cidr":"10.0.0.1/24"}\nEOF',
                },
            ]
        }
    )
    record = {"bootstrap": {"generation": "fixture"}, "ssh_user": "ubuntu"}
    bootstrap.validate_bootstrap(body, record, {"wireguard_mtu": 1370})
    with pytest.raises(RuntimeError, match="immutable"):
        bootstrap.validate_bootstrap(body, record, {"wireguard_tunnel_cidr": "10.1.0.1/24"})


def test_client_reports_replace_prior_success_and_redact_errors(tmp_path, monkeypatch):
    verification.write_report(tmp_path, [{"status": "PASS", "reason": "fixture"}])

    def failure(*args, **kwargs):
        raise RuntimeError("secret endpoint fixture")

    monkeypatch.setattr(verification, "verify_client", failure)
    result = verification.probe({}, None)
    assert result["status"] == "FAIL" and "secret" not in result["reason"]
    report = verification.write_report(tmp_path, [result])
    assert report["status"] == "FAIL"
    assert "PASS" not in (tmp_path / "wireguard-client-verification.md").read_text()
    assert (tmp_path / "wireguard-client-verification.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "failure,expected",
    [
        (None, "PASS"),
        ("route", "FAIL"),
        ("mtu", "FAIL"),
        ("anonymous", "FAIL"),
        ("small", "PENDING"),
        ("transfer", "FAIL"),
        ("context", "PENDING"),
        ("snapshot", "FAIL"),
    ],
)
def test_full_client_probe_checks_real_boundaries(monkeypatch, failure, expected):
    import ipaddress
    import time
    from contextlib import nullcontext

    from nebius_cxcli import cli

    monkeypatch.setattr(
        cli, "_kubeconfig_env_for_context", lambda *a, **k: {"KUBECONFIG": "frozen-fixture"}
    )

    def verify_snapshot(path, **kwargs):
        assert path == Path("frozen-fixture")
        if failure == "snapshot":
            raise RuntimeError("Context changed while copying")

    monkeypatch.setattr(verification, "verify_context_cluster", verify_snapshot)

    client = {
        "address": "10.8.0.2",
        "routes": [ipaddress.ip_network("10.0.0.0/8")],
        "public_key": "client",
        "server_key": "gateway",
        "mtu": 1370,
    }
    monkeypatch.setattr(
        verification, "active_interface", lambda c: ("utun1", 1420 if failure == "mtu" else 1370)
    )
    monkeypatch.setattr(verification, "local_mtu_limit", lambda c: 1420)
    monkeypatch.setattr(verification, "resolve_addresses", lambda *a: ["10.1.0.10"])

    def route(*args):
        if failure == "route":
            raise RuntimeError("wrong interface")

    monkeypatch.setattr(verification, "assert_route", route)
    calls = []

    def status(*args):
        calls.append(True)
        transfer = len(calls) if failure != "transfer" else 1
        return {
            "server_public_key": "gateway",
            "peer_found": True,
            "peer_allowed_ips": "10.8.0.2/32",
            "mtu": 1370,
            "peer_transfer": [transfer, transfer],
            "peer_handshake": time.time(),
        }

    monkeypatch.setattr(verification, "remote_network", status)

    def context(*args, **kwargs):
        if failure == "context":
            raise verification.VerificationPending("No matching context")
        return Path("fixture"), "fixture"

    monkeypatch.setattr(verification, "selected_context", context)
    monkeypatch.setattr(verification.socket, "create_connection", lambda *a, **k: nullcontext(None))
    tls_context = NS(minimum_version=verification.ssl.TLSVersion.MINIMUM_SUPPORTED)

    def wrap_socket(*args, **kwargs):
        assert tls_context.minimum_version == verification.ssl.TLSVersion.TLSv1_2
        return nullcontext(None)

    tls_context.wrap_socket = wrap_socket
    monkeypatch.setattr(verification.ssl, "create_default_context", lambda **k: tls_context)

    def kubectl(args, env, **kwargs):
        assert "--disable-compression=true" in args and "--context" in args
        assert env["KUBECONFIG"] == "frozen-fixture"
        if "whoami" in args:
            return json.dumps(
                {
                    "status": {
                        "userInfo": {
                            "username": "system:anonymous" if failure == "anonymous" else "fixture"
                        }
                    }
                }
            ).encode()
        return json.dumps(
            {"kind": "APIResourceList", "resources": "x" * (10 if failure == "small" else 8192)}
        ).encode()

    monkeypatch.setattr(verification, "bounded_kubectl", kubectl)
    assert (
        verification.probe(client, None, server="https://fixture.invalid", ca="fixture")["status"]
        == expected
    )


def test_disconnected_deploy_is_pending_without_cloud_reads(tmp_path):
    paths = NS(project_dir=tmp_path, reports_dir=tmp_path / "reports")
    executor = NS(
        config={"infra": {"components": [{"id": "wireguard-gw", "enabled": True}]}},
        manifest={"execution": {"targets": [{"component_id": "mk8s", "target_ref": "cluster"}]}},
        plan=NS(selected_targets=["cluster"]),
        paths=paths,
        cli=NS(console=NS(print=lambda *a: None)),
    )
    assert verification.verify_after_deploy(executor, paths)["status"] == "PENDING"
    executor.manifest["execution"]["targets"] = []
    assert verification.verify_after_deploy(executor, paths)["status"] == "NOT_APPLICABLE"


def test_automatic_target_scope_is_bound_to_vpc():
    gateway = {"inputs": {"network_id": "vpc-one"}}
    config = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "one",
                    "inputs": {"cluster": {"network_id": "vpc-one"}},
                },
                {
                    "id": "mk8s",
                    "instance_id": "two",
                    "inputs": {"cluster": {"network_id": "vpc-two"}},
                },
            ]
        }
    }
    targets = [{"instance_id": "one"}, {"instance_id": "two"}]
    assert verification.eligible_targets(config, gateway, targets) == targets[:1]


def test_automatic_same_vpc_targets_respect_client_destination_selection(monkeypatch):
    import ipaddress

    client = {"routes": [ipaddress.ip_network("10.1.0.0/24")]}
    monkeypatch.setattr(
        verification,
        "resolve_addresses",
        lambda host, port: ["10.1.0.10"] if host == "one.invalid" else ["10.2.0.10"],
    )
    assert verification.covers_endpoint(client, "https://one.invalid")
    assert not verification.covers_endpoint(client, "https://two.invalid")


def test_macos_identifies_unique_tunnel_without_root_mapping(monkeypatch):
    monkeypatch.setattr(verification.platform, "system", lambda: "Darwin")
    text = "utun5: flags=8051<UP> mtu 1370\n\tinet 10.8.0.2 netmask 0xffffffff\nen0: flags=8863<UP> mtu 1500\n\tinet 192.0.2.1 netmask 0xffffff00\n"
    monkeypatch.setattr(verification, "_run", lambda args: text)
    client = {"path": Path("wg-client.conf"), "address": "10.8.0.2"}
    assert verification.active_interface(client) == ("utun5", 1370)
    text += "utun6: flags=8051<UP> mtu 1420\n\tinet 10.8.0.2 netmask 0xffffffff\n"
    with pytest.raises(verification.VerificationPending):
        verification.active_interface(client)


@pytest.mark.parametrize("dry_run,lost_ack", [(False, False), (False, True), (True, False)])
def test_fresh_bootstrap_freezes_once_and_preview_never_writes(dry_run, lost_ack):
    from nebius_cxcli.object_storage_errors import ObjectStorageError

    files = {
        "/etc/nebius-wireguard/access.json": '{"generation":"fixture","ssh_user":"ubuntu"}',
        "/usr/local/bin/setup-wireguard.sh": "cat > /etc/nebius-wireguard/config.json <<'EOF'\n{}\nEOF",
    }
    body = yaml.safe_dump({"write_files": [{"path": k, "content": v} for k, v in files.items()]})
    objects, writes = {}, []

    def request(operation, key, **kwargs):
        if operation == "put_object":
            assert kwargs["create_only"]
            objects[key] = kwargs["body"]
            writes.append(key)
            if lost_ack:
                raise ObjectStorageError("request deadline exceeded")
        return NS(body=objects.get(key, b""))

    journal = NS(
        key="owned.json",
        value={"bootstrap": {"generation": "fixture"}, "ssh_user": "ubuntu"},
        transport=NS(request=request),
        assert_held=lambda: None,
        save=lambda: None,
    )
    owner = NS(
        rows=[{"id": "wireguard-gw", "instance_id": "vpn", "inputs": {}}],
        modules={"vpn": "vpn"},
        journals={"vpn": journal},
        env={},
        executor=NS(options=NS(dry_run=dry_run)),
        apply_runtime_inputs=lambda executor: None,
    )
    plan = {
        "planned_values": {
            "root_module": {
                "resources": [
                    {
                        "address": "module.vpn.module.vm.instance",
                        "type": "nebius_compute_v1_instance",
                        "values": {"cloud_init_user_data": body},
                    }
                ]
            }
        }
    }
    assert bootstrap.freeze_plan_bootstraps(owner, plan) is (not dry_run)
    assert bootstrap.freeze_plan_bootstraps(owner, plan) is False
    assert len(writes) == (0 if dry_run else 1)
    if not dry_run:
        assert owner.env["TF_VAR_vpn_bootstrap_user_data"] == body
        bootstrap.prepare_bootstraps(owner)
        assert len(writes) == 1
