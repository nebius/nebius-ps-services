from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from typer.testing import CliRunner

from nebius_cxcli import tailscale_api, tailscale_config, tailscale_deploy, tailscale_policy
from nebius_cxcli.tailscale_state import Journal
from nebius_cxcli.tailscale_verify import selected_router


def row():
    value = {
        "id": "tailscale-gw",
        "instance_id": "ts",
        "enabled": True,
        "inputs": {
            "replicas": 2,
            "name": "router",
            "parent_id": "project-test",
            "network_id": "network-test",
            "subnet_id": "subnet-test",
            "platform": "cpu-d3",
            "preset": "4vcpu-16gb",
            "ssh_user_name": "ubuntu",
            "ssh_public_key": "ssh-ed25519 placeholder",
            "management_cidrs": ["192.0.2.1/32"],
        },
        "tailscale": {
            "stage": "active",
            "onboarding": {"owner": "team", "purpose": "private access"},
            "policy": {
                "rules": [
                    {
                        "sources": ["group:users"],
                        "destinations": ["10.20.0.0/24"],
                        "protocol": "tcp",
                        "ports": ["443"],
                    }
                ]
            },
            "tailnet": "example.ts.net",
            "tags": ["tag:router"],
            "routes": ["10.20.0.0/24"],
            "auth": {
                "mode": "oauth",
                "client_id": "test",
                "client_secret_ref": "env:TEST_TAILSCALE_SECRET",
            },
        },
    }

    accept(value)
    return value


def accept(value):
    from nebius_cxcli.tailscale_onboarding import fingerprint

    value["tailscale"].setdefault("onboarding", {})["accepted"] = {
        "fingerprint": fingerprint(value),
        "reference": "test-approval",
    }


def healthy(member="a"):
    intent = tailscale_config.desired(row(), member)
    return {
        "online": True,
        "forwarding": True,
        "tailnet": intent["tailnet"],
        "node_id": "node-" + member,
        "tags": intent["tags"],
        "routes": intent["routes"],
        "version": intent["version"],
        "snat": True,
        "stateful_filtering": True,
        "netfilter": True,
        "accept_routes": False,
        "accept_dns": False,
        "tailscale_ssh": False,
        "approved_routes": intent["routes"],
        "key_expiry": None,
    }


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r["tailscale"].update(routes=["0.0.0.0/0"]),
        lambda r: r["tailscale"].update(routes=["10.0.0.1/24"]),
        lambda r: r["tailscale"].update(auth={"token": "inline"}),
        lambda r: r["tailscale"].update(tags=["tag:a", "tag:a"]),
        lambda r: r["inputs"].update(replicas=True),
        lambda r: r["tailscale"].update(verification={"required": True, "targets": []}),
        lambda r: r["tailscale"].update(
            verification={"targets": [{"url": "https://user:password@example.invalid"}]}
        ),
    ],
)
def test_rejects_unsafe_intent(change):
    value = row()
    change(value)
    with pytest.raises(ValueError):
        tailscale_config.validate(value)


def test_admin_key_mode_and_single_member_are_supported():
    value = row()
    value["inputs"]["replicas"] = 1
    value["tailscale"]["auth"] = {
        "mode": "auth-key",
        "member_key_refs": {"a": "file:protected/key"},
        "member_key_ids": {"a": "admin-key-a"},
    }
    assert tailscale_config.validate(value)
    assert tailscale_config.members(value) == ("a",)


def test_auth_check_distinguishes_readable_keys_from_administrator_capabilities(
    tmp_path, monkeypatch
):
    from nebius_cxcli import tailscale_cli

    value = row()
    value["tailscale"]["auth"] = {
        "mode": "auth-key",
        "member_key_refs": {"a": "env:TEST_KEY_A", "b": "env:TEST_KEY_B"},
        "member_key_ids": {"a": "admin-key-a", "b": "admin-key-b"},
    }
    monkeypatch.setattr(
        tailscale_cli, "load", lambda *_: (None, None, NS(project_dir=tmp_path), value)
    )
    monkeypatch.setattr(tailscale_config, "secret", lambda *_args, **_kwargs: "tskey-auth-test")
    emitted = []
    monkeypatch.setattr(tailscale_cli, "emit", lambda result, _json: emitted.append(result))
    tailscale_cli.auth_check(tmp_path / "config.yaml", json_output=True)
    assert emitted[0]["status"] == "PASS"
    for message in emitted[0]["checks"].values():
        assert "administrator" in message
        assert "one-time use checked" not in message


def test_source_policy_helper_reports_an_incompatible_go_toolchain(tmp_path, monkeypatch):
    monkeypatch.setattr(tailscale_policy, "_cache_root", lambda: tmp_path)
    monkeypatch.setattr(tailscale_policy.shutil, "which", lambda _: "/test/go")
    monkeypatch.setattr(
        tailscale_policy.subprocess,
        "run",
        lambda *_args, **_kwargs: NS(returncode=0, stdout="go version go1.25.0 linux/amd64\n"),
    )
    with pytest.raises(RuntimeError, match="Go 1.26 or newer"):
        tailscale_policy.helper()


@pytest.mark.parametrize("problem", ["mode", "symlink", "hardlink"])
def test_secret_file_protection(tmp_path, problem):
    path = tmp_path / "credential"
    path.write_text("disposable-test-value")
    path.chmod(0o600)
    if problem == "mode":
        path.chmod(0o644)
    elif problem == "symlink":
        (tmp_path / "link").symlink_to(path)
        path = tmp_path / "link"
    else:
        import os

        os.link(path, tmp_path / "link")
    with pytest.raises((RuntimeError, OSError)):
        tailscale_config.secret("file:" + str(path), base=tmp_path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("forwarding", False),
        ("accept_dns", True),
        ("accept_routes", True),
        ("tailscale_ssh", True),
        ("stateful_filtering", False),
        ("netfilter", False),
        ("version", "0.0.0"),
        ("expired", True),
    ],
)
def test_guest_readiness_cannot_ignore_unsafe_preferences(field, value):
    report = healthy()
    report[field] = value
    assert not tailscale_deploy.guest_ready(report, tailscale_config.desired(row(), "a"))


def test_admin_assisted_readiness_requires_approved_routes_and_no_expiry():
    report, intent = healthy(), tailscale_config.desired(row(), "a")
    assert tailscale_deploy.readiness(None, report, intent)
    report["approved_routes"] = []
    assert not tailscale_deploy.readiness(None, report, intent)
    report["approved_routes"] = intent["routes"]
    report["key_expiry"] = "2030-01-01T00:00:00Z"
    assert not tailscale_deploy.readiness(None, report, intent)


def test_peer_gate_checks_last_serving_intent_during_route_change():
    value = row()
    old = tailscale_config.desired(value, "b")
    value["tailscale"]["routes"] = ["10.30.0.0/24"]
    record = {"members": {"b": {"node_id": "node-b", "intent": old}}}
    guests = {"a": NS(), "b": NS(call=lambda _: healthy("b"))}
    owner = tailscale_deploy.TailscaleDeployment(NS(config={}))
    owner.assert_peer(value, "a", guests, record, None)
    guests["b"] = NS(call=lambda _: {**healthy("b"), "online": False})
    with pytest.raises(RuntimeError, match="peer"):
        owner.assert_peer(value, "a", guests, record, None)


def test_replacement_plan_rejects_pair_or_config_removal():
    def change(member, actions):
        return {
            "type": "nebius_compute_v1_instance",
            "address": f'module.ts.module.member["{member}"].instance',
            "change": {"actions": actions},
        }

    tailscale_deploy.check_plan({"resource_changes": [change("a", ["delete", "create"])]}, {"ts"})
    with pytest.raises(RuntimeError, match="multiple"):
        tailscale_deploy.check_plan(
            {"resource_changes": [change(m, ["delete", "create"]) for m in "ab"]}, {"ts"}
        )
    with pytest.raises(RuntimeError, match="destroy"):
        tailscale_deploy.check_plan({"resource_changes": [change("a", ["delete"])]}, {"ts"})


def test_plan_allows_retiring_a_replaced_management_cidr_rule():
    tailscale_deploy.check_plan(
        {
            "resource_changes": [
                {
                    "type": "nebius_vpc_v1_security_rule",
                    "address": 'module.ts.nebius_vpc_v1_security_rule.ssh["192.0.2.1/32"]',
                    "change": {"actions": ["delete"]},
                }
            ]
        },
        {"ts"},
    )


@pytest.mark.parametrize("previous_attempt", [False, True])
def test_replacement_plan_rechecks_peer_after_a_previous_attempt(monkeypatch, previous_attempt):
    from contextlib import nullcontext

    own = {"node_id": "node-a", "instance_id": "vm-a"}
    if previous_attempt:
        own["replacement"] = {"node_id": "node-a", "instance_id": "vm-a"}
    record = {"members": {"a": own, "b": {"node_id": "node-b", "instance_id": "vm-b"}}}
    journal = NS(value={"gateways": {"ts": record}}, save=lambda: None)
    owner = tailscale_deploy.TailscaleDeployment(
        NS(config={"infra": {"components": [row()]}}, manifest={}, options=NS(dry_run=False))
    )
    owner.open = lambda: journal
    owner.api = lambda _: NS()
    owner.raw_outputs = lambda: {}
    owner.guests = lambda *_: {}
    monkeypatch.setattr(tailscale_deploy, "asset", lambda *_: {"module_name": "ts"})
    monkeypatch.setattr(tailscale_deploy, "ComputeHostIdentityClient", lambda *_: nullcontext(NS()))

    def unavailable_peer(*_args):
        raise RuntimeError("peer is not ready")

    owner.assert_peer = unavailable_peer
    with pytest.raises(RuntimeError, match="peer is not ready"):
        owner.check_plan(
            {
                "resource_changes": [
                    {
                        "type": "nebius_compute_v1_instance",
                        "address": 'module.ts.module.member["a"].nebius_compute_v1_instance.vm',
                        "change": {"actions": ["delete", "create"], "before": {}, "after": {}},
                    }
                ]
            }
        )


def test_longest_prefix_unowned_route_cannot_prove_gateway():
    status = {
        "Peer": {
            "one": {"ID": "owned", "PrimaryRoutes": ["10.0.0.0/8"]},
            "two": {"ID": "other", "PrimaryRoutes": ["10.20.0.0/24"]},
        }
    }
    with pytest.raises(RuntimeError, match="unowned"):
        selected_router(status, "10.20.0.3", {"owned"})
    assert selected_router(status, "10.30.0.3", {"owned"}) == "owned"
    status["Peer"]["one"]["PrimaryRoutes"] = []
    with pytest.raises(RuntimeError, match="no selected"):
        selected_router(status, "10.30.0.3", {"owned"})


def test_api_route_approval_does_not_erase_unowned_prefixes(tmp_path, monkeypatch):
    api = tailscale_api.TailscaleAPI(row()["tailscale"], base_dir=tmp_path)
    monkeypatch.setattr(
        api,
        "routes",
        lambda _: {"advertisedRoutes": ["10.20.0.0/24"], "enabledRoutes": ["10.0.0.0/8"]},
    )
    monkeypatch.setattr(api, "request", lambda *a, **k: pytest.fail("unowned write"))
    with pytest.raises(RuntimeError, match="differ"):
        api.approve("node-a", ["10.20.0.0/24"])


def test_policy_conflict_has_no_write(tmp_path):
    cfg = row()["tailscale"]
    plan = {
        "schema": 1,
        "tailnet": cfg["tailnet"],
        "component": "ts",
        "intent_sha256": tailscale_config.digest(cfg),
        "etag": "old",
        "baseline_sha256": "old",
        "document": "{}",
        "patch": [],
    }
    plan["sha256"] = tailscale_config.digest(plan)
    calls = []
    api = NS(tailnet="/tailnet/example", request=lambda *a, **k: calls.append(a) or ("{}", "new"))
    with pytest.raises(RuntimeError, match="changed since"):
        tailscale_policy.apply_plan(api, cfg, "ts", plan)
    assert [c[0] for c in calls] == ["GET"]


class Transport:
    def __init__(self):
        self.objects = {}
        self.counter = 0
        self.lose_write = False

    def request(self, operation, key, *, body=None, create_only=False, etag=None, **kwargs):
        from nebius_cxcli.object_storage_errors import ObjectStorageError

        current = self.objects.get(key)
        if operation == "get_object":
            if current is None:
                raise ObjectStorageError("NoSuchKey")
            return current
        if (create_only and current) or (etag and (not current or current.etag != etag)):
            raise ObjectStorageError("PreconditionFailed")
        if operation == "delete_object":
            self.objects.pop(key, None)
            return NS(etag="")
        self.counter += 1
        result = NS(body=body, etag=str(self.counter))
        self.objects[key] = result
        if self.lose_write:
            self.lose_write = False
            raise ObjectStorageError("transport read timeout")
        return result


@pytest.mark.parametrize("schema", [1, 2])
def test_older_journal_schema_is_rejected_without_recreation(schema):
    transport = Transport()
    original = json.dumps({"schema": schema, "gateways": {}}).encode()
    transport.objects["state.tailscale.json"] = NS(body=original, etag="v1")
    with pytest.raises(RuntimeError, match="Unsupported Tailscale ownership journal"):
        Journal(transport, "state", lambda: None)
    assert transport.objects["state.tailscale.json"].body == original


def test_journal_lost_ack_reconciles_and_lost_lock_fences():
    transport = Transport()
    journal = Journal(transport, "state", lambda: None)
    transport.lose_write = True
    journal.acquire()
    journal.value["gateways"]["ts"] = {
        "tailnet": "example",
        "members": {},
        "lifecycle": "provisioning",
        "activation_started": False,
    }
    transport.lose_write = True
    journal.save()
    assert Journal(transport, "state", lambda: None).value == journal.value
    transport.objects[journal.lock_key] = NS(body=b'{"owner":"other"}', etag="new")
    with pytest.raises(RuntimeError, match="authority"):
        journal.save()


@pytest.fixture
def guest(tmp_path, monkeypatch):
    path = (
        Path(__file__).resolve().parents[3]
        / "platform-infra/modules/tailscale-gw/tailscale_guest.py"
    )
    spec = importlib.util.spec_from_file_location("tailscale_guest_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "STATE", tmp_path)
    return module


def test_guest_reuses_identity_without_authentication(guest, monkeypatch):
    observation = {**healthy(), "backend": "Running"}
    monkeypatch.setattr(guest, "observation", lambda: copy.deepcopy(observation))
    calls = []
    guest.write(
        guest.STATE / "owner.json",
        {"tailnet": observation["tailnet"], "node_id": observation["node_id"]},
    )
    monkeypatch.setattr(guest, "run", lambda args, **kwargs: calls.append(args) or "")
    assert guest.reconcile({"desired": tailscale_config.desired(row(), "a")})["status"] == "READY"
    assert all("up" not in call for call in calls)
    assert json.loads((guest.STATE / "owner.json").read_text())["node_id"] == "node-a"


def test_wrong_tailnet_never_advertises(guest, monkeypatch):
    monkeypatch.setattr(
        guest, "observation", lambda: {**healthy(), "tailnet": "wrong", "backend": "Running"}
    )
    monkeypatch.setattr(guest, "run", lambda *a, **kw: pytest.fail("unsafe command"))
    with pytest.raises(RuntimeError, match="Wrong tailnet"):
        guest.reconcile({"desired": tailscale_config.desired(row(), "a")})


def test_cli_contains_operations_but_no_second_deployment_entrypoint():
    from nebius_cxcli.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["tailscale", "--help"])
    assert result.exit_code == 0
    for command in ("status", "doctor", "verify", "recover", "auth", "policy", "routes"):
        assert command in result.stdout
    assert runner.invoke(app, ["tailscale", "install", "--help"]).exit_code != 0


def test_route_coverage_requires_whole_prefix_in_selected_vpc():
    from nebius_cxcli.tailscale_network import contained

    assert contained(["10.20.0.0/24"], ["10.20.0.0/25", "10.20.0.128/25"])
    assert not contained(["10.20.0.0/24"], ["10.20.0.0/25"])
    assert not contained(["10.30.0.0/24"], ["10.20.0.0/24"])


@pytest.mark.parametrize("linked_secret", [False, True])
def test_native_config_render_keeps_tailnet_control_outside_terraform(
    tmp_path, linked_secret, monkeypatch
):
    from dataclasses import asdict

    import yaml

    from nebius_cxcli.component_sources import SourceProfile
    from nebius_cxcli.config_loader import load_config
    from nebius_cxcli.generated_manifest import build_generated_manifest
    from nebius_cxcli.infra_render import rendered_module_sources
    from nebius_cxcli.paths import resolve_project_paths
    from nebius_cxcli.render import render_project
    from test_render import _VALID_ED25519_PUBLIC_KEY, _project_config_path, _starter_payload

    path = _project_config_path(tmp_path)
    path.parent.mkdir(parents=True)
    payload = _starter_payload(selected_infra={"tailscale-gw"}, selected_apps=set())
    selected = row()
    selected["inputs"].update(parent_id="project-456", ssh_public_key=_VALID_ED25519_PUBLIC_KEY)
    payload["infra"]["components"] = [selected]
    if linked_secret:
        from nebius_cxcli.tailscale_dependencies import materialize

        selected["tailscale"]["stage"] = "infrastructure"
        selected["tailscale"]["auth"] = {
            "mode": "oauth",
            "secretstash": {
                "project_id": "project-456",
                "component_instance": "ts-oauth",
                "secret_name": "oauth",
            },
        }
        materialize(payload)
    path.write_text(yaml.safe_dump(payload))
    config = load_config(path, persist_normalized=False)
    assert tailscale_config.rows(config)[0]["tailscale"] == selected["tailscale"]
    paths = resolve_project_paths(path)
    render_project(config, paths, source_profile=SourceProfile.LOCAL)
    manifest = build_generated_manifest(
        config=config,
        paths=paths,
        targets=[],
        required_component_outputs=[],
        module_sources=[
            asdict(item)
            for item in rendered_module_sources(config, source_profile=SourceProfile.LOCAL)
        ],
    )
    assert (
        manifest["runtime_config"]["infra"]["components"][0]["tailscale"] == selected["tailscale"]
    )
    assert "ts" in manifest["render"]["tailscale_guest"]
    tfvars = (paths.infra_dir / "terraform.auto.tfvars.json").read_text()
    assert "TEST_TAILSCALE_SECRET" not in tfvars
    assert "example.ts.net" not in tfvars
    assert "tailscale_version" in tfvars
    variables = (paths.infra_dir / "variables.tf").read_text()
    assert "ssh_host_keys" in variables and "bootstrap_user_data" in variables
    assert "sensitive = true" in variables or "sensitive   = true" in variables
    if linked_secret:
        assert "create_initial_versions" in tfvars and "payload_values" not in tfvars
        assert not any(
            c.get("enabled") for c in payload["apps"]["charts"] if c["id"] == "external-secrets"
        )
        from nebius_cxcli.cli import app

        monkeypatch.setattr("nebius.sdk.SDK", lambda *a, **kw: pytest.fail("cloud access"))
        monkeypatch.setattr(
            tailscale_api.TailscaleAPI, "request", lambda *a, **kw: pytest.fail("tailnet access")
        )
        result = CliRunner().invoke(
            app, ["tailscale", "onboarding", "request", str(path), "--json"]
        )
        assert result.exit_code == 0, result.output
        request = json.loads(result.output)
        assert request["credential_delivery"]["component_instance"] == "ts-oauth"
        assert request["infrastructure"]["status"] == "NOT_VERIFIED"


def test_deploy_recovers_lost_enrollment_response_and_second_deploy_is_identity_stable(
    tmp_path, monkeypatch
):
    from contextlib import nullcontext

    from nebius_cxcli import tailscale_network

    value = row()
    cloud = {
        m: {
            "instance_id": "vm-" + m,
            "boot_disk_id": "disk-" + m,
            "public_allocation_id": "ip-" + m,
            "network_id": "network-test",
            "subnet_id": "subnet-test",
            "public_ip": "192.0.2.1",
            "private_ip": "10.20.0.1",
        }
        for m in "ab"
    }
    record = {
        "tailnet": value["tailscale"]["tailnet"],
        "members": {
            m: {"host_public_key": "ssh-ed25519 fixture", "bootstrap_sha256": "fixture"}
            for m in "ab"
        },
    }
    journal = NS(value={"gateways": {"ts": record}}, assert_held=lambda: None, save=lambda: None)
    paths = NS(project_dir=tmp_path, reports_dir=tmp_path / "reports")
    executor = NS(
        config={"infra": {"components": [value]}},
        report_paths=paths,
        paths=paths,
        options=NS(dry_run=False),
        manifest={},
    )
    owner = tailscale_deploy.TailscaleDeployment(executor)
    owner.open = lambda: journal
    owner.raw_outputs = lambda: {}
    monkeypatch.setattr(tailscale_deploy, "outputs", lambda *_: cloud)
    monkeypatch.setattr(tailscale_deploy, "ComputeHostIdentityClient", lambda *_: nullcontext(NS()))
    monkeypatch.setattr(tailscale_deploy, "asset", lambda *_: {})
    monkeypatch.setattr(tailscale_network, "validate_live", lambda *_: None)
    key_calls = []
    deleted_nodes = []
    api = NS(
        credential_binding=lambda: {
            "source": "reference",
            "client_id": "test",
            "client_secret_ref": "env:TEST_TAILSCALE_SECRET",
        },
        bind_credential=lambda pin: None,
        revoke_key=lambda key: None,
        key=lambda m: key_calls.append(m) or ("key-" + m, "tskey-auth-test"),
        device=lambda node: {
            "nodeId": node,
            "tags": value["tailscale"]["tags"],
            "authorized": True,
            "keyExpiryDisabled": True,
        },
        routes=lambda node: {"enabledRoutes": value["tailscale"]["routes"]},
        delete_owned=lambda node, _tags: deleted_nodes.append(node),
    )
    owner.api = lambda _: api

    class Guest:
        def __init__(self, member):
            self.member, self.state = (
                member,
                {"node_id": "", "forwarding": True, "version": tailscale_config.VERSION},
            )
            self.lose = member == "a"
            self.receipt = None

        def install(self, _):
            pass

        def call(self, operation, **kwargs):
            if operation == "enrollment-status":
                return {"receipt": copy.deepcopy(self.receipt), "guest": copy.deepcopy(self.state)}
            if operation == "reconcile":
                if "enrollment" in kwargs:
                    self.receipt = {
                        "contract": kwargs["enrollment"],
                        "phase": "enrolled",
                        "fenced": False,
                        "node_id": "node-" + self.member,
                    }
                self.state = healthy(self.member)
                if self.lose:
                    self.lose = False
                    raise RuntimeError("response lost")
            return copy.deepcopy(self.state)

    guests = {m: Guest(m) for m in "ab"}
    owner.guests = lambda *_: guests
    owner.rows[0]["tailscale"]["stage"] = "infrastructure"
    owner.api = lambda _: pytest.fail("infrastructure contacted Tailscale")
    owner.complete()
    assert record["lifecycle"] == "infrastructure-ready"
    assert not key_calls
    owner.rows[0]["tailscale"]["stage"] = "active"
    accept(owner.rows[0])
    owner.api = lambda _: api
    with pytest.raises(RuntimeError, match="response lost"):
        owner.complete()
    assert len(key_calls) == 1
    owner.complete()
    snapshot = copy.deepcopy(record)
    owner.complete()
    assert record == snapshot
    assert len(key_calls) == 2
    assert len(set(key_calls)) == 2
    assert {m: r["node_id"] for m, r in record["members"].items()} == {"a": "node-a", "b": "node-b"}
    assert all(r["phase"] == "READY" for r in record["members"].values())

    # A VM-only replacement retaining the persistent disk keeps its original node.
    member = record["members"]["a"]
    previous = copy.deepcopy(member["enrollment"])
    member["replacement"] = {"node_id": "node-a", "instance_id": "vm-a"}
    cloud["a"]["instance_id"] = "vm-a-new"
    owner.complete()
    owner.complete()
    assert member["instance_id"] == "vm-a-new"
    assert (member["node_id"], member["boot_disk_id"], member["public_allocation_id"]) == (
        "node-a",
        "disk-a",
        "ip-a",
    )
    assert member["prior_enrollments"] == [previous]
    assert "replacement" not in member
    assert len(key_calls) == 2 and not deleted_nodes

    # An optional client probe must leave explicit pending evidence without
    # turning an otherwise ready deployment into a failure.
    from http.client import HTTPException

    from nebius_cxcli import tailscale_verify

    owner.rows[0]["tailscale"]["verification"] = {
        "targets": [{"url": "https://private.example/readyz"}]
    }

    def malformed_http(*_args, **_kwargs):
        raise HTTPException("Malformed HTTP response")

    monkeypatch.setattr(tailscale_verify, "verify", malformed_http)
    owner.complete()
    report = json.loads((paths.reports_dir / "tailscale-report.json").read_text())
    assert report["client"][0]["status"] == "PENDING"
    owner.rows[0]["tailscale"]["verification"]["required"] = True
    with pytest.raises(RuntimeError, match="Required Tailscale client verification"):
        owner.complete()


def test_wizard_declares_required_tailnet_fields_and_valid_auth_choices():
    from nebius_cxcli.components import component_entries

    entry = next(e for e in component_entries("infra") if e.id == "tailscale-gw")
    for field in ("tailscale.tailnet", "tailscale.tags", "tailscale.routes"):
        assert entry.wizard_fields[field]["required"]
    assert entry.wizard_fields["tailscale.stage"]["default"] == "infrastructure"
    assert "tailscale.auth.client_secret_ref" not in entry.wizard_fields
    assert "tailscale.auth" in entry.wizard_fields


def test_os_route_verification_rejects_overlapping_vpn(monkeypatch):
    from nebius_cxcli import tailscale_verify

    monkeypatch.setattr(tailscale_verify.platform, "system", lambda: "Darwin")

    def run(args, **kwargs):
        return NS(
            returncode=0,
            stdout="interface: " + ("utun9" if args[-1].startswith("100.") else "utun5") + "\n",
        )

    monkeypatch.setattr(tailscale_verify.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="outside"):
        tailscale_verify.verify_os_route(
            "10.20.0.1", {"Peer": {"p": {"ID": "n", "TailscaleIPs": ["100.64.0.1"]}}}, "n"
        )


def test_repeat_deploy_credits_only_exact_retained_tailscale_compute():
    from contextlib import nullcontext

    from nebius_cxcli.tailscale_quota import retained_compute

    value = row()
    config = {
        "client_info": {"nebius": {"region_id": "eu-north1", "project_id": "project-test"}},
        "infra": {"components": [value]},
    }
    sources = [{"component_id": "tailscale-gw", "instance_id": "ts", "module_name": "ts"}]
    before = {
        "id": "vm-a",
        "parent_id": "project-test",
        "resources": {"platform": "cpu-d3", "preset": "4vcpu-16gb"},
    }
    resource = {
        "address": "module.ts.module.member.instance",
        "mode": "managed",
        "type": "nebius_compute_v1_instance",
        "change": {
            "actions": ["no-op"],
            "before": before,
            "after": copy.deepcopy(before),
            "after_unknown": {},
        },
    }

    def factory(_):
        return nullcontext(NS(preset_resources=lambda *_: {"gpu_count": 0, "vcpu_count": 4}))

    credits = retained_compute(
        config, sources, {"resource_changes": [resource]}, sdk_factory=factory
    )
    assert {c.quota_name: c.required for c in credits} == {
        "compute.instance.count": 1,
        "compute.instance.non-gpu.vcpu": 4,
    }
    resource["change"]["actions"] = ["delete", "create"]
    assert not retained_compute(
        config, sources, {"resource_changes": [resource]}, sdk_factory=factory
    )
    resource["change"]["actions"] = ["no-op"]
    resource["change"]["after"]["id"] = "different"
    assert not retained_compute(
        config, sources, {"resource_changes": [resource]}, sdk_factory=factory
    )


def test_in_place_pair_resize_is_also_staged():
    resources = [
        {
            "type": "nebius_compute_v1_instance",
            "address": f'module.ts.module.member["{m}"].vm',
            "change": {
                "actions": ["update"],
                "before": {"resources": {"preset": "small"}},
                "after": {"resources": {"preset": "large"}},
            },
        }
        for m in "ab"
    ]
    with pytest.raises(RuntimeError, match="disrupting multiple"):
        tailscale_deploy.check_plan({"resource_changes": resources}, {"ts"})
    tailscale_deploy.check_plan({"resource_changes": resources[:1]}, {"ts"})


@pytest.mark.parametrize("custody", ["reference", "new"])
def test_native_wizard_collects_tailnet_intent(tmp_path, monkeypatch, custody):
    import yaml

    from nebius_cxcli import cli
    from nebius_cxcli.components import component_entries
    from nebius_cxcli.provider_options import ProviderOptionLookup
    from test_render import _starter_payload

    payload = _starter_payload(selected_infra={"tailscale-gw"}, selected_apps=set())
    value = row()
    value.pop("tailscale")
    payload["infra"]["components"] = [value]
    entry = next(e for e in component_entries("infra") if e.id == "tailscale-gw")
    prompts = []

    def prompt(label, current=None, **kwargs):
        prompts.append(label)
        for suffix, answer in {
            "tailscale.tailnet": "example.ts.net",
            "tailscale.tags": ["tag:router"],
            "tailscale.routes": ["10.20.0.0/24"],
            "tailscale.auth.mode": "oauth",
            "tailscale.auth.client_id": "test",
            "tailscale.auth.client_secret_ref": "env:TEST_TAILSCALE_SECRET",
            "tailscale.approval": "external",
        }.items():
            if label.endswith(suffix):
                return answer, False
        return current, False

    monkeypatch.setattr(
        cli, "_wizard_continue_phase", lambda *a, **k: cli._WizardPhaseDecision(proceed=True)
    )
    monkeypatch.setattr(cli, "_prompt_scalar_override", prompt)
    monkeypatch.setattr(cli, "_prompt_choice_override", lambda **kw: (custody, False))
    from nebius_cxcli import tailscale_wizard

    monkeypatch.setattr(
        tailscale_wizard, "prompt_rules", lambda *a: (row()["tailscale"]["policy"]["rules"], False)
    )

    def choices(**kwargs):
        from nebius_cxcli.provider_options import OptionChoice

        key = kwargs["full_path_label"].rsplit(".", 1)[-1]
        value = row()["inputs"].get(key)
        return (
            [OptionChoice(value, value)]
            if key in {"network_id", "subnet_id", "platform", "preset"}
            else []
        )

    monkeypatch.setattr(cli, "_resolve_dynamic_field_choices", choices)
    text, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload),
        selected_infra={"ts"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=ProviderOptionLookup(),
    )
    assert completed
    selected = yaml.safe_load(text)["infra"]["components"][0]
    assert selected["tailscale"]["tailnet"] == "example.ts.net"
    assert selected["tailscale"]["routes"] == ["10.20.0.0/24"]
    assert selected["tailscale"]["stage"] == "infrastructure"
    if custody == "new":
        from nebius_cxcli.tailscale_dependencies import dependencies

        assert (
            dependencies(yaml.safe_load(text))["ts"]["inputs"]["create_initial_versions"] is False
        )
    assert not any(p.endswith("tailscale.auth.client_secret_ref") for p in prompts)


def test_policy_http_success_can_still_mean_validation_failure():
    api = NS(
        tailnet="/tailnet/test",
        request=lambda *a, **k: ('{"message":"policy validation failed"}', ""),
    )
    with pytest.raises(RuntimeError, match="rejected"):
        tailscale_policy.validate_policy(api, "{}")


@pytest.mark.parametrize("switches", [True, False])
def test_failover_requires_peer_selection_and_always_restores(tmp_path, monkeypatch, switches):
    from nebius_cxcli import tailscale_verify

    cfg = row()["tailscale"]
    cfg["verification"] = {"targets": [{"url": "https://private.example/readyz"}]}
    state = {"faulted": False, "restored": False, "clock": 0}

    def clock():
        state["clock"] += 20
        return state["clock"]

    def status():
        router = "b" if state["faulted"] and switches else "a"
        return {
            "CurrentTailnet": {"Name": cfg["tailnet"]},
            "Peer": {router: {"ID": "node-" + router, "PrimaryRoutes": cfg["routes"]}},
        }

    def call(operation, **kwargs):
        if operation == "fault":
            assert kwargs["duration"] == 120
            state["faulted"] = True
        elif operation == "restore":
            state.update(faulted=False, restored=True)
        elif operation == "trial-status":
            return {"active": not state["faulted"]}
        elif operation == "status":
            return {"online": not state["faulted"]}
        return {}

    monkeypatch.setattr(tailscale_verify, "client_status", status)
    monkeypatch.setattr(tailscale_verify, "verify_os_route", lambda *_: "tailscale0")
    monkeypatch.setattr(tailscale_verify, "probe", lambda *_: {"tls": "verified", "bytes": 1024})
    monkeypatch.setattr(
        tailscale_verify.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(0, 0, 0, "", ("10.20.0.1", 443))],
    )
    monkeypatch.setattr(tailscale_verify.time, "monotonic", clock)
    monkeypatch.setattr(tailscale_verify.time, "sleep", lambda _: None)
    members = {m: {"node_id": "node-" + m, "phase": "READY"} for m in "ab"}
    guests = {m: NS(call=call) for m in "ab"}
    if switches:
        result = tailscale_verify.verify(cfg, members, base=tmp_path, guests=guests, failover=True)
        assert result["failover"]["status"] == "PASS"
        assert result["failover"]["restored"]
        assert result["failover"]["targets"][0]["router"] == "node-b"
    else:
        with pytest.raises(RuntimeError, match="did not converge"):
            tailscale_verify.verify(cfg, members, base=tmp_path, guests=guests, failover=True)
    assert state["restored"] and not state["faulted"]


@pytest.mark.parametrize("preview", [False, True])
def test_prepare_freezes_host_inputs_without_enrollment_and_preview_cannot_write(
    tmp_path, monkeypatch, preview
):
    from contextlib import nullcontext

    from nebius_cxcli import nat_gateway_sdk

    value = row()
    value["tailscale"]["stage"] = "infrastructure"
    calls = []
    journal = NS(value={"schema": 3, "gateways": {}}, save=lambda: calls.append("save"))
    paths = NS(project_dir=tmp_path, infra_dir=tmp_path)
    cli = NS(terraform_output_json=lambda *a, **k: {}, terraform_show_json=lambda *a, **k: {})
    executor = NS(
        config={"infra": {"components": [value]}},
        paths=paths,
        report_paths=paths,
        runtime_env={},
        cli=cli,
        options=NS(dry_run=preview),
        manifest={},
    )
    owner = tailscale_deploy.TailscaleDeployment(executor)
    owner.open = lambda: journal
    owner.api = lambda _: NS(
        tailnet="/tailnet/example", request=lambda *a: calls.append("check-api")
    )
    monkeypatch.setattr(tailscale_deploy, "asset", lambda *a: {"module_name": "ts"})
    monkeypatch.setattr(
        nat_gateway_sdk,
        "NatGatewaySDK",
        lambda *a: nullcontext(NS(preset_resources=lambda *a: {"gpu_count": 0, "vcpu_count": 4})),
    )
    monkeypatch.setattr(tailscale_deploy, "ComputeHostIdentityClient", lambda *a: nullcontext(NS()))
    monkeypatch.setattr(
        tailscale_deploy,
        "fresh_identity",
        lambda *a, **k: {
            "ed25519_public": "disposable-public-" + a[2],
            "ed25519_private": "disposable-fixture",
        },
    )
    owner.prepare()
    owner.prepare()
    assert set(json.loads(executor.runtime_env["TF_VAR_ts_ssh_host_keys"])) == {"a", "b"}
    assert calls == ([] if preview else ["save"])
