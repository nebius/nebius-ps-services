import copy

import pytest
import yaml

from nebius_cxcli import cli
from nebius_cxcli.components import component_entries, soperator_install_entry
from nebius_cxcli.soperator_config_materialization import _materialize_soperator_component_defaults
from nebius_cxcli.soperator_values import explicit_values, soperator_rows


@pytest.fixture(autouse=True)
def confirm_fast(monkeypatch):
    monkeypatch.setattr("typer.confirm", lambda *a, **kw: True)


def payload_for(count=1):
    payload = cli._starter_component_payload(
        client_name="example",
        tenant_id="tenant-example",
        project_id="project-example",
        region_id="eu-north1",
        email=None,
        selected_infra={"mk8s", "sfs"},
        selected_apps={"soperator"},
        infra_entries=component_entries("infra"),
        app_entries=(
            soperator_install_entry("4.1.9", chart_repo="oci://example.invalid/soperator"),
        ),
    )
    payload = yaml.safe_load(yaml.safe_dump(payload))
    set_count(payload, count)
    return payload


def set_count(payload, count):
    inputs = next(row["inputs"] for row in payload["infra"]["components"] if row["id"] == "mk8s")
    preset = "1gpu-16vcpu-200gb" if count == 1 else "8gpu-128vcpu-1600gb"
    inputs["node_group_defaults"]["gpu"].update(platform="gpu-h100-sxm", preset=preset)


def run_wizard(payload, selected_apps=None, selected_infra=None):
    result, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload),
        selected_infra=selected_infra or set(),
        selected_apps={"soperator"} if selected_apps is None else selected_apps,
        infra_entries=(),
        app_entries=(),
    )
    assert completed
    return yaml.safe_load(result)


def test_wizard_persists_profile_after_effective_worker_materialization():
    payload = payload_for(1)
    result = run_wizard(payload)
    row = soperator_rows(result)[0]
    assert row["values"]["deploymentProfile"] == "fast-dev-test"
    assert explicit_values(row)["deploymentProfile"] == "fast-dev-test"
    _materialize_soperator_component_defaults(result)
    _materialize_soperator_component_defaults(result)
    assert row["values"]["deploymentProfile"] == "fast-dev-test"
    assert (
        next(n for n in row["values"]["nodesets"] if n["gpu"]["enabled"])["slurmd"]["resources"][
            "gpu"
        ]
        == 1
    )


@pytest.mark.parametrize("answer,profile", [(True, "fast-dev-test"), (False, "standard")])
def test_wizard_uses_shared_profile_prompt_once(monkeypatch, answer, profile):
    choices = []

    def choose():
        choices.append(answer)
        return answer

    monkeypatch.setattr(cli, "_prompt_fast_deploy_profile", choose)
    result = run_wizard(payload_for(1))
    assert soperator_rows(result)[0]["values"]["deploymentProfile"] == profile
    assert soperator_rows(run_wizard(result))[0]["values"]["deploymentProfile"] == profile
    assert choices == [answer]


def test_profile_is_independent_of_hardware_backtracking():
    payload = run_wizard(payload_for(1))
    for count in (8, 1):
        set_count(payload, count)
        payload = run_wizard(payload)
        assert soperator_rows(payload)[0]["values"]["deploymentProfile"] == "fast-dev-test"


def test_explicit_standard_is_preserved_by_wizard():
    payload = payload_for(8)
    soperator_rows(payload)[0]["values"]["deploymentProfile"] = "standard"
    assert soperator_rows(run_wizard(payload))[0]["values"]["deploymentProfile"] == "standard"


def test_generic_reconciliation_without_chooser_preserves_fast_fallback():
    payload = payload_for(8)
    notices = cli.reconcile_wizard_deployment(payload, target_refs={"mk8s"})
    assert notices == {"mk8s": "fast-dev-test"}
    assert explicit_values(soperator_rows(payload)[0])["deploymentProfile"] == "fast-dev-test"


def test_unselected_soperator_target_unchanged():
    payload = payload_for(1)
    before = copy.deepcopy(soperator_rows(payload))
    result = run_wizard(payload, selected_apps={"another-app"})
    assert soperator_rows(result) == before


def test_wizard_abort_does_not_persist_profile(monkeypatch):
    payload = payload_for(1)
    before = copy.deepcopy(payload)
    monkeypatch.setattr("typer.confirm", lambda *a, **kw: (_ for _ in ()).throw(cli.typer.Abort()))
    with pytest.raises(cli.typer.Abort):
        run_wizard(payload)
    assert payload == before


def test_component_add_scope_preserves_other_target():
    payload = payload_for(1)
    other = copy.deepcopy(soperator_rows(payload)[0])
    other["instance_id"] = "unrelated"
    other["target_ref"] = "unrelated"
    payload["apps"]["charts"].append(other)
    before = copy.deepcopy(other)
    result = run_wizard(payload, selected_apps={"soperator@mk8s"})
    rows = soperator_rows(result)
    assert rows[0]["values"]["deploymentProfile"] == "fast-dev-test"
    assert rows[1] == before


def test_infrastructure_only_wizard_selection_updates_bound_soperator():
    result = run_wizard(payload_for(1), selected_apps=set(), selected_infra={"mk8s"})
    assert soperator_rows(result)[0]["values"]["deploymentProfile"] == "fast-dev-test"


def test_final_notice_explains_reduced_coverage(capsys):
    run_wizard(payload_for(1))
    output = " ".join(capsys.readouterr().out.split())
    assert "Dev/Test only" in output
    assert "qualification are disabled" in output
    assert "Slurm readiness and a test job are verified" in output


def test_single_gpu_prompt_warning_is_scoped_to_soperator(monkeypatch):
    from types import SimpleNamespace

    from nebius_cxcli.components import ComponentEntry

    payload = payload_for(1)
    captured = []
    monkeypatch.setattr(cli.console, "print", lambda message: captured.append(str(message)))
    provider = SimpleNamespace(
        compute_platform_preset_resources=lambda **kw: (16, 200, 1),
        compute_platform_preset_allows_gpu_clustering=lambda **kw: False,
    )
    index = next(i for i, row in enumerate(payload["infra"]["components"]) if row["id"] == "mk8s")
    args = dict(
        payload=payload,
        entry=ComponentEntry(
            id="mk8s", scope="infra", config_path="infra.mk8s", description="MK8s"
        ),
        full_path_label=f"infra.components[{index}].inputs.node_group_defaults.gpu.preset",
        provider_lookup=provider,
        emitted_guidance=set(),
    )
    cli._maybe_print_selected_gpu_preset_guidance(**args)
    cli._maybe_print_selected_gpu_preset_guidance(**args)
    assert len(captured) == 1
    assert "1-GPU Soperator requires fast deploy for Dev/Test" in captured[0]
    assert "Slurm readiness" in captured[0]
    soperator_rows(payload)[0]["enabled"] = False
    captured.clear()
    args["emitted_guidance"] = set()
    cli._maybe_print_selected_gpu_preset_guidance(**args)
    assert len(captured) == 1
    assert "Soperator" not in captured[0]


def test_noninteractive_materialization_does_not_infer_waiver():
    payload = payload_for(1)
    _materialize_soperator_component_defaults(payload)
    assert "deploymentProfile" not in soperator_rows(payload)[0]["values"]


def test_registered_target_keeps_explicit_diagnostics_policy():
    payload = payload_for(1)
    payload.setdefault("deploy", {})["targets"] = [
        {"instance_id": "mk8s", "kind": "external-mk8s", "ownership": "external"}
    ]
    before = copy.deepcopy(soperator_rows(payload))
    result = run_wizard(payload)
    assert soperator_rows(result) == before


def test_unknown_explicit_profile_is_not_silently_overwritten():
    payload = payload_for(1)
    soperator_rows(payload)[0]["values"]["deploymentProfile"] = "unsupported"
    with pytest.raises(ValueError, match="deploymentProfile"):
        run_wizard(payload)


def test_default_named_infra_instance_does_not_select_other_mk8s_targets():
    payload = payload_for(1)
    other_infra = copy.deepcopy(
        next(row for row in payload["infra"]["components"] if row["id"] == "mk8s")
    )
    other_infra["instance_id"] = "unrelated"
    payload["infra"]["components"].append(other_infra)
    other_app = copy.deepcopy(soperator_rows(payload)[0])
    other_app["instance_id"] = "unrelated"
    other_app["target_ref"] = "unrelated"
    payload["apps"]["charts"].append(other_app)
    before = copy.deepcopy(other_app)
    result = run_wizard(payload, selected_apps=set(), selected_infra={"mk8s"})
    rows = soperator_rows(result)
    assert rows[0]["values"]["deploymentProfile"] == "fast-dev-test"
    assert rows[1] == before
