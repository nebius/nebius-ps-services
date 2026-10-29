from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from nebius_cxcli import cli
from nebius_cxcli.quota_checks import QuotaCheck, QuotaContributor, QuotaReport

GIB = 1024**3


def inputs(size=2048):
    return {
        "client_info": {"nebius": {"project_id": "project", "region_id": "region"}},
        "infra": {
            "components": [
                {
                    "id": "sfs",
                    "instance_id": "storage",
                    "enabled": True,
                    "inputs": {
                        "parent_id": "project",
                        "name": "storage",
                        "type": "NETWORK_SSD",
                        "size_gib": size,
                    },
                }
            ]
        },
    }


def manifest(config=None):
    return {
        "runtime_config": deepcopy(inputs() if config is None else config),
        "render": {
            "module_sources": [
                {
                    "component_id": "sfs",
                    "instance_id": "storage",
                    "module_name": "storage",
                    "source": "./sfs",
                    "source_ref": "local",
                    "resolved_source": "./sfs",
                }
            ]
        },
        "schema": "nebius-cxcli-generated/v3",
        "execution": {
            "backend": {},
            "targets": [],
            "required_component_outputs": [],
            "status_watchers": [],
            "validations": [],
        },
    }


def plan(*, old=2048, new=2048, actions=("no-op",)):
    before = {
        "id": "filesystem-id",
        "parent_id": "project",
        "name": "storage",
        "type": "NETWORK_SSD",
        "size_bytes": old * GIB,
    }
    after = {**before, "size_bytes": new * GIB}
    return {
        "resource_changes": [
            {
                "address": 'module.storage.nebius_compute_v1_filesystem.this["default"]',
                "mode": "managed",
                "type": "nebius_compute_v1_filesystem",
                "change": {
                    "actions": list(actions),
                    "before": before,
                    "after": after,
                    "after_unknown": {},
                },
            }
        ]
    }


@pytest.fixture(params=["deploy", "validate", "quota check", "quota request", "render"])
def quota_phase(request):
    return request.param


def run_gate(
    monkeypatch,
    tmp_path,
    *,
    phase="deploy",
    size=2048,
    available=1024,
    payload=None,
    fail_plan=False,
    missing_artifact=None,
    generated_evidence="matching",
    plan_calls=None,
):
    config = inputs(size)
    generated = manifest(config)
    if generated_evidence == "missing-snapshot":
        generated.pop("runtime_config")
    elif generated_evidence == "wrong-scope":
        generated["runtime_config"]["client_info"]["nebius"]["project_id"] = "other"
    elif generated_evidence == "edited-block-size":
        config["infra"]["components"][0]["inputs"]["block_size_kib"] = 8
    elif generated_evidence == "duplicate-instance":
        generated["runtime_config"]["infra"]["components"] *= 2
    elif generated_evidence == "unrelated-edit":
        config["infra"]["components"].append({"id": "mk8s", "enabled": False, "inputs": {}})
    required = size * GIB
    check = QuotaCheck(
        component_id="sfs",
        instance_id="storage",
        component_label="storage",
        quota_name="compute.filesystem.size.network-ssd",
        region="region",
        required=required,
        reason="shared filesystem",
        unit="byte",
        available=available * GIB,
        sufficient=required <= available * GIB,
        tenant_limit=(available + 2048) * GIB,
        tenant_usage=2048 * GIB,
        project_limit=None,
        project_usage=None,
        source_scope="tenant",
        description="storage",
        contributors=(QuotaContributor("sfs", "storage", "storage", required, "storage"),),
    )
    report = QuotaReport(
        tenant_id="tenant",
        project_id="project",
        region_id="region",
        checked_at="now",
        checks=(check,),
    )
    monkeypatch.setattr(cli, "terraform_init", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "_assess_live_quota_report", lambda *a, **kw: report)
    monkeypatch.setattr(
        cli, "_managed_mk8s_quota_requirements_from_terraform_state", lambda *a, **kw: ()
    )
    calls = [] if plan_calls is None else plan_calls

    def make_plan(*args, **kwargs):
        calls.append(kwargs)
        if fail_plan:
            raise RuntimeError("private provider diagnostics")
        kwargs["plan_file"].write_bytes(b"private saved plan")

    monkeypatch.setattr(cli, "terraform_plan", make_plan)
    monkeypatch.setattr(cli, "terraform_show_json", lambda *a, **kw: payload or plan())
    paths = SimpleNamespace(
        infra_dir=tmp_path if missing_artifact != "infra" else tmp_path / "absent",
        generated_dir=tmp_path,
        config_path=tmp_path / "config.yaml",
    )
    if phase == "deploy":
        result = cli._raise_on_generated_bundle_live_quota_issues(
            config, paths, manifest=generated, runtime_env={}, phase=phase
        )
    else:
        if missing_artifact != "manifest":
            cli.manifest_path_for_generated_dir(tmp_path).write_text("{}")
        monkeypatch.setattr(cli, "load_generated_manifest", lambda *a: generated)
        monkeypatch.setattr(cli, "_ensure_runtime_auth_material", lambda *a, **kw: None)
        monkeypatch.setattr(cli, "_ensure_backend_s3_env_aliases", lambda: None)
        monkeypatch.setattr(cli, "_terraform_runtime_env", lambda *a: {})
        result = cli._raise_on_config_live_quota_issues(config, paths, phase=phase)
    assert len(calls) == 1
    assert not calls[0]["plan_file"].exists()
    return result


def test_existing_two_tib_filesystem_does_not_need_two_tib_free(monkeypatch, tmp_path, quota_phase):
    assert not run_gate(monkeypatch, tmp_path, phase=quota_phase).checks


def test_expansion_checks_only_growth(monkeypatch, tmp_path, quota_phase):
    report = run_gate(
        monkeypatch,
        tmp_path,
        phase=quota_phase,
        size=3072,
        payload=plan(new=3072, actions=("update",)),
    )
    assert report.checks[0].required == 1024 * GIB


@pytest.mark.parametrize("actions", [("create",), ("create", "delete"), ("delete", "create")])
def test_new_and_replacement_filesystems_still_need_full_quota(
    monkeypatch, tmp_path, actions, quota_phase
):
    with pytest.raises(RuntimeError, match="quota.*is insufficient"):
        run_gate(monkeypatch, tmp_path, phase=quota_phase, payload=plan(actions=actions))


def test_excess_growth_still_blocks(monkeypatch, tmp_path, quota_phase):
    with pytest.raises(RuntimeError, match="quota.*is insufficient"):
        run_gate(
            monkeypatch,
            tmp_path,
            phase=quota_phase,
            size=4096,
            payload=plan(new=4096, actions=("update",)),
        )


def test_failed_plan_does_not_suppress_quota_or_leak_diagnostics(
    monkeypatch, tmp_path, capsys, quota_phase
):
    with pytest.raises(RuntimeError, match="quota.*is insufficient"):
        run_gate(monkeypatch, tmp_path, phase=quota_phase, fail_plan=True)
    assert "private provider diagnostics" not in capsys.readouterr().out
    assert not list(tmp_path.rglob("*.tfplan"))


def test_stale_generated_size_does_not_earn_credit(monkeypatch, tmp_path, quota_phase):
    with pytest.raises(RuntimeError, match="quota.*is insufficient"):
        run_gate(monkeypatch, tmp_path, phase=quota_phase, size=3072, payload=plan())


@pytest.mark.parametrize("missing_artifact", ["infra", "manifest"])
def test_source_quota_without_generated_artifacts_checks_full_demand(
    monkeypatch, tmp_path, missing_artifact
):
    with pytest.raises(RuntimeError, match="quota.*is insufficient"):
        run_gate(monkeypatch, tmp_path, phase="validate", missing_artifact=missing_artifact)


@pytest.mark.parametrize(
    "generated_evidence",
    ["missing-snapshot", "wrong-scope", "edited-block-size", "duplicate-instance"],
)
def test_quota_requires_matching_generated_filesystem_inputs(
    monkeypatch, tmp_path, generated_evidence, quota_phase
):
    calls = []
    with pytest.raises(RuntimeError, match="quota.*is insufficient"):
        run_gate(
            monkeypatch,
            tmp_path,
            phase=quota_phase,
            generated_evidence=generated_evidence,
            plan_calls=calls,
        )
    assert not calls


def test_unrelated_config_edits_preserve_filesystem_credit(monkeypatch, tmp_path, quota_phase):
    assert not run_gate(
        monkeypatch, tmp_path, phase=quota_phase, generated_evidence="unrelated-edit"
    ).checks


@pytest.mark.parametrize("other_state", ["new", "edited"])
def test_unmatched_filesystem_preserves_other_retained_credit(tmp_path, other_state):
    from nebius_cxcli.retained_quota import planned_retained_quota_credit

    config = inputs()
    generated = manifest(config)
    other = deepcopy(config["infra"]["components"][0])
    other["instance_id"] = "other"
    other["inputs"].update(name="other", size_gib=1024)
    config["infra"]["components"].append(other)
    payload = plan()
    if other_state == "edited":
        generated["runtime_config"]["infra"]["components"].append(deepcopy(other))
        other["inputs"]["block_size_kib"] = 8
        source = deepcopy(generated["render"]["module_sources"][0])
        source.update(instance_id="other", module_name="other")
        generated["render"]["module_sources"].append(source)
        resource = plan(old=1024, new=1024)["resource_changes"][0]
        resource["address"] = resource["address"].replace("module.storage.", "module.other.")
        payload["resource_changes"].append(resource)
    calls = []

    def make_plan(*args, **kwargs):
        calls.append(kwargs["plan_file"])
        kwargs["plan_file"].write_bytes(b"private plan")

    credits = planned_retained_quota_credit(
        config,
        tmp_path,
        generated,
        runtime_env={},
        plan_runner=make_plan,
        plan_reader=lambda *a, **kw: payload,
        warn=lambda message: None,
    )
    assert {credit.instance_id for credit in credits} == {"storage"}
    byte_credit = sum(credit.required for credit in credits if ".size." in credit.quota_name)
    assert (2048 + 1024) * GIB - byte_credit == 1024 * GIB
    assert len(calls) == 1 and not calls[0].exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-module",
        "duplicate",
        "missing-id",
        "wrong-parent",
        "changed-type",
        "unknown-size",
        "unknown-id",
        "wrong-size",
        "missing-changes",
        "data",
        "missing-module",
    ],
)
def test_ambiguous_or_unbound_plan_earns_no_credit(mutation):
    from nebius_cxcli.retained_quota import retained_filesystem_quota

    p = plan()
    sources = manifest()["render"]["module_sources"]
    r = p["resource_changes"][0]
    if mutation == "wrong-module":
        r["address"] = r["address"].replace("module.storage.", "module.other.")
    elif mutation == "duplicate":
        p["resource_changes"].append(deepcopy(r))
    elif mutation == "missing-id":
        r["change"]["before"].pop("id")
    elif mutation == "wrong-parent":
        r["change"]["before"]["parent_id"] = "other"
    elif mutation == "changed-type":
        r["change"]["after"]["type"] = "NETWORK_HDD"
    elif mutation == "unknown-size":
        r["change"]["after"]["size_bytes"] = None
    elif mutation == "unknown-id":
        r["change"]["after_unknown"]["id"] = True
    elif mutation == "wrong-size":
        r["change"]["after"]["size_bytes"] = GIB
    elif mutation == "missing-changes":
        p = {}
    elif mutation == "data":
        r["mode"] = "data"
    else:
        sources = []
    assert retained_filesystem_quota(inputs(), sources, p) == ()


def test_count_and_bytes_credit_are_isolated_to_retained_instance():
    from nebius_cxcli.retained_quota import retained_filesystem_quota

    c = inputs()
    c["infra"]["components"].append({**deepcopy(c["infra"]["components"][0]), "instance_id": "new"})
    credits = retained_filesystem_quota(c, manifest()["render"]["module_sources"], plan())
    assert {r.instance_id for r in credits} == {"storage"}
    assert {r.quota_name: r.required for r in credits} == {
        "compute.filesystem.count": 1,
        "compute.filesystem.size.network-ssd": 2048 * GIB,
    }
