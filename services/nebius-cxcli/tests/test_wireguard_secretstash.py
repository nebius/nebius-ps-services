from __future__ import annotations

import json
from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest
import yaml
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.iam.v1 import Group, GroupSpec

from nebius_cxcli.object_storage_errors import ObjectStorageError
from nebius_cxcli.ssh_host_identity import generate_host_identity
from nebius_cxcli.wireguard_access import client_access, validate_access
from nebius_cxcli.wireguard_journal import WireGuardJournal, journal_keys
from nebius_cxcli.wireguard_secret_api import SecretStashAPI


class Store:
    def __init__(self):
        self.items = {}
        self.serial = 0
        self.lose_ack = False

    def request(self, operation, key, *, body=b"", etag=None, create_only=False, **kwargs):
        prior = self.items.get(key)
        if operation == "get_object":
            if prior is None:
                raise ObjectStorageError("NoSuchKey")
            return NS(etag=prior[0], body=prior[1])
        if create_only and prior or etag and (not prior or prior[0] != etag):
            raise ObjectStorageError("PreconditionFailed")
        assert create_only or etag, "all mutations must be conditional"
        self.serial += 1
        if operation == "put_object":
            self.items[key] = (str(self.serial), body)
        else:
            self.items.pop(key)
        if self.lose_ack:
            self.lose_ack = False
            raise ObjectStorageError("transport read timeout")
        return NS(etag=str(self.serial))


@pytest.fixture(scope="module")
def pair():
    return generate_host_identity()


def connection(pair):
    return dict(
        schema=1,
        state="READY",
        project_id="project",
        component_id="vpn",
        generation="generation",
        instance_id="vm",
        boot_disk_id="disk",
        public_allocation_id="allocation",
        public_ip="203.0.113.10",
        ssh_user="ubuntu",
        host_public_key=pair["ed25519_public"],
        login_secret_id="login",
        login_version_id="login-v1",
    )


def test_remote_lock_excludes_second_machine_and_has_no_expiry():
    store = Store()
    first = WireGuardJournal(store, "project", "vpn")
    second = WireGuardJournal(store, "project", "vpn")
    first.acquire()
    with pytest.raises(RuntimeError, match="locked"):
        second.acquire()
    with pytest.raises(RuntimeError, match="changed"):
        second.unlock("wrong-owner")
    first.release()
    second.acquire()
    second.release()
    assert journal_keys("project", "vpn") != journal_keys("other-project", "vpn")


def test_journal_reconciles_lost_ack_and_rejects_secrets():
    store = Store()
    journal = WireGuardJournal(store, "project", "vpn")
    store.lose_ack = True
    journal.acquire()
    journal.value = dict(schema=1, project_id="project", component_id="vpn", phase="preparing")
    store.lose_ack = True
    journal.save()
    assert WireGuardJournal(store, "project", "vpn").value == journal.value
    journal.value["ed25519_private"] = "secret"
    with pytest.raises(RuntimeError, match="forbidden"):
        journal.save()
    journal.release()


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"admin_group_id": ""},
        {"admin_group_id": "group", "private_key": "no"},
        {"admin_group_id": "group", "generation": "generation"},
    ],
)
def test_access_contract_fails_closed(bad):
    with pytest.raises(ValueError):
        validate_access({"enabled": True, "access": bad})


def test_personal_keys_are_rejected():
    with pytest.raises(ValueError, match="lifecycle-owned"):
        validate_access(
            {
                "enabled": True,
                "access": {"admin_group_id": "group", "secretstash_ref": "mysterybox@vpn-access"},
                "inputs": {"ssh_public_key": "key"},
            }
        )


def test_direct_reader_uses_only_payloads_and_cleans_temporary_material(pair):
    current = connection(pair)
    calls = []

    def payload(secret, version=""):
        calls.append((secret, version))
        return (
            ("c1", {"connection": json.dumps(current)})
            if secret == "connection"
            else ("login-v1", pair)
        )

    with client_access(
        NS(payload=payload),
        project="project",
        component="vpn",
        access={"connection_secret_id": "connection", "generation": "generation"},
    ) as access:
        assert access.private_key.stat().st_mode & 0o777 == 0o600
        assert access.private_key.parent.stat().st_mode & 0o777 == 0o700
        assert access.known_hosts.read_text() == f"203.0.113.10 {pair['ed25519_public']}\n"
        private = access.private_key
    assert not private.exists()
    assert calls == [("connection", ""), ("login", "login-v1"), ("connection", "")]


@pytest.mark.parametrize("state", ["PENDING", "RETIRED"])
def test_nonready_binding_fails_before_login_payload(pair, state):
    current = {**connection(pair), "state": state}
    calls = []

    def payload(secret, version=""):
        calls.append(secret)
        return "c1", {"connection": json.dumps(current)}

    with (
        pytest.raises(RuntimeError, match="unavailable"),
        client_access(
            NS(payload=payload),
            project="project",
            component="vpn",
            access={"connection_secret_id": "connection", "generation": "generation"},
        ),
    ):
        pytest.fail("must not expose credentials")
    assert calls == ["connection"]


def test_binding_change_blocks_ssh(pair):
    calls = 0

    def payload(secret, version=""):
        nonlocal calls
        calls += 1
        if secret == "login":
            return "login-v1", pair
        return ("c1" if calls == 1 else "c2"), {"connection": json.dumps(connection(pair))}

    with (
        pytest.raises(RuntimeError, match="changed"),
        client_access(
            NS(payload=payload),
            project="project",
            component="vpn",
            access={"connection_secret_id": "connection", "generation": "generation"},
        ),
    ):
        pytest.fail("must not reach SSH")


def group(identity, parent, name="admins"):
    return Group(
        metadata=ResourceMetadata(id=identity, parent_id=parent, name=name), spec=GroupSpec()
    )


def test_group_discovery_fully_paginates_both_scopes_and_retains_duplicate_names(monkeypatch):
    api = SecretStashAPI(object())
    calls = []
    responses = {
        ("tenant", ""): NS(items=[group("tenant-group", "tenant")], next_page_token="next"),
        ("tenant", "next"): NS(items=[group("tenant-group-2", "tenant")], next_page_token=""),
        ("project", ""): NS(items=[group("project-group", "project")], next_page_token=""),
    }

    def call(method, request):
        calls.append((request.parent_id, request.page_token))
        return responses[calls[-1]]

    monkeypatch.setattr(api, "call", call)
    result = api.admin_groups("tenant", "project")
    assert calls == [("tenant", ""), ("tenant", "next"), ("project", "")]
    assert {r["id"] for r in result} == {"tenant-group", "tenant-group-2", "project-group"}


@pytest.mark.parametrize("failure", ["scope", "repeated", "denied", "duplicate"])
def test_group_discovery_never_returns_partial_results(monkeypatch, failure):
    api = SecretStashAPI(object())

    def call(method, request):
        if request.parent_id == "tenant":
            return NS(items=[group("tenant-group", "tenant")], next_page_token="")
        if failure == "denied":
            raise RuntimeError("denied")
        if failure == "scope":
            return NS(items=[group("other", "sibling")], next_page_token="")
        if failure == "duplicate":
            return NS(
                items=[group("same", "project"), group("same", "project", "changed")],
                next_page_token="",
            )
        return NS(items=[], next_page_token="repeat")

    monkeypatch.setattr(api, "call", call)
    with pytest.raises(RuntimeError):
        api.admin_groups("tenant", "project")


def test_group_picker_never_uses_plugins_after_denial(monkeypatch):
    from nebius_cxcli import provider_options as providers
    from nebius_cxcli import wireguard_secret_api as sdk

    def denied(*args):
        raise RuntimeError("denied")

    monkeypatch.setattr(sdk, "wireguard_api", lambda: nullcontext(NS(admin_groups=denied)))
    monkeypatch.setattr(
        providers, "_load_option_plugins", lambda *_: pytest.fail("plugin fallback")
    )
    lookup = providers.ProviderOptionLookup()
    assert (
        lookup.resolve(
            provider="wireguard_admin_groups",
            args={},
            payload={"client_info": {"nebius": {"tenant_id": "tenant", "project_id": "project"}}},
            field_path="",
        )
        == []
    )
    assert lookup.last_error()


@pytest.mark.parametrize("denied", [False, True])
def test_component_wizard_requires_live_group_selection(monkeypatch, denied):
    import yaml

    from nebius_cxcli import cli, wireguard_secret_api
    from nebius_cxcli.components import ComponentEntry
    from nebius_cxcli.provider_options import ProviderOptionLookup
    from nebius_cxcli.wizard_profiles import resolve_builtin_wizard_profile

    entry = ComponentEntry(
        id="wireguard-gw",
        scope="infra",
        config_path="infra.wireguard-gw",
        description="VPN",
        source="../../platform-infra//modules/wireguard-gw",
        wizard_fields={
            "access.admin_group_id": resolve_builtin_wizard_profile("wireguard-gw")[
                "access.admin_group_id"
            ]
        },
    )
    payload = {
        "version": "v2",
        "client_info": {"nebius": {"tenant_id": "tenant", "project_id": "project"}},
        "infra": {
            "components": [
                {
                    "id": "wireguard-gw",
                    "instance_id": "vpn",
                    "enabled": True,
                    "inputs": {"name": "vpn"},
                }
            ]
        },
        "apps": {"charts": []},
    }
    monkeypatch.setattr(cli, "module_variables", lambda _: ())
    monkeypatch.setattr(cli, "module_required_variables", lambda _: ())
    monkeypatch.setattr(cli, "_runtime_required_input_leaf_names", lambda _: set())
    monkeypatch.setattr(cli, "_wizard_continue_phase", lambda *a, **k: True)
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    lookups, prompts = [], []

    def groups(tenant, project):
        lookups.append((tenant, project))
        assert (tenant, project) == ("tenant", "project")
        if denied:
            raise RuntimeError("denied")
        return [
            dict(id=identity, name="Admins", parent_id=parent, scope=parent)
            for identity, parent in [("tenant-group", tenant), ("project-group", project)]
        ]

    monkeypatch.setattr(
        wireguard_secret_api, "wireguard_api", lambda: nullcontext(NS(admin_groups=groups))
    )

    def prompt(path, current, **kwargs):
        assert not denied, "failed discovery must never fall back to a manual prompt"
        prompts.append(path)
        assert path.endswith(".access.admin_group_id") and kwargs["required"]
        selected = next(choice for choice in kwargs["choices"] if choice.value == "project-group")
        assert selected.metadata == {
            "live_id": "project-group",
            "parent_id": "project",
            "scope": "project",
        }
        return selected.value, False

    monkeypatch.setattr(cli, "_prompt_scalar_override", prompt)

    def run():
        return cli._run_component_field_wizard(
            config_yaml=yaml.safe_dump(payload),
            selected_infra={"vpn"},
            selected_apps=set(),
            infra_entries=(entry,),
            app_entries=(),
            provider_lookup=ProviderOptionLookup(),
        )

    if denied:
        with pytest.raises(RuntimeError, match="Complete live IAM group choices"):
            run()
        assert not prompts
    else:
        result, complete = run()
        assert complete and len(prompts) == 1
        assert (
            yaml.safe_load(result)["infra"]["components"][0]["access"]["admin_group_id"]
            == "project-group"
        )
    assert lookups


@pytest.mark.parametrize("credentials_file", [None, "explicit-credentials.json"])
def test_wireguard_sdk_preserves_explicit_auth_and_user_agent(monkeypatch, credentials_file):
    from nebius.sdk import SDK

    from nebius_cxcli import sdk_auth
    from nebius_cxcli.wireguard_secret_api import wireguard_api

    monkeypatch.setenv("NEBIUS_IAM_TOKEN", "unrelated-credential")

    def operator(**_):
        assert credentials_file is None
        return "selected-identity"

    monkeypatch.setattr(sdk_auth, "acquire_operator_access_token", operator)
    seen = []
    monkeypatch.setattr(SDK, "__init__", lambda self, **kwargs: seen.append(kwargs))
    monkeypatch.setattr(SDK, "sync_close", lambda self: None)
    with wireguard_api(credentials_file=credentials_file):
        pass
    auth = (
        {"credentials_file_name": credentials_file}
        if credentials_file
        else {"credentials": "selected-identity"}
    )
    assert seen == [
        {
            **auth,
            "domain": "api.nebius.cloud",
            "user_agent_prefix": sdk_auth.NEBIUS_SDK_USER_AGENT_PREFIX,
        }
    ]


def test_real_wireguard_sdk_constructor_emits_no_future_warning(monkeypatch):
    import warnings

    from nebius_cxcli import sdk_auth
    from nebius_cxcli.wireguard_secret_api import wireguard_api

    monkeypatch.setattr(sdk_auth, "acquire_operator_access_token", lambda **_: "test-only-token")
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        with wireguard_api():
            pass


def test_source_only_loader_does_not_invoke_deploy_or_normalization(tmp_path, monkeypatch):
    from nebius_cxcli import cli, config_loader
    from nebius_cxcli.wireguard_commands import load_wireguard_context

    payload = {
        "version": "v2",
        "client_info": {"nebius": {"project_id": "project", "tenant_id": "tenant"}},
        "infra": {
            "components": [
                {
                    "id": "wireguard-gw",
                    "instance_id": "vpn",
                    "enabled": True,
                    "inputs": {"ssh_user_name": "ubuntu"},
                    "access": {
                        "admin_group_id": "group",
                        "secretstash_ref": "mysterybox@vpn-access",
                        "connection_secret_id": "connection",
                        "generation": "generation",
                    },
                }
            ]
        },
        "apps": {"charts": []},
    }
    path = tmp_path / "tenant/project/config.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(payload))
    before = path.read_bytes()
    monkeypatch.setattr(cli, "_load_deploy_context", lambda *_: pytest.fail("deploy context"))
    monkeypatch.setattr(config_loader, "load_config", lambda *_: pytest.fail("normalizing loader"))
    loaded, _, component, row, project = load_wireguard_context(path)
    assert loaded == payload and component.instance_id == "vpn" and project == "project"
    assert row["access"]["connection_secret_id"] == "connection"
    assert path.read_bytes() == before


def test_uncertain_provider_effect_retains_lock_until_terminal_reconciliation(monkeypatch):
    store = Store()
    journal = WireGuardJournal(store, "project", "vpn")
    journal.acquire()
    journal.value = dict(
        schema=1, project_id="project", component_id="vpn", versions={"login": {"nonce": "nonce"}}
    )
    journal.begin_effect(
        client="SecretVersionServiceClient", nonce="nonce", collection="versions", key="login"
    )
    journal.value["in_flight"]["operation_id"] = "operation"
    journal.save()
    journal.release()
    with pytest.raises(RuntimeError, match="locked"):
        WireGuardJournal(store, "project", "vpn").acquire()
    api = SecretStashAPI(NS(parent_id=lambda: None))
    operation = NS(
        id="operation", resource_id="secret", done=lambda: False, successful=lambda: True
    )
    monkeypatch.setattr(api, "call", lambda *a, **k: operation)
    with pytest.raises(RuntimeError, match="still running"):
        api.reconcile_effect(journal)
    operation.done = lambda: True
    api.reconcile_effect(journal)
    assert journal.value["versions"]["login"]["id"] == "secret"
    journal.release()
    other = WireGuardJournal(store, "project", "vpn")
    other.acquire()
    other.release()


def test_unknown_dispatch_is_never_recreated_or_unlocked(monkeypatch):
    store = Store()
    journal = WireGuardJournal(store, "project", "vpn")
    journal.acquire()
    journal.value = dict(schema=1, project_id="project", component_id="vpn")
    journal.begin_effect(
        client="SecretVersionServiceClient", nonce="nonce", collection="versions", key="login"
    )
    api = SecretStashAPI(object())
    with pytest.raises(RuntimeError, match="acknowledgement was lost"):
        api.reconcile_effect(journal)
    journal.release()
    assert journal.read(journal.lock_key)


def test_provider_error_reports_only_status_code():
    from grpc import StatusCode
    from nebius.aio.service_error import RequestError

    api = SecretStashAPI(object())
    status = NS(code=StatusCode.INVALID_ARGUMENT, message="sensitive service detail")

    def failed(*args, **kwargs):
        raise RequestError(status)

    with pytest.raises(RuntimeError) as raised:
        api.call(failed, object())
    assert "INVALID_ARGUMENT" in str(raised.value)
    assert "sensitive" not in str(raised.value)
    assert raised.value.__suppress_context__


@pytest.mark.parametrize(
    ("kind", "failure"),
    [("version", "payload")],
)
def test_persisted_undispatched_intent_can_resume(monkeypatch, kind, failure):
    store = Store()
    journal = WireGuardJournal(store, "project", "vpn")
    journal.acquire()
    journal.value = dict(schema=1, project_id="project", component_id="vpn", generation="gen")
    api = SecretStashAPI(object())

    def interrupted(*args):
        raise RuntimeError("interrupted before dispatch")

    def invoke(owner, factory):
        return api.version(owner, owner.value, "candidate", "secret", factory)

    monkeypatch.setattr(api, "pages", interrupted if failure == "discovery" else lambda *a: [])
    monkeypatch.setattr(api, "call", lambda *a, **k: pytest.fail("unexpected dispatch"))
    with pytest.raises(RuntimeError, match="before dispatch"):
        invoke(journal, interrupted)
    assert not journal.value.get("in_flight")
    before = json.dumps(journal.value, sort_keys=True)
    journal.release()
    resumed = WireGuardJournal(store, "project", "vpn")
    resumed.acquire()
    assert json.dumps(resumed.value, sort_keys=True) == before
    monkeypatch.setattr(api, "pages", lambda *a: [])

    def dispatched(*args, **kwargs):
        assert resumed.value["in_flight"]["nonce"] == kwargs["operation_key"]
        raise RuntimeError("resumed dispatch")

    monkeypatch.setattr(api, "call", dispatched)
    with pytest.raises(RuntimeError, match="resumed dispatch"):
        invoke(resumed, lambda: {"value": "fixture"})
    resumed.release()
    assert resumed.read(resumed.lock_key)  # A dispatch is now uncertain, never auto-retried.


@pytest.mark.parametrize("changed", ["", "id", "parent", "state"])
def test_version_resume_uses_recorded_identity_without_labels(monkeypatch, changed):
    from nebius.api.nebius.mysterybox.v1 import SecretVersion, SecretVersionStatus

    journal = WireGuardJournal(Store(), "project", "vpn")
    journal.acquire()
    journal.value = dict(
        schema=1,
        project_id="project",
        component_id="vpn",
        versions={"ready": dict(id="version", secret="secret", nonce="nonce", operation_id="op")},
    )
    api = SecretStashAPI(object())
    version = SecretVersion(
        metadata=ResourceMetadata(
            id="other" if changed == "id" else "version",
            parent_id="other" if changed == "parent" else "secret",
        ),
        status=SecretVersionStatus(
            state=(
                SecretVersionStatus.State.SCHEDULED_FOR_DELETION
                if changed == "state"
                else SecretVersionStatus.State.ACTIVE
            )
        ),
    )

    def call(method, request, **kwargs):
        assert type(request).__name__ == "GetSecretVersionRequest"
        assert request.id == "version"
        return version

    monkeypatch.setattr(api, "call", call)
    monkeypatch.setattr(api, "pages", lambda *a: [])

    def factory():
        pytest.fail("An owned version must never regenerate payload")

    if changed:
        with pytest.raises(RuntimeError, match="identity|active"):
            api.version(journal, journal.value, "ready", "secret", factory)
    else:
        assert api.version(journal, journal.value, "ready", "secret", factory) == "version"
    assert "in_flight" not in journal.value
    journal.release()


@pytest.mark.parametrize("resource_version", [0, 42])
@pytest.mark.parametrize("initial_primary", [None, "", "old"])
def test_secret_primary_update_preserves_metadata_and_rejects_conflicts(
    monkeypatch, resource_version, initial_primary
):
    from nebius.api.nebius.mysterybox.v1 import Secret, SecretSpec

    store = Store()
    journal = WireGuardJournal(store, "project", "vpn")
    journal.acquire()
    journal.value = dict(schema=1, project_id="project", component_id="vpn")
    api = SecretStashAPI(object())
    spec = SecretSpec(description="retain description")
    if initial_primary is not None:
        spec.primary_version_id = initial_primary
    assert spec.primary_version_id == initial_primary
    current = Secret(
        metadata=ResourceMetadata(
            id="secret",
            parent_id="project",
            name="connection",
            labels={"owner": "fixture"},
            resource_version=resource_version,
        ),
        spec=spec,
    )
    original_metadata = current.metadata.to_json()
    calls = []

    def call(method, request, **kwargs):
        calls.append(request)
        if type(request).__name__ == "UpdateSecretRequest":
            assert request.metadata.to_json() == original_metadata
            assert request.spec.description == "retain description"
            current.spec.primary_version_id = request.spec.primary_version_id
            return NS(id="operation", sync_wait=lambda **k: None, successful=lambda: True)
        return current

    monkeypatch.setattr(api, "call", call)
    expected = initial_primary or ""
    api.publish(journal, "secret", "new", expected)
    assert current.spec.primary_version_id == "new"
    assert "in_flight" not in journal.value
    # A lost acknowledgement must not cause a second publication on replay.
    api.publish(journal, "secret", "new", expected)
    with pytest.raises(RuntimeError, match="concurrently"):
        api.publish(journal, "secret", "next", expected)
    assert "in_flight" not in journal.value
    assert len([r for r in calls if type(r).__name__ == "UpdateSecretRequest"]) == 1
    journal.release()


@pytest.mark.parametrize(
    "returned", [group("deleted-and-replaced", "project"), group("selected", "sibling")]
)
def test_group_revalidation_checks_exact_id_and_scope(monkeypatch, returned):
    api = SecretStashAPI(object())

    def call(method, request):
        assert request.id == "selected"
        return returned

    monkeypatch.setattr(api, "call", call)
    with pytest.raises(RuntimeError, match="scope"):
        api.validate_group("selected", "tenant", "project")


def test_direct_broad_payload_grants_are_paginated_and_resource_scoped(monkeypatch):
    from nebius.api.nebius.iam import v1 as iam

    api = SecretStashAPI(object())
    requests = []

    def permit(identity, resource, role):
        return iam.AccessPermit(
            metadata=ResourceMetadata(id=identity, parent_id="operators"),
            spec=iam.AccessPermitSpec(resource_id=resource, role=role),
        )

    def call(method, request):
        requests.append((request.parent_id, request.page_token))
        if not request.page_token:
            return NS(
                items=[
                    permit("broad", "project", "mysterybox.payload-viewer"),
                    permit("narrow", "secret", "mysterybox.payload-viewer"),
                ],
                next_page_token="page-2",
            )
        return NS(
            items=[
                permit("admin", "tenant", "admin"),
                permit("editor", "project", "editor"),
                permit("foreign", "other", "admin"),
            ],
            next_page_token="",
        )

    monkeypatch.setattr(api, "call", call)
    assert api.direct_broad_payload_grants("operators", "tenant", "project") == [
        {"id": "broad", "role": "mysterybox.payload-viewer", "scope": "project"},
        {"id": "admin", "role": "admin", "scope": "tenant"},
    ]
    assert requests == [("operators", ""), ("operators", "page-2")]


@pytest.mark.parametrize("wrong", [None, "scope", "grant", "membership", "extra-grant"])
def test_access_readback_uses_exact_sdk_metadata_and_narrow_grants(monkeypatch, wrong):
    from nebius.api.nebius.iam import v1 as iam
    from nebius.api.nebius.mysterybox import v1 as box

    resources = {
        key: "id-" + key
        for key in (
            "login",
            "host",
            "connection",
            "account",
            "group",
            "membership",
            "host-permit",
            "login-permit",
            "connection-permit",
        )
    }
    names = {role: "vpn-access-" + role for role in ("login", "host", "connection")}
    objects = {
        resources[role]: box.Secret(
            metadata=ResourceMetadata(
                id=resources[role],
                parent_id="other" if wrong == "scope" and role == "host" else "project",
                name=names[role],
            ),
            status=box.SecretStatus(state=box.SecretStatus.State.ACTIVE),
        )
        for role in names
    }
    objects[resources["account"]] = iam.ServiceAccount(
        metadata=ResourceMetadata(id=resources["account"], parent_id="project")
    )
    objects[resources["group"]] = iam.Group(
        metadata=ResourceMetadata(id=resources["group"], parent_id="project")
    )
    membership = iam.GroupMembership(
        metadata=ResourceMetadata(id=resources["membership"], parent_id=resources["group"]),
        spec=iam.GroupMembershipSpec(
            member_id="other" if wrong == "membership" else resources["account"]
        ),
    )
    for role in names:
        key = resources[role + "-permit"]
        objects[key] = iam.AccessPermit(
            metadata=ResourceMetadata(
                id=key, parent_id=resources["group"] if role == "host" else "admins"
            ),
            spec=iam.AccessPermitSpec(
                resource_id=resources[role],
                role="editor" if wrong == "grant" else "mysterybox.payload-viewer",
            ),
        )
    api = SecretStashAPI(object())
    calls = []

    def get(method, request, **kwargs):
        assert method.__name__ == "get"
        calls.append(request.id)
        return objects[request.id]

    monkeypatch.setattr(api, "call", get)
    monkeypatch.setattr(
        api,
        "pages",
        lambda method, request, parent, field: (
            [membership]
            if field == "memberships"
            else [objects[resources["host-permit"]]] * (2 if wrong == "extra-grant" else 1)
        ),
    )
    if wrong:
        with pytest.raises(RuntimeError):
            api.verify_access_resources(
                resources, project="project", admin_group="admins", names=names
            )
    else:
        api.verify_access_resources(resources, project="project", admin_group="admins", names=names)
        assert len(calls) == 8
