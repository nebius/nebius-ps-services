from __future__ import annotations

import copy
import json
from types import SimpleNamespace as NS

import pytest

from nebius_cxcli.wireguard_dependencies import (
    access_dependencies,
    access_owner_rows,
    assert_access_removal_allowed,
    materialize_wireguard_dependencies,
    secret_names,
)
from nebius_cxcli.wireguard_foundation import admit_access_plan


@pytest.fixture
def payload():
    value = {
        "version": "v2",
        "client_info": {
            "client_name": "example",
            "nebius": {"project_id": "project", "region_id": "eu-north1", "tenant_id": "tenant"},
        },
        "infra": {
            "components": [
                {
                    "id": "wireguard-gw",
                    "instance_id": "vpn",
                    "enabled": True,
                    "inputs": {"parent_id": "project", "ssh_user_name": "ubuntu"},
                    "access": {"admin_group_id": "admins"},
                }
            ]
        },
        "apps": {"charts": []},
    }
    materialize_wireguard_dependencies(value)
    return value


def test_wizard_materializes_visible_dedicated_dependency_idempotently(payload):
    assert materialize_wireguard_dependencies(payload) == []
    owner, dependency = payload["infra"]["components"]
    assert owner["access"]["secretstash_ref"] == "mysterybox@vpn-access"
    assert dependency["inputs"] == {
        "parent_id": "project",
        "create_initial_versions": False,
        "secrets": [{"name": name} for name in secret_names("vpn-access").values()],
    }
    assert access_dependencies(payload) == {"vpn": dependency}
    assert payload["apps"]["charts"] == []


@pytest.mark.parametrize(
    "invalid", ["missing", "disabled", "payload", "project", "shared", "names"]
)
def test_dependency_contract_fails_closed(payload, invalid):
    owner, dependency = payload["infra"]["components"]
    if invalid == "missing":
        payload["infra"]["components"].pop()
    elif invalid == "disabled":
        dependency["enabled"] = False
    elif invalid == "payload":
        dependency["inputs"]["secrets"][0]["payload"] = {"KEY": {"type": "text"}}
    elif invalid == "project":
        dependency["inputs"]["parent_id"] = "other"
    elif invalid == "shared":
        other = copy.deepcopy(owner)
        other["instance_id"] = "other"
        payload["infra"]["components"].append(other)
    else:
        dependency["inputs"]["secrets"][0]["name"] = "other"
    with pytest.raises(ValueError):
        access_dependencies(payload)


def test_retained_access_resolves_without_former_network(payload):
    owner = payload["infra"]["components"][0]
    owner["enabled"] = False
    owner["access"].update(
        lifecycle="retained", generation="generation", connection_secret_id="secret"
    )
    owner["bindings"] = {"inputs.subnet_id": "vpc@deleted.subnets[workload].id"}
    assert access_owner_rows(payload) == [owner]
    assert access_dependencies(payload)["vpn"]["instance_id"] == "vpn-access"
    for removed in ({"vpn"}, {"vpn-access"}, {"vpn", "vpn-access"}):
        with pytest.raises(ValueError, match="retained|owner remains"):
            assert_access_removal_allowed(payload, removed)


def test_existing_sdk_generation_cannot_be_materialized(payload):
    owner = payload["infra"]["components"][0]
    owner["access"].pop("secretstash_ref")
    owner["access"].update(generation="generation", connection_secret_id="secret")
    before = copy.deepcopy(payload)
    with pytest.raises(ValueError, match="migration"):
        materialize_wireguard_dependencies(payload)
    assert before == payload


@pytest.mark.parametrize("change", ["none", "delete", "recreate", "payload", "foreign"])
def test_access_plan_admission_prevents_credential_rebinding(payload, change):
    resources = {
        role: "id-" + role
        for role in (
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
    owner = NS(
        owners=access_owner_rows(payload),
        journals={
            "vpn": NS(value={"resources": {key: {"id": value} for key, value in resources.items()}})
        },
        executor=NS(
            manifest={
                "render": {
                    "module_sources": [{"module_name": "vpn_access", "root": "infra-access"}]
                }
            }
        ),
    )
    plan = {
        "planned_values": {"outputs": {"vpn_access_resources": {"value": dict(resources)}}},
        "resource_changes": [],
    }
    if change == "delete":
        plan["resource_changes"] = [
            {
                "type": "nebius_mysterybox_v1_secret",
                "address": 'module.vpn_access.nebius_mysterybox_v1_secret.this["vpn-access-login"]',
                "change": {"actions": ["delete", "create"]},
            }
        ]
    elif change == "recreate":
        plan["planned_values"]["outputs"]["vpn_access_resources"]["value"].pop("login")
    elif change in {"payload", "foreign"}:
        plan["resource_changes"] = [
            {
                "type": "nebius_mysterybox_v1_secret_version"
                if change == "payload"
                else "nebius_mysterybox_v1_secret",
                "address": "module.foreign.secret.test",
                "change": {"actions": ["create"]},
            }
        ]
    if change == "none":
        admit_access_plan(owner, plan)
    else:
        with pytest.raises(RuntimeError):
            admit_access_plan(owner, plan)
    assert "PRIVATE" not in json.dumps(plan)


@pytest.mark.parametrize("change", ["remove", "disable", "rebind"])
def test_render_cannot_drop_existing_access_owner(payload, change):
    from nebius_cxcli.wireguard_dependencies import assert_access_owner_continuity

    before = copy.deepcopy(payload)
    row = payload["infra"]["components"][0]
    if change == "remove":
        payload["infra"]["components"].pop(0)
    elif change == "disable":
        row["enabled"] = False
    else:
        row["access"]["secretstash_ref"] = "mysterybox@other"
    with pytest.raises(ValueError, match="cannot be removed or rebound"):
        assert_access_owner_continuity(before, payload)


@pytest.mark.parametrize("existing", ["container", "iam", "output"])
def test_absent_journal_cannot_adopt_access_state(payload, existing):
    from nebius_cxcli.wireguard_foundation import assert_fresh_access_state

    state = {"values": {"root_module": {"child_modules": []}, "outputs": {}}}
    if existing == "output":
        state["values"]["outputs"]["vpn_access_resources"] = {"value": {"login": "id"}}
    else:
        module = "vpn_access" if existing == "container" else "vpn_access_iam"
        state["values"]["root_module"]["child_modules"] = [
            {"resources": [{"address": f"module.{module}.resource.example"}]}
        ]
    owner = NS(
        executor=NS(
            paths=NS(access_infra_dir="access"),
            runtime_env={},
            manifest={
                "render": {
                    "module_sources": [
                        {"module_name": module, "instance_id": "vpn-access", "root": "infra-access"}
                        for module in ("vpn_access", "vpn_access_iam")
                    ]
                }
            },
            cli=NS(terraform_show_json=lambda *a, **kw: state),
        )
    )
    with pytest.raises(RuntimeError, match="no ownership journal"):
        assert_fresh_access_state(owner, payload["infra"]["components"][0])


def test_foundation_applies_once_and_verifies_without_persisting_payloads(payload, tmp_path):
    from nebius_cxcli.wireguard_foundation import reconcile_access_root

    root = tmp_path / "infra-access"
    root.mkdir()
    (root / "main.tf").write_text("# fixture")
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
    events = []
    applied = False
    journal = NS(
        value={},
        project="project",
        assert_held=lambda: events.append("journal-lock"),
        save=lambda: events.append("save"),
    )

    def plan(*args, plan_file, **kwargs):
        plan_file.write_bytes(b"fixture-saved-plan")
        events.append("plan")

    def show(*args, **kwargs):
        return {
            "planned_values": {"outputs": {"vpn_access_resources": {"value": resources}}},
            "resource_changes": []
            if applied
            else [
                {
                    "type": "nebius_mysterybox_v1_secret",
                    "address": 'module.vpn_access.nebius_mysterybox_v1_secret.this["vpn-access-login"]',
                    "change": {"actions": ["create"]},
                }
            ],
        }

    def apply(*args, abort_check, expected_plan_sha256, **kwargs):
        nonlocal applied
        abort_check()
        assert expected_plan_sha256.startswith("sha256:")
        applied = True
        events.append("apply")

    owner = NS(
        owners=access_owner_rows(payload),
        journals={"vpn": journal},
        api=NS(verify_access_resources=lambda *a, **kw: events.append("sdk-readback")),
        executor=NS(
            paths=NS(access_infra_dir=root),
            runtime_env={},
            lease=NS(assert_held=lambda: events.append("project-lock")),
            manifest={
                "render": {
                    "module_sources": [{"module_name": "vpn_access", "root": "infra-access"}]
                }
            },
            cli=NS(
                progress_console=NS(print=lambda *a: None),
                terraform_plan=plan,
                terraform_show_json=show,
                terraform_apply=apply,
                terraform_output_json=lambda *a, **kw: {
                    "vpn_access_resources": {"value": resources}
                },
            ),
        ),
    )
    reconcile_access_root(owner)
    first = copy.deepcopy(journal.value)
    reconcile_access_root(owner, verify_only=True)
    assert events.count("apply") == 1 and events.count("sdk-readback") == 2
    assert events.index("apply") < events.index("sdk-readback") < events.index("save")
    assert (
        journal.value
        == first
        == {"resources": {key: {"id": value} for key, value in resources.items()}}
    )


def test_both_roots_are_frozen_and_recovered(payload, tmp_path, monkeypatch):
    from nebius_cxcli.deployment_state import DeploymentGeneration
    from nebius_cxcli.generated_manifest import build_generated_manifest, validate_manifest_contract
    from nebius_cxcli.paths import resolve_project_paths

    path = tmp_path / "deployments/tenant/example/config.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("version: v2\n")
    paths = resolve_project_paths(path)
    for root in paths.terraform_dirs:
        root.mkdir(parents=True)
        (root / "main.tf").write_text(f"# {root.name}\n")
        (root / ".terraform.lock.hcl").write_text("# fixture lock\n")
    manifest = build_generated_manifest(
        config=payload,
        paths=paths,
        targets=[],
        required_component_outputs=[],
        access_tfvars={"public": "id"},
    )
    # Validation uses the frozen backend even if a shell override changes later.
    monkeypatch.setenv("NEBIUS_CXCLI_TFSTATE_OBJECT_KEY", "different.tfstate")
    validate_manifest_contract(manifest)
    generation = DeploymentGeneration.capture(paths, manifest)
    assert {"infra/main.tf", "infra-access/main.tf", "infra-access/.terraform.lock.hcl"} <= set(
        generation.files
    )
    recovered = resolve_project_paths(tmp_path / "recovery/tenant/example/config.yaml")
    result = generation.materialize(recovered)
    assert (recovered.access_infra_dir / "main.tf").read_text() == "# infra-access\n"
    assert result["execution"]["terraform_roots"] == manifest["execution"]["terraform_roots"]


def test_outputs_merge_both_roots_without_identity_collisions(payload, tmp_path):
    from nebius_cxcli.paths import resolve_project_paths
    from nebius_cxcli.terraform_roots import merged_outputs

    paths = resolve_project_paths(tmp_path / "deployments/tenant/example/config.yaml")
    cli = NS(terraform_output_json=lambda root, **kw: {root.name: {"value": root.name}})
    assert set(merged_outputs(cli, paths, payload)) == {"infra", "infra-access"}
    cli.terraform_output_json = lambda root, **kw: {"duplicate": {"value": root.name}}
    with pytest.raises(RuntimeError, match="duplicate"):
        merged_outputs(cli, paths, payload)


@pytest.mark.parametrize("entrypoint", ["transaction", "promotion"])
def test_render_entrypoints_preserve_previous_access_ownership(payload, tmp_path, entrypoint):
    from nebius_cxcli.generated_manifest import manifest_path_for_generated_dir
    from nebius_cxcli.paths import resolve_project_paths
    from nebius_cxcli.render import (
        build_project_generation_plan,
        promote_staged_generated_paths,
        staged_generated_paths,
    )

    path = tmp_path / "deployments/tenant/example/config.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("version: v2\n")
    paths = resolve_project_paths(path)
    paths.generated_dir.mkdir()
    old_manifest = manifest_path_for_generated_dir(paths.generated_dir)
    old_manifest.write_text(json.dumps({"runtime_config": payload}))
    original = old_manifest.read_bytes()
    staged = staged_generated_paths(paths)
    current = copy.deepcopy(payload)
    current["infra"]["components"].pop(0)
    manifest_path_for_generated_dir(staged.generated_dir).write_text(
        json.dumps({"runtime_config": current})
    )
    with pytest.raises(ValueError, match="cannot be removed or rebound"):
        if entrypoint == "transaction":
            build_project_generation_plan(
                final_paths=paths,
                staged_paths=staged,
                config_path=path,
                config_content="version: v2\n",
            )
        else:
            promote_staged_generated_paths(staged, paths)
    assert old_manifest.read_bytes() == original


def test_skipping_fresh_gateway_removes_only_its_new_access_dependency():
    from nebius_cxcli import cli

    payload = {
        "client_info": {"nebius": {"project_id": "project"}},
        "infra": {
            "components": [
                {"id": "wireguard-gw", "instance_id": "vpn", "enabled": True, "inputs": {}},
                {"id": "mysterybox", "instance_id": "existing", "enabled": True, "inputs": {}},
            ]
        },
    }
    materialize_wireguard_dependencies(payload, selected_instances={"vpn"})
    removed = cli._remove_skipped_component_add_infra_rows(
        payload=payload,
        skipped_components={("infra", "wireguard-gw", "vpn")},
        added_infra_labels=["wireguard-gw@vpn", "mysterybox@vpn-access"],
        infra_entries=(),
    )
    assert [row["instance_id"] for row in payload["infra"]["components"]] == ["existing"]
    assert set(removed[0]) == {"wireguard-gw@vpn", "mysterybox@vpn-access"}


@pytest.mark.parametrize("protected", ["existing", "generation", "retained", "shared"])
def test_skip_cleanup_preserves_nonfresh_or_shared_access(payload, protected):
    from nebius_cxcli.wireguard_dependencies import skipped_new_dependencies

    owner, dependency = payload["infra"]["components"]
    added = {"vpn-access"}
    if protected == "existing":
        added.clear()
    elif protected == "generation":
        owner["access"]["generation"] = "generation"
    elif protected == "retained":
        owner["access"]["lifecycle"] = "retained"
    else:
        payload["infra"]["components"].append({**owner, "instance_id": "other"})
    assert skipped_new_dependencies(payload, {"vpn"}, added) == set()
    assert dependency in payload["infra"]["components"]


@pytest.mark.parametrize("length", [56, 57, 63])
def test_generated_access_identity_fits_instance_contract(length):
    from nebius_cxcli.component_instances import INSTANCE_ID_PATTERN

    owner = "a" * length
    payload = {
        "client_info": {"nebius": {"project_id": "project"}},
        "infra": {
            "components": [
                {"id": "wireguard-gw", "instance_id": owner, "enabled": True, "inputs": {}}
            ]
        },
    }
    materialize_wireguard_dependencies(payload)
    dependency = payload["infra"]["components"][1]["instance_id"]
    assert INSTANCE_ID_PATTERN.fullmatch(dependency)
    if length == 56:
        assert dependency == owner + "-access"
    assert materialize_wireguard_dependencies(payload) == []


def test_long_dependency_names_are_distinct_and_collision_is_not_adopted():
    def config(owner):
        return {
            "client_info": {"nebius": {"project_id": "project"}},
            "infra": {
                "components": [
                    {"id": "wireguard-gw", "instance_id": owner, "enabled": True, "inputs": {}}
                ]
            },
        }

    first, second = config("a" * 62 + "b"), config("a" * 62 + "c")
    materialize_wireguard_dependencies(first)
    materialize_wireguard_dependencies(second)
    assert (
        first["infra"]["components"][1]["instance_id"]
        != second["infra"]["components"][1]["instance_id"]
    )
    collision = config("a" * 62 + "b")
    collision["infra"]["components"].append(copy.deepcopy(first["infra"]["components"][1]))
    with pytest.raises(ValueError, match="already exists"):
        materialize_wireguard_dependencies(collision)
    assert "secretstash_ref" not in collision["infra"]["components"][0].get("access", {})
