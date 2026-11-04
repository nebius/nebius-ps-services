from __future__ import annotations

import copy
import json
from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest
import yaml

from nebius_cxcli import wireguard_host_identity as wg
from nebius_cxcli.wireguard_commands import rotate_login
from test_wireguard_secretstash import Store


class API:
    def __init__(self):
        self.data = {}
        self.primary = {}
        self.created = []

    def validate_group(self, *args):
        assert args == ("admins", "tenant", "project")

    def direct_broad_payload_grants(self, *args):
        assert args == ("admins", "tenant", "project")
        return []

    def payload(self, secret, version=""):
        version = version or self.primary[secret]
        return version, copy.deepcopy(self.data[secret, version])

    def version(self, journal, record, key, secret, factory):
        version = "version-" + key
        if (secret, version) not in self.data:
            self.data[secret, version] = factory()
        record.setdefault("versions", {})[key] = {"id": version}
        journal.save()
        return version

    def publish(self, journal, secret, version, expected):
        assert self.primary.get(secret, "") in {version, expected}
        self.primary[secret] = version


@pytest.fixture
def setup(tmp_path, monkeypatch):
    import nebius_cxcli.object_storage_transport as storage

    row = {
        "id": "wireguard-gw",
        "instance_id": "vpn",
        "enabled": True,
        "inputs": {"parent_id": "project", "ssh_user_name": "ubuntu"},
        "access": {"admin_group_id": "admins"},
    }
    config = {
        "version": "v2",
        "client_info": {"nebius": {"project_id": "project", "tenant_id": "tenant"}},
        "infra": {"components": [row]},
        "apps": {"charts": []},
    }
    from nebius_cxcli.wireguard_dependencies import materialize_wireguard_dependencies

    materialize_wireguard_dependencies(config)
    manifest = {
        "schema": "nebius-cxcli-generated/v3",
        "execution": {
            "backend": {},
            "targets": [],
            "required_component_outputs": [],
            "status_watchers": [],
            "validations": [],
        },
        "runtime_config": copy.deepcopy(config),
        "render": {
            "module_sources": [
                {"component_id": "wireguard-gw", "instance_id": "vpn", "module_name": "vpn"}
            ]
        },
    }
    path = tmp_path / "config.yaml"
    import hashlib

    manifest["render"]["module_sources"][0]["source"] = "fixture"
    manifest["render"]["wireguard_network"] = {
        "vpn": {
            "schema": 1,
            "source": "fixture",
            "module_name": "vpn",
            "content": "# fixture\n",
            "sha256": hashlib.sha256(b"# fixture\n").hexdigest(),
            "wireguard_mtu": None,
        }
    }
    path.write_text(yaml.safe_dump(config))
    paths = NS(
        config_path=path,
        project_dir=tmp_path,
        infra_dir=tmp_path,
        generated_dir=tmp_path,
        access_infra_dir=tmp_path / "infra-access",
    )
    from nebius_cxcli.terraform_backend import TerraformBackendSettings

    settings = TerraformBackendSettings(
        project_id="project",
        key="state",
        bucket="bucket",
        client_name="client",
        region_id="eu-north1",
        endpoint="https://storage.eu-north1.nebius.cloud",
    )
    from dataclasses import asdict

    from nebius_cxcli.terraform_roots import root_manifest

    manifest["execution"].update(
        backend=asdict(settings), terraform_roots=root_manifest(config, settings=settings)
    )
    manifest["render"]["access_tfvars"] = {}
    store, api = Store(), API()
    monkeypatch.setattr(storage, "object_storage_transport", lambda *a, **k: nullcontext(store))
    monkeypatch.setattr(wg, "wireguard_api", lambda **kw: nullcontext(api))
    executor = NS(
        config=config,
        manifest=manifest,
        paths=paths,
        report_paths=paths,
        runtime_env={},
        options=NS(dry_run=False),
        lease=NS(assert_held=lambda: None),
        cli=NS(
            _terraform_runtime_env=lambda *_: {},
            terraform_output_json=lambda *a, **k: {},
            terraform_show_json=lambda *a, **k: {},
        ),
    )
    from nebius_cxcli import wireguard_foundation

    def foundation(owner, *, verify_only=False):
        owner.executor.lease.assert_held()
        for journal in owner.journals.values():
            journal.assert_held()
        for journal in owner.journals.values():
            if "resources" not in journal.value:
                assert not verify_only
                roles = (
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
                journal.value["resources"] = {role: {"id": "owned-" + role} for role in roles}
                api.created.extend(roles)
                journal.save()

    monkeypatch.setattr(wireguard_foundation, "reconcile_access_root", foundation)
    return executor, settings, store, api


def test_install_prepares_only_public_frozen_inputs_and_resumes(setup):
    executor, settings, store, api = setup
    with wg.WireGuardHostIdentities(executor, settings=settings) as owner:
        owner.prepare()
        first = dict(executor.runtime_env)
        assert set(first) == {"TF_VAR_vpn_access_bootstrap"}
        assert "PRIVATE KEY" not in json.dumps(first)
        executor.runtime_env.clear()
        owner.prepare()
        assert executor.runtime_env == first
    created = list(api.created)
    with wg.WireGuardHostIdentities(executor, settings=settings) as owner:
        owner.prepare()
        assert executor.runtime_env == first
    assert api.created == created
    assert len(created) == 9
    assert not (executor.paths.project_dir / ".nebius-cxcli/wireguard-host-keys").exists()
    assert all(b"PRIVATE KEY" not in body for _, body in store.items.values())
    assert wg.CURRENT_WIREGUARD_IDENTITIES.get() is None


def test_preview_unresolved_is_readonly(setup):
    executor, settings, store, api = setup
    executor.options.dry_run = True
    with (
        wg.WireGuardHostIdentities(executor, settings=settings) as owner,
        pytest.raises(RuntimeError, match="unresolved"),
    ):
        owner.prepare()
    assert not store.items and not api.created


def test_failed_preparation_reentry_keeps_its_owner_and_releases_clean_lock(setup):
    executor, settings, store, api = setup
    executor.cli.terraform_output_json = lambda *a, **kw: {"vpn_instance_id": {"value": "old"}}
    with wg.WireGuardHostIdentities(executor, settings=settings) as owner:
        for _ in range(2):
            with pytest.raises(RuntimeError, match="Existing WireGuard VM"):
                owner.prepare()
    assert not api.created
    assert not store.items


def test_preparation_reentry_preserves_provider_status_without_redispatch(setup, monkeypatch):
    executor, settings, store, api = setup
    calls = []

    def denied(journal, record, kind, *args, **kwargs):
        calls.append(kind)
        journal.begin_effect(
            client="SecretServiceClient", nonce="nonce", collection="resources", key=kind
        )
        raise wg.WireGuardRequestError("PERMISSION_DENIED")

    monkeypatch.setattr(api, "version", denied)
    with wg.WireGuardHostIdentities(executor, settings=settings) as owner:
        for _ in range(2):
            with pytest.raises(wg.WireGuardRequestError, match="PERMISSION_DENIED"):
                owner.prepare()
    assert calls == ["initial-login"]
    assert owner.journals["vpn"].read(owner.journals["vpn"].lock_key)


@pytest.mark.parametrize("change", ["config", "module", "duplicate"])
def test_child_admission_cannot_rebind_public_bootstrap(setup, change):
    executor, settings, _, _ = setup
    with wg.WireGuardHostIdentities(executor, settings=settings) as owner:
        owner.prepare()
        child = NS(
            config=copy.deepcopy(executor.config),
            manifest=copy.deepcopy(executor.manifest),
            runtime_env={},
        )
        if change == "config":
            child.config["infra"]["components"][0]["inputs"]["parent_id"] = "other"
        elif change == "module":
            child.manifest["render"]["module_sources"][0]["module_name"] = "other"
        else:
            child.manifest["render"]["module_sources"] *= 2
        with pytest.raises(RuntimeError):
            owner.apply_runtime_inputs(child)


def finish(owner, executor, monkeypatch):
    from nebius_cxcli import wireguard_networking

    monkeypatch.setattr(wireguard_networking, "network_asset", lambda *a: {"sha256": "fixture"})
    monkeypatch.setattr(
        wireguard_networking, "reconcile_guest", lambda *a: {"sha256": "fixture", "mtu": 1370}
    )
    outputs = {
        f"vpn_{key}": {"value": value}
        for key, value in {
            "instance_id": "vm",
            "boot_disk_id": "disk",
            "public_ip": "203.0.113.10",
            "public_ip_allocation_id": "allocation",
        }.items()
    }
    executor.cli.terraform_output_json = lambda *a, **k: outputs
    monkeypatch.setattr(owner, "_verify_compute", lambda *a: None)
    monkeypatch.setattr(wg, "wait_for_guest", lambda *a: None)
    owner.complete()


def test_completion_publishes_ready_and_only_nonsecret_config_refs(setup, monkeypatch):
    executor, settings, _, api = setup
    with wg.WireGuardHostIdentities(executor, settings=settings) as owner:
        owner.prepare()
        assert (
            "connection_secret_id"
            not in yaml.safe_load(executor.paths.config_path.read_text())["infra"]["components"][0][
                "access"
            ]
        )
        finish(owner, executor, monkeypatch)
        value = json.loads(api.payload("owned-connection")[1]["connection"])
        assert value["state"] == "READY" and value["instance_id"] == "vm"
        access = yaml.safe_load(executor.paths.config_path.read_text())["infra"]["components"][0][
            "access"
        ]
        assert access == {
            "secretstash_ref": "mysterybox@vpn-access",
            "admin_group_id": "admins",
            "connection_secret_id": "owned-connection",
            "generation": value["generation"],
        }
        assert "PRIVATE KEY" not in executor.paths.config_path.read_text()
        owner.complete()  # idempotent completion has no new publication


def test_concurrent_source_change_is_preserved(setup, monkeypatch):
    executor, settings, _, _ = setup
    with wg.WireGuardHostIdentities(executor, settings=settings) as owner:
        owner.prepare()
        executor.paths.config_path.write_text(
            executor.paths.config_path.read_text() + "# concurrent\n"
        )
        with pytest.raises(RuntimeError, match="changed during"):
            finish(owner, executor, monkeypatch)
        assert executor.paths.config_path.read_text().endswith("# concurrent\n")


def test_delete_and_replacement_require_retirement_workflow(setup):
    executor, settings, _, _ = setup
    with wg.WireGuardHostIdentities(executor, settings=settings) as owner:
        for actions in (["delete"], ["delete", "create"]):
            with pytest.raises(RuntimeError, match="explicit destroy"):
                owner.check_plan(
                    {
                        "resource_changes": [
                            {
                                "address": "module.vpn.module.vm.nebius_compute_v1_instance.this",
                                "change": {"actions": actions, "before": {}},
                            }
                        ]
                    }
                )
        owner.check_plan(
            {
                "resource_changes": [
                    {"address": "module.other.vm", "change": {"actions": ["update"], "before": {}}}
                ]
            }
        )


@pytest.mark.parametrize("interruption", [None, "stage", "retire", "finish"])
def test_rotation_publishes_only_after_old_key_rejection_and_preserves_bootstrap(
    setup, monkeypatch, interruption
):
    import nebius_cxcli.wireguard_commands as commands
    from nebius_cxcli.ssh_host_identity import _pair

    executor, settings, _, api = setup
    with wg.WireGuardHostIdentities(executor, settings=settings) as owner:
        owner.prepare()
        finish(owner, executor, monkeypatch)
        journal = owner.journals["vpn"]
        frozen = copy.deepcopy(journal.value["bootstrap"])
        old = frozen["login_public_key"]
        authorized = {old}
        interrupted = False
        guest_phase = ""

        def remote(ssh, command, payload=None):
            nonlocal interrupted, guest_phase
            key = _pair(ssh.private_key.read_bytes())["ed25519_public"]
            if key not in authorized:
                return NS(returncode=255, stderr="Permission denied (publickey).", stdout="")
            if command == "inspect":
                return NS(
                    returncode=0,
                    stderr="",
                    stdout=json.dumps(
                        dict(
                            generation=journal.value["generation"],
                            rotation=journal.value["rotation"]["id"],
                            phase=guest_phase,
                        )
                    ),
                )
            if command == "stage":
                authorized.add(payload["public_key"])
                guest_phase = "staged"
            elif command == "retire":
                authorized.clear()
                authorized.add(payload["public_key"])
                guest_phase = "retired"
            elif command == "finish":
                guest_phase = "complete"
            if command == interruption and not interrupted:
                interrupted = True
                raise RuntimeError("SSH acknowledgement lost")
            return NS(returncode=0, stderr="", stdout="")

        monkeypatch.setattr(commands, "remote_access", remote)
        monkeypatch.setattr(
            commands,
            "verify_guest",
            lambda connection, pair: (
                None
                if pair["ed25519_public"] in authorized
                else pytest.fail("candidate not authorized")
            ),
        )
        if interruption:
            with pytest.raises(RuntimeError, match="acknowledgement lost"):
                rotate_login(api, journal)
            journal.release()
            assert journal.read(journal.lock_key)
            assert journal.value["in_flight"]["client"] == "guest"
            commands.reconcile_guest(api, journal)
            assert "in_flight" not in journal.value
        rotate_login(api, journal)
        assert old not in authorized
        assert journal.value["rotation"]["phase"] == "complete"
        assert journal.value["bootstrap"] == frozen
        assert json.loads(executor.runtime_env["TF_VAR_vpn_access_bootstrap"]) == frozen
        current = json.loads(api.payload("owned-connection")[1]["connection"])
        assert current["state"] == "READY" and current["login_version_id"] != "owned-login-initial"


@pytest.mark.parametrize("ready", [True, False])
def test_destroy_retires_ready_or_partial_gateway_before_yield(setup, monkeypatch, ready):
    from nebius_cxcli import generated_manifest, terraform_backend
    from nebius_cxcli import wireguard_commands as commands
    from nebius_cxcli.wireguard_journal import WireGuardJournal

    executor, settings, store, api = setup
    with wg.WireGuardHostIdentities(executor, settings=settings) as owner:
        owner.prepare()
        if ready:
            finish(owner, executor, monkeypatch)
    outputs = {
        f"vpn_{key}": {"value": value}
        for key, value in dict(
            instance_id="vm",
            boot_disk_id="disk",
            public_ip="203.0.113.10",
            public_ip_allocation_id="allocation",
        ).items()
    }
    verified = []
    api.admit_backend = lambda settings: None
    api.verify_instance_binding = lambda own, record, **kw: verified.append((own, kw))
    monkeypatch.setattr(commands, "wireguard_api", lambda **kw: nullcontext(api))
    live = {"vm"}
    api.verify_instance_absent = lambda instance: (
        None if instance not in live else pytest.fail("VM still exists")
    )
    monkeypatch.setattr(commands, "state_wireguard_instances", lambda *a: live)
    from nebius_cxcli import deployment_cli

    monkeypatch.setattr(
        deployment_cli, "deployment_execution", lambda **kw: nullcontext(executor.lease)
    )
    monkeypatch.setattr(generated_manifest, "load_generated_manifest", lambda *a: executor.manifest)
    monkeypatch.setattr(terraform_backend, "backend_settings_from_config", lambda *a: settings)
    from nebius_cxcli import cli

    monkeypatch.setattr(cli, "terraform_output_json", lambda *a, **k: outputs)
    created = list(api.created)
    with commands.retire_for_destroy(executor.config, executor.paths, {}) as runtime:
        current = json.loads(api.payload("owned-connection")[1]["connection"])
        assert current["state"] == "RETIRED" and current["instance_id"] == "vm"
        assert set(runtime) == {"TF_VAR_vpn_access_bootstrap"}
        assert "PRIVATE KEY" not in json.dumps(runtime)
        with pytest.raises(RuntimeError, match="locked"):
            WireGuardJournal(store, "project", "vpn").acquire()
        live.clear()
    retained = yaml.safe_load(executor.paths.config_path.read_text())["infra"]["components"][0]
    assert retained["enabled"] is False and retained["access"]["lifecycle"] == "retained"
    assert api.created == created
    assert bool(verified) is not ready
    if verified:
        assert verified[0][1] == {"require_running": False}
    journal = WireGuardJournal(store, "project", "vpn")
    journal.acquire()
    assert journal.value["phase"] == "retired"
    journal.release()


def test_direct_apply_cannot_bypass_wireguard_lifecycle(setup):
    from nebius_cxcli.wireguard_commands import guard_direct_apply

    executor, _, _, _ = setup
    with pytest.raises(RuntimeError, match="require deploy"):
        guard_direct_apply(executor.cli, executor.config, executor.paths, {}, initialize=False)


def test_readiness_wait_is_bounded_and_accepts_delayed_success(monkeypatch):
    attempts = []

    def verify(*args):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("booting")

    monkeypatch.setattr(wg, "verify_guest", verify)
    monkeypatch.setattr(wg.time, "sleep", lambda _: None)
    wg.wait_for_guest({}, {}, timeout=1)
    assert len(attempts) == 3
    monkeypatch.setattr(
        wg, "verify_guest", lambda *a: (_ for _ in ()).throw(RuntimeError("booting"))
    )
    with pytest.raises(RuntimeError, match="deadline"):
        wg.wait_for_guest({}, {}, timeout=0)


@pytest.mark.parametrize("persisted", [False, True])
def test_quota_with_unprepared_active_owner_uses_readonly_preview(setup, monkeypatch, persisted):
    from nebius_cxcli import cli, deployment_cli, terraform_backend

    executor, settings, store, api = setup
    if persisted:
        with wg.WireGuardHostIdentities(executor, settings=settings) as previous:
            previous.prepare()
    before = copy.deepcopy(store.items)
    created = list(api.created)
    original = {"EXISTING": "preserved"}
    monkeypatch.setattr(cli, "resolve_project_paths", lambda _: executor.paths)
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda *_: {})
    monkeypatch.setattr(terraform_backend, "backend_settings_from_config", lambda _: settings)

    def preview(config, paths, manifest, *, options, lease):
        assert options.dry_run and lease is None
        return NS(**{**vars(executor), "options": options, "lease": lease, "runtime_env": {}})

    monkeypatch.setattr(deployment_cli, "_CliDeploymentExecutor", preview)
    with wg.WireGuardHostIdentities(executor, settings=settings) as active:
        monkeypatch.setattr(active, "prepare", lambda: pytest.fail("Mutable quota prepare"))

        def observe():
            with deployment_cli.quota_plan_runtime_env(
                executor.paths.infra_dir, executor.manifest, original
            ) as env:
                assert env["EXISTING"] == "preserved"
                assert "TF_VAR_vpn_access_bootstrap" in env
                assert wg.CURRENT_WIREGUARD_IDENTITIES.get() is not active

        if persisted:
            observe()
        else:
            with pytest.raises(RuntimeError, match="unresolved"):
                observe()
        assert wg.CURRENT_WIREGUARD_IDENTITIES.get() is active
        assert not active.prepared and not active.journals
    assert store.items == before and api.created == created
    assert original == {"EXISTING": "preserved"}


def test_quota_reuses_prepared_public_inputs_and_revalidates_identity(setup, monkeypatch):
    from nebius_cxcli import deployment_cli

    executor, settings, store, api = setup
    with wg.WireGuardHostIdentities(executor, settings=settings) as active:
        active.prepare()
        before = copy.deepcopy(store.items)
        monkeypatch.setattr(active, "prepare", lambda: pytest.fail("Already prepared"))
        with deployment_cli.quota_plan_runtime_env(
            executor.paths.infra_dir, executor.manifest, {"EXISTING": "preserved"}
        ) as env:
            assert env["EXISTING"] == "preserved"
            assert "TF_VAR_vpn_access_bootstrap" in env
        changed = copy.deepcopy(executor.manifest)
        changed["runtime_config"]["infra"]["components"][0]["access"]["admin_group_id"] = "other"
        with (
            pytest.raises(RuntimeError),
            deployment_cli.quota_plan_runtime_env(executor.paths.infra_dir, changed, {}),
        ):
            pytest.fail("Changed identity admitted")
        assert store.items == before


def test_preparation_reports_broad_grant_without_revoking_it(setup, monkeypatch):
    executor, settings, store, api = setup
    messages = []
    executor.cli.progress_console = NS(print=lambda message, **kw: messages.append(message))
    monkeypatch.setattr(
        api,
        "direct_broad_payload_grants",
        lambda *args: [
            {"id": "existing-permit", "role": "mysterybox.payload-viewer", "scope": "project"}
        ],
    )
    with wg.WireGuardHostIdentities(executor, settings=settings) as owner:
        owner.prepare()
    assert len(messages) == 1
    assert "project-wide" in messages[0] and "existing-permit" in messages[0]
    assert "Existing grants are unchanged" in messages[0]
    assert "delete" not in api.created


def test_foundation_readback_denial_prevents_payload_creation(setup, monkeypatch):
    from nebius_cxcli import wireguard_foundation

    executor, settings, store, api = setup
    denied = []

    def fail(*args, **kwargs):
        denied.append(True)
        raise wg.WireGuardRequestError("PERMISSION_DENIED")

    monkeypatch.setattr(wireguard_foundation, "reconcile_access_root", fail)
    with wg.WireGuardHostIdentities(executor, settings=settings) as owner:
        for _ in range(2):
            with pytest.raises(wg.WireGuardRequestError, match="PERMISSION_DENIED"):
                owner.prepare()
        assert not owner.prepared
    assert denied == [True] and not api.data
