from __future__ import annotations

import base64
import copy
from types import SimpleNamespace

import pytest

from nebius_cxcli import cli
from nebius_cxcli.deployment_campaign import TerraformCampaignHooks
from nebius_cxcli.deployment_plan import plan_deployment
from nebius_cxcli.deployment_recovery import capture_execution_cache, restore_execution_cache
from nebius_cxcli.deployment_state import DeploymentGeneration
from nebius_cxcli.deployment_workflow import StageAdmission
from nebius_cxcli.paths import ProjectPaths
from test_deployment_plan import config
from test_operation_config_authority import _Store


def paths(root):
    project = root / "tenant" / "project"
    project.mkdir(parents=True)
    return ProjectPaths(
        project / "config.yaml",
        root,
        root,
        project,
        project / "generated",
        project / "generated/infra",
        project / "generated/flux",
        project / "generated/reports",
        "tenant",
        "project",
    )


def generation(payload, content="initial"):
    return DeploymentGeneration(
        {
            "schema": "nebius-cxcli-generated/v2",
            "execution": {"backend": {}},
            "runtime_config": payload,
            "deploy": {"targets": []},
        },
        {"infra/main.tf": base64.b64encode(content.encode()).decode()},
    )


def fixture(tmp_path):
    source, desired = config(), config()
    desired["infra"]["components"][0]["inputs"]["node_groups"]["worker-a"]["node_count"] = 1
    del desired["infra"]["components"][0]["inputs"]["node_groups"]["worker-b"]
    desired["infra"]["components"][0]["inputs"]["node_groups"]["worker-c"] = {
        "node_count": 2,
        "os": "new",
    }
    plan = plan_deployment(
        desired, accepted=source, live_release="1.22.3", infrastructure_absent=False
    )
    local = paths(tmp_path / "runner-a")
    manifest = generation(source).materialize(local)
    local.config_path.write_text(cli.render_updated_source_payload(source))
    executor = SimpleNamespace(cli=cli, plan=plan, manifest=manifest, paths=local, runtime_env={})
    executor.campaign_application_generation = lambda name, frozen, **kwargs: frozen
    hooks = TerraformCampaignHooks(executor, source=source, generation=generation(desired))
    hooks.admissions = {
        stage.name.value: StageAdmission(
            stage,
            {},
            {
                "generation": generation(stage.config, stage.name.value).as_payload(),
                "generationId": generation(stage.config, stage.name.value).identity,
            },
        )
        for stage in plan.stages
    }
    return hooks


def test_stage_publication_recovers_lost_ack_and_reconstructs_on_another_runner(tmp_path):
    hooks = fixture(tmp_path)
    store = _Store(fail_first_applied_record=True)
    with pytest.raises(RuntimeError, match="receipt write interruption"):
        hooks._publish("retire", config_store=store, assert_authority=lambda: None)
    assert (hooks.executor.paths.infra_dir / "main.tf").read_text() == "retire"
    assert store.transitions["deployment:retire"].status == "planned"
    cache = capture_execution_cache(hooks.executor.paths.repo_root)
    restored_paths = paths(tmp_path / "runner-b")
    restore_execution_cache(restored_paths.repo_root, cache)
    hooks.executor.paths = restored_paths
    hooks._reload()
    hooks._publish("retire", config_store=store, assert_authority=lambda: None)
    assert store.transitions["deployment:retire"].status == "applied"
    hooks._publish("grow", config_store=store, assert_authority=lambda: None)
    assert (restored_paths.infra_dir / "main.tf").read_text() == "grow"
    # An old stage cannot overwrite a later committed generation.
    with pytest.raises(RuntimeError):
        hooks._publish("retire", config_store=store, assert_authority=lambda: None)


def test_stage_publication_exports_only_source_rows_from_runtime(tmp_path):
    from dataclasses import replace

    import yaml

    from nebius_cxcli.config_model import to_runtime_payload

    hooks = fixture(tmp_path)
    admitted = hooks.admissions["retire"]
    runtime = to_runtime_payload(admitted.stage.config)
    frozen = generation(runtime, "retire")
    hooks.admissions["retire"] = replace(
        admitted,
        stage=replace(admitted.stage, config=runtime),
        application={"generation": frozen.as_payload(), "generationId": frozen.identity},
    )
    hooks._publish("retire", config_store=_Store(), assert_authority=lambda: None)
    written = yaml.safe_load(hooks.executor.paths.config_path.read_text())
    expected = {"version": "v1", "deploy": {}, **admitted.stage.config}
    assert written == expected
    assert "target_ref" not in written["apps"]["charts"][0]
    assert set(written["apps"]) == {"charts"}
    assert set(written["infra"]) == {"components"}


def test_retirement_refresh_cannot_apply_after_losing_authority(tmp_path, monkeypatch):
    hooks = fixture(tmp_path)
    changed = {
        "resource_changes": [
            {
                "address": "module.cluster.group",
                "mode": "managed",
                "type": "nebius_mk8s_v1_node_group",
                "change": {"actions": ["delete"], "after": None},
            }
        ]
    }
    report = {"admitted": True, "matrix_sha256": "frozen", "rows": []}
    hooks.executor.compatibility_report = report
    hooks.executor.preflight = lambda: None
    hooks.admissions["retire"] = StageAdmission(
        hooks.admissions["retire"].stage, changed, {"compatibilityAdmission": report}
    )
    monkeypatch.setattr(hooks, "_publish", lambda *a, **kw: None)
    monkeypatch.setattr(hooks, "_quiescence", lambda **kw: None)
    monkeypatch.setattr(cli, "terraform_init", lambda *a, **kw: None)
    held = True

    def freeze(**kwargs):
        nonlocal held
        held = False

    monkeypatch.setattr(hooks, "_freeze_retirement", freeze)
    hooks.executor._terraform_plan = lambda **kwargs: (None, copy.deepcopy(changed))
    monkeypatch.setattr(
        cli,
        "_run_terraform_apply_with_status",
        lambda *a, **kw: pytest.fail("lost lease must prevent apply"),
    )

    def authority():
        if not held:
            raise RuntimeError("lost fence")

    with pytest.raises(RuntimeError, match="lost fence"):
        hooks.execute(
            "retire",
            intent=object(),
            assert_authority=authority,
            config_store=object(),
            kube_env={},
            kube_context="ctx",
            namespace="slurm",
        )


def test_campaign_delegates_initialization_to_stage_preflight(tmp_path, monkeypatch):
    hooks = fixture(tmp_path)
    calls = []
    monkeypatch.setattr(hooks, "_publish", lambda *a, **kw: None)
    monkeypatch.setattr(hooks, "_quiescence", lambda **kw: None)
    monkeypatch.setattr(hooks, "_dependencies", lambda *a: (None, ()))
    monkeypatch.setattr(cli, "terraform_init", lambda *a, **kw: calls.append("init"))

    class PreflightComplete(Exception):
        pass

    def preflight():
        calls.append("preflight")
        cli.terraform_init(hooks.executor.paths.infra_dir, extra_env=hooks.executor.runtime_env)
        raise PreflightComplete()

    hooks.executor.preflight = preflight
    with pytest.raises(PreflightComplete):
        hooks.execute(
            "retire",
            intent=object(),
            assert_authority=lambda: None,
            config_store=object(),
            kube_env={},
            kube_context="ctx",
            namespace="slurm",
        )
    assert calls == ["preflight", "init"]


def test_growth_binds_only_new_provider_ids_and_keeps_downsized_survivor(tmp_path, monkeypatch):
    hooks = fixture(tmp_path)
    ids = {"worker-a": "a", "worker-b": "b"}
    monkeypatch.setattr(hooks, "_owned_ids", lambda: ids.copy())
    hooks.bind_source_inventory(
        [SimpleNamespace(metadata=SimpleNamespace(id=id)) for id in ids.values()],
        None,
        compatibility_lookup=lambda **kw: [
            SimpleNamespace(os=os, drivers_preset="") for os in ("old", "new")
        ],
    )
    assert hooks.contract["retired_group_ids"] == ["b"]
    assert hooks.contract["remaining_group_ids"] == ["a"]
    assert hooks.contract["added_group_keys"] == ["worker-c"]
    ids.clear()
    ids.update({"worker-a": "a", "worker-c": "c"})
    assert hooks.newly_owned_ids() == {"worker-c": "c"}
    from test_soperator_full_stack_upgrade import _intent

    final = hooks.final_intent(_intent())
    assert {row.key: row.provider_id for row in final.node_groups} == ids
    assert {row.group_key for row in final.compatibility_rows} == set(ids)
    ids["worker-c"] = "b"
    assert hooks.newly_owned_ids() == {}


def test_onboarded_hook_publishes_desired_values_inside_parent_maintenance(tmp_path, monkeypatch):
    from nebius_cxcli.deployment_campaign import ApplicationCampaignHooks

    old = config()
    old["infra"]["components"] = []
    desired = copy.deepcopy(old)
    desired["apps"]["charts"][0]["values"] = {"settings": {"new": 2}}
    local = paths(tmp_path / "onboarded")
    manifest = generation(old).materialize(local)
    local.config_path.write_text(cli.render_updated_source_payload(old))
    plan = plan_deployment(
        desired, accepted=old, live_release="1.22.3", infrastructure_absent=False
    )
    executor = SimpleNamespace(cli=cli, plan=plan, paths=local, manifest=manifest)
    executor.campaign_application_generation = lambda name, frozen, **kwargs: frozen
    hook = ApplicationCampaignHooks(executor, source=old, generation=generation(desired))
    hook.admissions = {
        "reconcile": StageAdmission(
            plan.stages[-1],
            {},
            {
                "generation": generation(desired).as_payload(),
                "generationId": generation(desired).identity,
            },
        )
    }
    parent = SimpleNamespace(ownership="onboarded", backend="provider-api")
    calls = []

    def release(**kwargs):
        assert cli.to_plain_data(executor.config)["apps"]["charts"][0]["values"] == {
            "settings": {"new": 2}
        }
        assert kwargs["campaign"][0] is parent
        calls.append("release")

    executor._release = release
    executor.prepare_campaign_applications = lambda **kw: calls.append("prerequisites")
    executor.apply_admitted_project_terraform = lambda *args: calls.append("project-terraform")
    executor.verify_soperator_desired = lambda **kw: calls.append("verify") or {"ready": True}
    monkeypatch.setattr(
        cli,
        "terraform_show_json",
        lambda *a, **kw: pytest.fail("onboarded inventory must not discover Terraform ownership"),
    )
    hook.bind_source_inventory(
        [SimpleNamespace(metadata=SimpleNamespace(id="registered-id"))],
        None,
        compatibility_lookup=lambda **kw: [],
    )
    assert hook.contract["remaining_group_ids"] == ["registered-id"]
    assert hook.contract["retired_group_ids"] == []
    result = hook.execute(
        "final-reconcile",
        intent=parent,
        assert_authority=lambda: None,
        config_store=_Store(),
        kube_env={},
        kube_context="ctx",
        namespace="slurm",
    )
    assert calls == ["project-terraform", "prerequisites", "release"]
    assert hook.verify_desired(kube_env={}) == {"ready": True}
    assert calls[-1] == "verify"
    assert result.evidence["generation"] == generation(desired).identity
    assert hook.final_intent(parent) is parent
