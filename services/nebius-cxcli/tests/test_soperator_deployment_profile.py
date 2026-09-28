"""Fast deployment intent, pinned-source suppression and worker-based queue sizing."""

import copy

import pytest

from nebius_cxcli.soperator_adapter import compile_upstream_soperator_values
from nebius_cxcli.soperator_checks_policy import compile_checks_policy
from nebius_cxcli.soperator_deployment_profile import (
    FAST_DEPLOY_NOTICE,
    WAIVED_CHECKS,
    deployment_profile,
    diagnostics_notice,
    resolve_create_profile,
)
from nebius_cxcli.soperator_passive_policy import BASE_SCRIPTS, DIAGNOSTICS
from nebius_cxcli.soperator_vmagent import materialize_vmagent_queues
from test_soperator_checks_gpu_shapes import values_for
from test_soperator_configuration_render import _render_values, frozen_charts_for


@pytest.fixture(params=["4.1.9", "4.1.11"])
def frozen_charts(tmp_path, request):
    return frozen_charts_for(tmp_path, request.param)


@pytest.mark.parametrize(
    "flag,file,wanted",
    [
        (True, "standard", "fast-dev-test"),
        (False, "fast-dev-test", "standard"),
        (None, "standard", "standard"),
        (None, "fast-dev-test", "fast-dev-test"),
        (True, None, "fast-dev-test"),
        (False, None, "standard"),
        (None, None, "standard"),
    ],
)
def test_noninteractive_create_precedence(flag, file, wanted):
    values = {"deploymentProfile": file} if file is not None else {}
    before = copy.deepcopy(values)
    result = resolve_create_profile(values, fast_deploy=flag)
    assert result["deploymentProfile"] == wanted
    assert values == before


@pytest.mark.parametrize("flag", [None, False, True])
@pytest.mark.parametrize("file", [None, "standard", "fast-dev-test"])
@pytest.mark.parametrize("choice", [False, True])
def test_interactive_create_always_asks_and_final_answer_wins(flag, file, choice):
    invoked = []
    values = {"deploymentProfile": file} if file is not None else {}
    values["nested"] = {"preserved": []}
    before = copy.deepcopy(values)
    result = resolve_create_profile(
        values, fast_deploy=flag, choose=lambda seed: invoked.append(seed) or choice
    )
    expected_seed = flag if flag is not None else None if file is None else file == "fast-dev-test"
    assert invoked == [expected_seed]
    assert result["deploymentProfile"] == ("fast-dev-test" if choice else "standard")
    result["nested"]["preserved"].append("changed")
    assert values == before


def test_existing_and_new_noninteractive_defaults_are_standard():
    assert deployment_profile({}) == "standard"
    assert resolve_create_profile(None, fast_deploy=None) == {"deploymentProfile": "standard"}


@pytest.mark.parametrize("profile", ["cpu", "gpu", "mixed"])
@pytest.mark.parametrize("explicit_conflict", [False, True])
def test_fast_generated_defaults_compile_without_overriding_user_checks(profile, explicit_conflict):
    from nebius_cxcli import cli
    from nebius_cxcli.components import component_entries, soperator_install_entry
    from nebius_cxcli.soperator_values import seed_soperator_values, soperator_rows
    from soperator_fixtures import sample_snapshot

    snapshot = sample_snapshot()
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
            soperator_install_entry(snapshot.release, chart_repo="oci://example.invalid/soperator"),
        ),
        soperator_profile=f"nebius-{profile}-v1",
    )
    values = resolve_create_profile(None, fast_deploy=True)
    if explicit_conflict:
        values["soperator-activechecks"] = {"checks": {"ensure-healthy-nodes": {"enabled": True}}}
    seed_soperator_values(payload, values)
    for _ in range(2):
        cli._materialize_soperator_component_defaults(payload)
        authored = soperator_rows(payload)[0]["values"]
        if explicit_conflict:
            with pytest.raises(
                ValueError, match="Fast deployment conflicts with explicit check controls"
            ):
                compile_upstream_soperator_values(authored, release=snapshot)
        else:
            compiled, _ = compile_upstream_soperator_values(authored, release=snapshot)
            checks = compiled["soperatorActiveChecks"]["overrideValues"]["checks"]
            assert all(checks[name]["enabled"] is False for name in WAIVED_CHECKS)
            assert compiled["cxcliDiagnostics"]["profile"] == "fast-dev-test"


@pytest.mark.parametrize(
    "values",
    [
        {"deploymentProfile": "unknown"},
        {"deploymentProfile": None},
        {"diagnosticsProfile": "test-dev-single-gpu"},
    ],
)
def test_invalid_and_retired_profile_rejected(values):
    with pytest.raises(ValueError, match="deploymentProfile"):
        resolve_create_profile(
            values, fast_deploy=True, choose=lambda _seed: pytest.fail("invalid input prompted")
        )


@pytest.mark.parametrize("counts", [(1,), (8,), (1, 8), (0,), (1, 0)])
def test_fast_actual_chart_retains_setup_and_operational_hooks(frozen_charts, counts):
    snapshot, _, source = frozen_charts
    values = values_for(*counts)
    for node, count in zip(values["nodesets"], counts, strict=True):
        node["gpu"]["enabled"] = count > 0
    assert "checks" not in values.get("soperator-activechecks", {})
    authored = copy.deepcopy(values)
    values["deploymentProfile"] = "fast-dev-test"
    compiled, _ = compile_upstream_soperator_values(values, release=snapshot)
    assert values == {**authored, "deploymentProfile": "fast-dev-test"}
    controls = compiled["soperatorActiveChecks"]["overrideValues"]["checks"]
    assert all(controls[name]["enabled"] is False for name in WAIVED_CHECKS)
    policy = compile_checks_policy(source, compiled)
    assert diagnostics_notice({"diagnostics": policy.diagnostics}) == FAST_DEPLOY_NOTICE
    assert set(policy.diagnostics["waived"]) == WAIVED_CHECKS
    assert "deploymentProfile" not in compiled["slurmCluster"]["overrideValues"]
    assert not policy.passive["diagnostics"]
    names = {rule.name for rule in policy.required}
    assert {"manage-jail-state", "create-user-nebius"} <= names
    assert not names & WAIVED_CHECKS
    rendered = _render_values(
        source / "helm/soperator-activechecks", compiled["soperatorActiveChecks"]["overrideValues"]
    )
    actual = {row["metadata"]["name"] for row in rendered if row["kind"] == "ActiveCheck"}
    assert not actual & WAIVED_CHECKS
    for row in rendered:
        if row["kind"] == "ActiveCheck":
            assert set(row["spec"].get("dependsOn", [])) <= actual
    passive = _render_values(
        source / "helm/slurm-cluster", compiled["slurmCluster"]["overrideValues"]
    )
    config = next(
        row
        for row in passive
        if row["kind"] == "ConfigMap" and row["metadata"]["name"] == "slurm-scripts"
    )["data"]
    assert set(BASE_SCRIPTS) <= config.keys()
    assert not DIAGNOSTICS & config.keys()
    assert "cleanup_enroot.sh" in config
    assert "job_tmpfs_recreate.sh" in config


def test_standard_one_gpu_rejected_before_deployment(frozen_charts):
    snapshot, _, source = frozen_charts
    with pytest.raises(ValueError, match="One-GPU.*fast-dev-test"):
        compile_upstream_soperator_values(values_for(1), release=snapshot)
    compiled, _ = compile_upstream_soperator_values(values_for(8), release=snapshot)
    compiled["nodesets"]["overrideValues"]["nodesets"][0]["slurmd"]["resources"]["gpu"] = 1
    with pytest.raises(ValueError, match="One-GPU.*fast-dev-test"):
        compile_checks_policy(source, compiled)


def test_profile_roundtrip_preserves_authored_controls_and_recovery_selection(
    frozen_charts, tmp_path
):
    from types import SimpleNamespace

    import yaml

    from nebius_cxcli.soperator_deployment_profile import rendered_deployment_profile
    from nebius_cxcli.soperator_release_reconciler import soperator_reconcile_stage_plan_sha256

    snapshot, _, source = frozen_charts
    values = values_for(8)
    values["soperator-activechecks"] = {
        "checks": {"wait-for-topology": {"runAfterCreation": False}}
    }
    before = copy.deepcopy(values)
    rendered = []
    for profile in ("standard", "fast-dev-test", "standard"):
        selected = {**values, "deploymentProfile": profile}
        compiled, _ = compile_upstream_soperator_values(selected, release=snapshot)
        compile_checks_policy(source, compiled)
        rendered.append(compiled)
        assert (
            compiled["soperatorActiveChecks"]["overrideValues"]["checks"]["wait-for-topology"][
                "runAfterCreation"
            ]
            is False
        )
        (tmp_path / "configmap-terraform-fluxcd-values.yaml").write_text(
            yaml.safe_dump(
                {
                    "data": {"values.yaml": yaml.safe_dump(compiled)},
                }
            )
        )
        selected["deploymentProfile"] = (
            "standard" if profile == "fast-dev-test" else "fast-dev-test"
        )
        frozen_profile = rendered_deployment_profile(SimpleNamespace(flux_dir=tmp_path))
        assert frozen_profile == profile
        assert soperator_reconcile_stage_plan_sha256(
            strategy="install",
            rendered_graph_sha256="sha256:" + "a" * 64,
            deployment_profile=frozen_profile,
        ) != soperator_reconcile_stage_plan_sha256(
            strategy="install",
            rendered_graph_sha256="sha256:" + "a" * 64,
            deployment_profile=selected["deploymentProfile"],
        )
    assert values == before
    assert rendered[0] == rendered[2]
    assert "cxcliDiagnostics" not in rendered[2]


@pytest.mark.parametrize("mutation", ["reenable", "metadata", "prerequisite", "custom-passive"])
def test_profile_tampering_fails(frozen_charts, mutation):
    snapshot, _, source = frozen_charts
    values = values_for(1)
    values["deploymentProfile"] = "fast-dev-test"
    compiled, _ = compile_upstream_soperator_values(values, release=snapshot)
    checks = compiled["soperatorActiveChecks"]["overrideValues"]["checks"]
    if mutation == "reenable":
        checks["cuda-samples"]["enabled"] = True
    elif mutation == "metadata":
        compiled["cxcliDiagnostics"]["waived"].pop()
    elif mutation == "prerequisite":
        checks["enroot-cleanup"]["dependsOn"] = []
    else:
        compiled["slurmCluster"]["overrideValues"]["slurmScripts"]["prolog.sh"] = "echo custom"
    with pytest.raises(ValueError):
        compile_checks_policy(source, compiled)


@pytest.mark.parametrize(
    "capacity,wanted", [(2, "2"), (59, "2"), (60, "3"), (119, "3"), (120, "4")]
)
def test_queue_boundaries(capacity, wanted):
    values = {"observability": {}, "nodesets": [{"replicas": capacity}]}
    materialize_vmagent_queues(values)
    assert values["observability"]["vmStack"]["values"]["vmagent"]["spec"]["extraArgs"] == {
        "remoteWrite.queues": wanted
    }


@pytest.mark.parametrize("profile", ["standard", "fast-dev-test"])
def test_deploy_summary_uses_verified_frozen_values_once(
    frozen_charts, tmp_path, monkeypatch, profile
):
    from io import StringIO
    from types import SimpleNamespace

    import yaml
    from rich.console import Console

    from nebius_cxcli import cli

    snapshot, receipt, source = frozen_charts
    values = {**values_for(8), "deploymentProfile": profile}
    compiled, _ = compile_upstream_soperator_values(values, release=snapshot)
    (tmp_path / "configmap-terraform-fluxcd-values.yaml").write_text(
        yaml.safe_dump(
            {
                "data": {"values.yaml": yaml.safe_dump(compiled)},
            }
        )
    )
    (tmp_path / "soperator-nebius-adapter.yaml").write_text("")
    calls = []
    stream = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=stream, width=180))
    monkeypatch.setattr(cli, "flux_dir_has_rendered_resources", lambda path: True)
    monkeypatch.setattr(cli, "load_soperator_release_snapshot", lambda path: snapshot)
    monkeypatch.setattr(
        cli, "ensure_soperator_release_source", lambda snap: calls.append("source") or receipt
    )

    def compile_policy(path, rendered):
        calls.append("policy")
        return compile_checks_policy(path, rendered)

    monkeypatch.setattr(cli, "compile_checks_policy", compile_policy)
    monkeypatch.setattr(cli, "_rendered_soperator_adapter_state", lambda path: {})
    monkeypatch.setattr(
        cli, "rendered_soperator_jail_image_authority", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        cli, "verify_soperator_release_artifacts", lambda *args, **kwargs: calls.append("artifacts")
    )
    which = cli.shutil.which
    monkeypatch.setattr(
        cli.shutil, "which", lambda name: None if name == "kubectl" else which(name)
    )
    paths = SimpleNamespace(flux_dir=tmp_path, reports_dir=tmp_path, path_project_folder="cluster")
    changed_source = {
        "deploymentProfile": "standard" if profile == "fast-dev-test" else "fast-dev-test"
    }
    with pytest.raises(RuntimeError, match="kubectl is required"):
        cli._apply_rendered_flux(paths, config=changed_source, target_ref="cluster")
    assert calls == ["source", "policy", "artifacts"]
    assert stream.getvalue().count(f"Frozen deployment inputs: {profile}") == 1
    assert "Saved configuration" not in stream.getvalue()


def test_queue_override_and_capacity_resize_on_rendered_copy():
    authored = {
        "observability": {},
        "nodesets": [{"replicas": 60, "ephemeral": True}, {"replicas": 60}],
        "slurmctld": {"replicas": 50},
        "login": {"replicas": 50},
    }
    first = copy.deepcopy(authored)
    materialize_vmagent_queues(first)
    args = first["observability"]["vmStack"]["values"]["vmagent"]["spec"]["extraArgs"]
    assert args["remoteWrite.queues"] == "4"
    assert authored["observability"] == {}
    authored["nodesets"] = [{"replicas": 1}]
    second = copy.deepcopy(authored)
    materialize_vmagent_queues(second)
    assert (
        second["observability"]["vmStack"]["values"]["vmagent"]["spec"]["extraArgs"][
            "remoteWrite.queues"
        ]
        == "2"
    )
    args["remoteWrite.queues"] = "7"
    first["nodesets"] = [{"replicas": 1000}]
    materialize_vmagent_queues(first)
    assert args["remoteWrite.queues"] == "7"


@pytest.mark.parametrize("mutation", ["check-kind", "inline-override", "container-override"])
def test_fast_rejects_incompatible_or_custom_waived_execution(frozen_charts, mutation):
    import yaml

    snapshot, _, source = frozen_charts
    values = values_for(1)
    values["deploymentProfile"] = "fast-dev-test"
    compiled, _ = compile_upstream_soperator_values(values, release=snapshot)
    chart = source / "helm/soperator-activechecks"
    if mutation == "check-kind":
        path = chart / "values.yaml"
        defaults = yaml.safe_load(path.read_text())
        defaults["checks"]["gpu-fryer"]["checkType"] = "unsupported"
        path.write_text(yaml.safe_dump(defaults))
    elif mutation == "inline-override":
        compiled["soperatorActiveChecks"]["overrideValues"]["checks"]["ssh-check"]["k8sJobSpec"] = {
            "script": "echo custom-setup"
        }
    else:
        compiled["soperatorActiveChecks"]["overrideValues"]["checks"]["ssh-check"][
            "podTemplateNameRef"
        ] = "custom-template"
    with pytest.raises(ValueError, match="Fast deployment.*(upstream|custom)"):
        compile_checks_policy(source, compiled)


def test_fast_accepts_future_release_and_changed_disabled_scripts(frozen_charts):
    import yaml

    from soperator_fixtures import sample_snapshot

    _, _, source = frozen_charts
    values = values_for(1)
    values["deploymentProfile"] = "fast-dev-test"
    snapshot = sample_snapshot(release="9.9.9")
    compiled, _ = compile_upstream_soperator_values(values, release=snapshot)
    for directory in ("soperator-activechecks", "slurm-cluster"):
        chart = source / "helm" / directory
        metadata = chart / "Chart.yaml"
        doc = yaml.safe_load(metadata.read_text())
        doc.update(version="9.9.9", appVersion="9.9.9")
        metadata.write_text(yaml.safe_dump(doc))
        defaults = chart / "values.yaml"
        doc = yaml.safe_load(defaults.read_text())
        key = "k8sJob" if directory == "soperator-activechecks" else "slurmd"
        doc["images"][key] = "registry.example.invalid/native:9.9.9"
        defaults.write_text(yaml.safe_dump(doc))
        script = chart / (
            "scripts/ssh-check.sh"
            if directory == "soperator-activechecks"
            else "slurm_scripts/gpu_health_check.py"
        )
        script.write_text(script.read_text() + "\n# future upstream diagnostic revision\n")
    policy = compile_checks_policy(source, compiled)
    assert not {rule.name for rule in policy.rules} & WAIVED_CHECKS
    assert policy.passive["supported"]
    assert not policy.passive["diagnostics"]


@pytest.mark.parametrize(
    "custom", ["PluginDir=/opt/slurm/lib/slurm", "SchedulerParameters=bf_continue"]
)
def test_fast_allows_unrelated_custom_slurm_settings(frozen_charts, custom):
    snapshot, _, source = frozen_charts
    values = values_for(1)
    values.update(deploymentProfile="fast-dev-test", customSlurmConfig=custom)
    compiled, _ = compile_upstream_soperator_values(values, release=snapshot)
    policy = compile_checks_policy(source, compiled)
    assert policy.passive["supported"]
    assert not policy.passive["diagnostics"]


@pytest.mark.parametrize(
    "custom",
    ["Prolog=/opt/custom.sh", "HealthCheckProgram=/opt/custom.sh", "Include=/opt/custom.conf"],
)
def test_fast_rejects_custom_slurm_hook_changes(frozen_charts, custom):
    snapshot, _, source = frozen_charts
    values = values_for(1)
    values.update(deploymentProfile="fast-dev-test", customSlurmConfig=custom)
    compiled, _ = compile_upstream_soperator_values(values, release=snapshot)
    with pytest.raises(ValueError, match="contract|Include"):
        compile_checks_policy(source, compiled)


@pytest.mark.parametrize(
    "mutation", ["disabled-script", "disabled-entry", "missing-operational-script"]
)
def test_fast_rejects_passive_render_that_ignores_controls(frozen_charts, monkeypatch, mutation):
    import json

    from nebius_cxcli import soperator_passive_policy as passive

    snapshot, _, source = frozen_charts
    values = values_for(1)
    values["deploymentProfile"] = "fast-dev-test"
    compiled, _ = compile_upstream_soperator_values(values, release=snapshot)
    original = passive._opaque_data

    def render(*args):
        data = copy.deepcopy(original(*args))
        if mutation == "disabled-script":
            data["gpu_health_check.py"] = "unexpected diagnostic"
        elif mutation == "disabled-entry":
            data["checks.json"] = json.dumps([{"command": "./gpu_health_check.py"}])
        else:
            data.pop("prolog.sh")
        return data

    monkeypatch.setattr(passive, "_opaque_data", render)
    with pytest.raises(ValueError, match="passive.*render"):
        compile_checks_policy(source, compiled)


def test_fast_rejects_active_template_that_ignores_disable_controls(frozen_charts):
    snapshot, _, source = frozen_charts
    values = values_for(1)
    values["deploymentProfile"] = "fast-dev-test"
    compiled, _ = compile_upstream_soperator_values(values, release=snapshot)
    path = source / "helm/soperator-activechecks/templates/active-checks.yaml"
    template = path.read_text()
    assert "if $check.enabled" in template
    path.write_text(template.replace("if $check.enabled", "if true"))
    with pytest.raises(ValueError, match="inventory differs"):
        compile_checks_policy(source, compiled)
