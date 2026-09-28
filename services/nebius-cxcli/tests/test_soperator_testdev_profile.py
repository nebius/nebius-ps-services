"""The one-GPU exception is explicit, bounded and visible in acceptance evidence."""

import shutil
import subprocess

import pytest
import yaml

from nebius_cxcli.soperator_acceptance import validation_contract
from nebius_cxcli.soperator_adapter import compile_upstream_soperator_values
from nebius_cxcli.soperator_checks_policy import compile_checks_policy
from nebius_cxcli.soperator_deployment_profile import WAIVED_CHECKS as WAIVED
from test_soperator_checks_gpu_shapes import values_for
from test_soperator_configuration_render import _render_values
from test_soperator_deployment_profile import frozen_charts as frozen_charts
from test_soperator_upstream_adapter import _RELEASE


def test_standard_eight_gpu_values_have_no_exception():
    compiled, _ = compile_upstream_soperator_values(values_for(8), release=_RELEASE)
    assert "cxcliDiagnostics" not in compiled
    assert not set(compiled["soperatorActiveChecks"]["overrideValues"].get("checks", {})) & WAIVED


@pytest.mark.parametrize("dependencies", [[], ["manage-jail-state", "cuda-samples"], None])
def test_testdev_rejects_conflicting_dependency_overrides(dependencies):
    values = values_for(1)
    values["deploymentProfile"] = "fast-dev-test"
    values["soperator-activechecks"] = {"checks": {"enroot-cleanup": {"dependsOn": dependencies}}}
    with pytest.raises(ValueError, match="[Ff]ast deployment.*dependencies"):
        compile_upstream_soperator_values(values, release=_RELEASE)


def test_testdev_preserves_new_upstream_non_gpu_dependencies(frozen_charts):
    snapshot, _, source = frozen_charts
    values = values_for(1)
    values["deploymentProfile"] = "fast-dev-test"
    compiled, _ = compile_upstream_soperator_values(values, release=snapshot)
    path = source / "helm/soperator-activechecks/values.yaml"
    defaults = yaml.safe_load(path.read_text())
    defaults["checks"]["enroot-cleanup"]["dependsOn"].append("create-user-nebius")
    path.write_text(yaml.safe_dump(defaults))
    with pytest.raises(ValueError, match="preserve every non-waived check dependency"):
        compile_checks_policy(source, compiled)


def test_testdev_rejects_dependency_tampering_in_frozen_values(frozen_charts):
    snapshot, _, source = frozen_charts
    values = values_for(1)
    values["deploymentProfile"] = "fast-dev-test"
    compiled, _ = compile_upstream_soperator_values(values, release=snapshot)
    compiled["soperatorActiveChecks"]["overrideValues"]["checks"]["enroot-cleanup"][
        "dependsOn"
    ] = []
    with pytest.raises(ValueError, match="[Ff]ast deployment.*dependencies"):
        compile_checks_policy(source, compiled)


def test_standard_eight_gpu_keeps_upstream_health_dependencies(frozen_charts):
    snapshot, _, source = frozen_charts
    compiled, _ = compile_upstream_soperator_values(values_for(8), release=snapshot)
    policy = compile_checks_policy(source, compiled)
    healthy = next(rule for rule in policy.rules if rule.name == "ensure-healthy-nodes")
    defaults = yaml.safe_load((source / "helm/soperator-activechecks/values.yaml").read_text())
    assert healthy.dependencies == tuple(defaults["checks"]["ensure-healthy-nodes"]["dependsOn"])
    assert set(healthy.dependencies) & WAIVED


@pytest.mark.parametrize("testdev", [False, True])
def test_auxiliary_scheduler_preserves_profile_at_handoff(frozen_charts, tmp_path, testdev):
    from nebius_cxcli.soperator_checks_binding import auxiliary_post_renderers

    if not shutil.which("kubectl"):
        pytest.skip("kubectl kustomize is required for native postrenderer validation")
    snapshot, _, source = frozen_charts
    values = values_for(1 if testdev else 8)
    if testdev:
        values["deploymentProfile"] = "fast-dev-test"
    compiled, _ = compile_upstream_soperator_values(values, release=snapshot)
    policy = compile_checks_policy(source, compiled)
    rendered = _render_values(
        source / "helm/soperator-activechecks", compiled["soperatorActiveChecks"]["overrideValues"]
    )
    cron = next(row for row in rendered if row["kind"] == "CronJob")
    directory = tmp_path / "auxiliary"
    directory.mkdir()
    (directory / "cron.yaml").write_text(yaml.safe_dump(cron))
    for deferred in (True, False):
        (directory / "kustomization.yaml").write_text(
            yaml.safe_dump(
                {
                    "resources": ["cron.yaml"],
                    "patches": auxiliary_post_renderers(compiled, suspended=deferred)[0][
                        "kustomize"
                    ]["patches"],
                }
            )
        )
        result = subprocess.run(
            ["kubectl", "kustomize", str(directory)], capture_output=True, text=True, check=True
        )
        actual = yaml.safe_load(result.stdout)["spec"]
        assert actual["suspend"] is (deferred or testdev)
        if not deferred:
            assert actual == policy.auxiliary_spec


def test_notice_states_waivers_and_preserves_standard_output(frozen_charts):
    from nebius_cxcli.soperator_deployment_profile import diagnostics_notice

    snapshot, _, source = frozen_charts
    values = values_for(1)
    values["deploymentProfile"] = "fast-dev-test"
    compiled, _ = compile_upstream_soperator_values(values, release=snapshot)
    policy = compile_checks_policy(source, compiled)
    contract = validation_contract(policy)
    notice = diagnostics_notice(contract)
    assert "Fast deploy" in notice
    assert "Dev/Test only" in notice
    assert "GPU health and performance qualification are disabled" in notice
    assert "passed" not in notice
    assert diagnostics_notice({}) == ""
    contract["diagnostics"]["waived"].pop()
    assert set(policy.diagnostics["waived"]) == WAIVED
    with pytest.raises(ValueError, match="coverage"):
        diagnostics_notice(contract)


@pytest.mark.parametrize("defect", ["missing-source", "reenabled", "unrelated-disabled"])
def test_profile_cannot_bypass_changed_frozen_controls(defect):
    from nebius_cxcli.soperator_deployment_profile import validate_diagnostics_profile

    values = values_for(1)
    values["deploymentProfile"] = "fast-dev-test"
    compiled, _ = compile_upstream_soperator_values(values, release=_RELEASE)
    defaults = {name: {"checkType": "slurmJob"} for name in WAIVED}
    checks = compiled["soperatorActiveChecks"]["overrideValues"]["checks"]
    if defect == "missing-source":
        defaults.pop("cuda-samples")
    elif defect == "reenabled":
        checks["cuda-samples"]["enabled"] = True
    else:
        checks["create-user-nebius"] = {"enabled": False}
    with pytest.raises(ValueError, match="[Ff]ast deployment"):
        validate_diagnostics_profile(compiled, defaults)
