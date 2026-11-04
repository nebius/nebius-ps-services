"""Offline boundaries for two-stage onboarding and renewable credential custody."""

import copy
import json
from types import SimpleNamespace as NS

import pytest

from nebius_cxcli import secretstash
from nebius_cxcli import tailscale_dependencies as deps
from nebius_cxcli import tailscale_onboarding as onboarding
from nebius_cxcli.tailscale_config import validate
from test_tailscale import accept, row
from test_tailscale import guest as guest  # noqa: F401


def test_request_is_exact_pure_and_credential_independent(monkeypatch):
    monkeypatch.setattr(secretstash, "sdk_context", lambda *a, **k: pytest.fail("credential read"))
    value = row()
    request = onboarding.request(value)
    assert request["request"]["approve_tags"] == ["tag:router"]
    assert request["request"]["oauth_scopes"] == [
        "auth_keys",
        "devices:core",
        "devices:routes:read",
    ]
    assert request["request"]["access_grants"] == [
        {"src": ["group:users"], "dst": ["10.20.0.0/24"], "ip": ["tcp:443"]}
    ]
    assert "TEST_TAILSCALE_SECRET" not in json.dumps(request)
    value["tailscale"]["auth"] = {
        "mode": "oauth",
        "secretstash": {"project_id": "p", "secret_id": "secret", "version_id": "v2"},
    }
    assert onboarding.fingerprint(value) == request["intent_sha256"]
    value["tailscale"]["policy"]["rules"][0]["ports"] = ["22"]
    with pytest.raises(ValueError, match="stale"):
        onboarding.assert_accepted(value)
    accept(value)
    onboarding.assert_accepted(value)


def test_rules_preserve_separate_source_destination_ports():
    value = row()
    value["tailscale"]["policy"]["rules"].append(
        {
            "sources": ["group:ops"],
            "destinations": ["10.20.0.5/32"],
            "protocol": "tcp",
            "ports": ["22"],
        }
    )
    grants = onboarding.request(value)["request"]["access_grants"]
    assert len(grants) == 2
    assert grants[0]["ip"] == ["tcp:443"] and grants[1]["src"] == ["group:ops"]
    assert grants[1]["dst"] == ["10.20.0.5/32"]


def test_infrastructure_can_be_draft_but_active_cannot_skip_handoff():
    value = row()
    value["tailscale"] = {"stage": "infrastructure", "routes": ["10.20.0.0/24"]}
    validate(value)
    assert onboarding.request(value)["status"] == "DRAFT"
    value["tailscale"]["stage"] = "active"
    with pytest.raises(ValueError):
        validate(value)


def linked():
    value = row()
    value["tailscale"]["stage"] = "infrastructure"
    value["tailscale"]["auth"] = {
        "mode": "oauth",
        "secretstash": {
            "project_id": "project-test",
            "component_instance": "ts-oauth",
            "secret_name": "oauth",
        },
    }
    return {"infra": {"components": [value]}, "apps": {"components": []}}


def test_linked_container_is_metadata_only_without_kubernetes_or_eso():
    payload = linked()
    assert deps.materialize(payload) == ["ts-oauth"]
    assert deps.materialize(payload) == []
    assert deps.dependencies(payload)["ts"]["inputs"] == {
        "parent_id": "project-test",
        "create_initial_versions": False,
        "secrets": [{"name": "oauth"}],
    }
    assert payload["apps"]["components"] == []
    with pytest.raises(ValueError, match="Cannot remove"):
        deps.assert_removal_allowed(payload, {"ts-oauth"})
    assert deps.skipped_new_dependencies(payload, {"ts"}, {"ts-oauth"}) == {"ts-oauth"}
    assert deps.skipped_new_dependencies(payload, {"ts"}, set()) == set()
    payload["infra"]["components"][0]["tailscale"]["auth"] = {"mode": "oauth"}
    deps.prune_unreferenced(payload, {"ts-oauth"})
    assert len(payload["infra"]["components"]) == 1


def test_original_secret_version_is_used_even_after_primary_changes(tmp_path, monkeypatch):
    reads = []

    def read(ref):
        reads.append(copy.deepcopy(ref))
        version = ref.get("version_id", "primary-new")
        return version, {
            "client_id": "original" if version == "v1" else "replacement",
            "client_secret": "test-fixture",
        }

    monkeypatch.setattr(secretstash, "payload", read)
    auth = {"secretstash": {"project_id": "p", "secret_id": "s"}}
    pin = {
        "source": "secretstash",
        "project_id": "p",
        "secret_id": "s",
        "version_id": "v1",
        "client_id": "original",
    }
    assert secretstash.oauth_pair(auth, base=tmp_path, binding=pin)[0] == "original"
    assert reads[-1]["version_id"] == "v1"
    assert secretstash.oauth_pair(auth, base=tmp_path)[0] == "replacement"


def test_new_operation_selects_current_primary_after_original_recovery(tmp_path, monkeypatch):
    from nebius_cxcli import tailscale_api

    configured = {"source": "reference", "client_id": "new", "client_secret_ref": "env:NEW"}
    api = tailscale_api.TailscaleAPI(row()["tailscale"], base_dir=tmp_path)
    api.bind_credential({**configured, "client_id": "old"})
    api.token = "fixture"
    monkeypatch.setattr(tailscale_api, "oauth_pair", lambda *a, **k: ("new", "fixture", configured))
    assert api.credential_binding() == configured
    assert not api.token


def test_guest_contract_excludes_controller_credential_binding(enrollment_case):
    from nebius_cxcli.tailscale_enrollment import contract

    c = enrollment_case
    c.resume()
    guest = contract(c.own["enrollment"])
    assert guest["schema"] == 1
    assert "credential" not in guest and "key_retired" not in guest
    assert c.own["enrollment"]["key_retired"]


def test_workload_identity_preserves_rotating_projected_path(tmp_path, monkeypatch):
    from nebius.base.service_account.federated_credentials import FileFederatedCredentials

    from nebius_cxcli.workload_identity import workload_identity_sdk

    first, second = tmp_path / "first", tmp_path / "second"
    first.write_text("fixture-first")
    second.write_text("fixture-second")
    token = tmp_path / "token"
    token.symlink_to(first)
    reader = FileFederatedCredentials(str(token))
    captured = {}

    class Bearer:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def set_channel(self, sdk):
            captured["sdk"] = sdk

    monkeypatch.setattr("nebius.aio.token.federated_credentials.FederatedCredentialsBearer", Bearer)
    monkeypatch.setattr("nebius.sdk.SDK", lambda **kwargs: NS(**kwargs))
    sdk = workload_identity_sdk(service_account_id="sa", token_file=token, parent_id="p")
    assert captured["sdk"] is sdk and captured["federated_credentials"] == str(token)
    assert reader.credentials() == "fixture-first"
    token.unlink()
    token.symlink_to(second)
    assert reader.credentials() == "fixture-second"


def test_explicit_workload_identity_failure_never_uses_cli(tmp_path, monkeypatch):
    from nebius_cxcli.sdk_auth import init_nebius_sdk

    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("CLI fallback"))
    monkeypatch.setattr(
        "nebius_cxcli.workload_identity.workload_identity_sdk",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    for key in (
        "NEBIUS_AUTH_CREDENTIALS_FILE",
        "NEBIUS_IAM_TOKEN",
        "NEBIUS_PROFILE",
        "NEBIUS_SERVICE_ACCOUNT_ID",
        "CXCLI_NEBIUS_DELEGATE_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="unavailable"):
        init_nebius_sdk(
            parent_id="p",
            workload_identity={"service_account_id": "sa", "token_file": str(tmp_path / "token")},
        )


from test_tailscale_recovery import enrollment_case as enrollment_case  # noqa: E402, F401


def handoff_fixture(tmp_path, monkeypatch):
    from nebius_cxcli import tailscale_onboarding_cli as commands

    value = row()
    value["tailscale"]["stage"] = "infrastructure"
    value["tailscale"]["auth"] = {"mode": "oauth"}
    path = tmp_path / "config.yaml"
    path.write_text("initial")
    payload = {"infra": {"components": [copy.deepcopy(value)]}}
    writes = []
    cli = NS(
        _load_config_payload=lambda _: payload,
        _write_runtime_payload_config=lambda p, v, **kw: writes.append((copy.deepcopy(v), kw)),
    )
    paths = NS(config_path=path, project_dir=tmp_path, reports_dir=tmp_path / "reports")
    monkeypatch.setattr(commands, "load", lambda *a: (cli, payload, paths, value))
    monkeypatch.setattr(commands, "emit", lambda *a: None)
    return commands, value, path, writes


def test_noninteractive_handoff_saves_approved_reference_without_reading_it(tmp_path, monkeypatch):
    commands, value, path, writes = handoff_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "nebius_cxcli.tailscale_config.secret", lambda *a, **kw: pytest.fail("secret read")
    )
    commands.onboarding_configure.__wrapped__(
        path,
        interactive=False,
        approval_reference="approved-ticket",
        accepted_fingerprint=onboarding.fingerprint(value),
        client_id="client",
        client_secret_ref="env:ADMIN_DELIVERED",
    )
    saved = writes[0][0]["infra"]["components"][0]
    assert writes[0][1]["expected_bytes"] == b"initial"
    assert saved["tailscale"]["stage"] == "active"
    onboarding.assert_accepted(saved)


def test_noninteractive_handoff_json_is_one_document(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from nebius_cxcli.tailscale_cli_io import emit

    commands, value, path, writes = handoff_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(commands, "emit", emit)
    result = CliRunner().invoke(
        commands.app,
        [
            "configure",
            str(path),
            "--no-interactive",
            "--json",
            "--approval-reference",
            "ticket",
            "--accepted-fingerprint",
            onboarding.fingerprint(value),
            "--client-id",
            "client",
            "--client-secret-ref",
            "env:ADMIN_DELIVERED",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "CONFIGURED"
    assert len(writes) == 1


def test_wrong_handoff_fingerprint_writes_nothing(tmp_path, monkeypatch):
    commands, _value, path, writes = handoff_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="fingerprint"):
        commands.onboarding_configure.__wrapped__(
            path,
            interactive=False,
            approval_reference="ticket",
            accepted_fingerprint="0" * 64,
            client_id="client",
            client_secret_ref="env:ADMIN_DELIVERED",
        )
    assert not writes


@pytest.mark.parametrize(
    "option",
    [
        {"import_to_secretstash": True},
        {"secret_version": "version-a"},
        {"nebius_service_account_id": "sa-a"},
        {"import_operation_id": "operation-a"},
    ],
)
def test_secretstash_options_cannot_be_silently_ignored(tmp_path, monkeypatch, option):
    commands, value, path, writes = handoff_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="SecretStash"):
        commands.onboarding_configure.__wrapped__(
            path,
            interactive=False,
            approval_reference="ticket",
            accepted_fingerprint=onboarding.fingerprint(value),
            client_id="client",
            client_secret_ref="env:ADMIN_DELIVERED",
            **option,
        )
    assert not writes


def test_invalid_handoff_is_rejected_before_import(tmp_path, monkeypatch):
    commands, value, path, writes = handoff_fixture(tmp_path, monkeypatch)
    value["tailscale"]["approval"] = "unsupported"
    payload = {"infra": {"components": [copy.deepcopy(value)]}}
    cli, _, paths, _ = commands.load(path)
    cli._load_config_payload = lambda _: payload
    monkeypatch.setattr(
        "nebius_cxcli.tailscale_secret_import.import_pair",
        lambda *a, **kw: pytest.fail("import effect"),
    )
    with pytest.raises(ValueError, match="approval"):
        commands.onboarding_configure.__wrapped__(
            path,
            interactive=False,
            approval_reference="ticket",
            accepted_fingerprint=onboarding.fingerprint(value),
            secret_id="secret-a",
            import_to_secretstash=True,
            client_id="client",
            client_secret_ref="env:ADMIN_DELIVERED",
        )
    assert not writes


def test_credential_free_retirement_requires_explicit_never_activated_proof(tmp_path):
    from nebius_cxcli.tailscale_retirement import retire_members

    record = {
        "activation_started": False,
        "lifecycle": "infrastructure-ready",
        "members": {"a": {}},
    }
    journal = NS(value={"gateways": {"ts": record}}, save=lambda: None)
    owner = NS(open=lambda: journal, api=lambda _: pytest.fail("Tailscale credential read"))
    retire_members(owner, row(), {}, NS())
    assert record["lifecycle"] == "retiring"
    del record["activation_started"]
    with pytest.raises(pytest.fail.Exception, match="credential read"):
        retire_members(owner, row(), {}, NS())
