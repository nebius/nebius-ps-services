from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import flux_ops
from nebius_cxcli.soperator_adapter import compile_upstream_soperator_values
from nebius_cxcli.soperator_checks_binding import CHECKS_RELEASE, auxiliary_post_renderers
from nebius_cxcli.soperator_checks_policy import compile_checks_policy
from test_soperator_checks_gpu_shapes import values_for
from test_soperator_deployment_profile import frozen_charts as frozen_charts
from test_soperator_flux_sources import _outer_bundle, _paths, _staged_contract


def test_fast_bootstrap_closes_partitions_without_changing_native_setup(frozen_charts):
    snapshot, _, source = frozen_charts
    values = values_for(1)
    values["deploymentProfile"] = "fast-dev-test"
    compiled, _ = compile_upstream_soperator_values(values, release=snapshot)
    policy = compile_checks_policy(source, compiled)
    initial = policy.effective_values(compiled, installing=True)
    assert initial["soperatorActiveChecks"] == compiled["soperatorActiveChecks"]
    for row in initial["slurmCluster"]["overrideValues"]["partitionConfiguration"]["partitions"]:
        assert "State=DOWN" in row["config"]
    assert initial != compiled


@pytest.mark.parametrize("fast", [True, False])
def test_admission_updates_only_values_and_umbrella_without_restaging(
    frozen_charts, tmp_path, monkeypatch, fast
):
    snapshot, _, _ = frozen_charts
    values = values_for(1 if fast else 8)
    if fast:
        values["deploymentProfile"] = "fast-dev-test"
    compiled, _ = compile_upstream_soperator_values(values, release=snapshot)
    outer = yaml.safe_load(_outer_bundle())
    outer["spec"]["values"] = compiled
    outer["spec"]["postRenderers"][0]["kustomize"]["patches"].append(
        {
            "target": {"name": CHECKS_RELEASE},
            "patch": yaml.safe_dump(
                [
                    {
                        "op": "add",
                        "path": "/spec/postRenderers",
                        "value": auxiliary_post_renderers(compiled),
                    }
                ]
            ),
        }
    )
    cm = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "terraform-fluxcd-values"},
        "data": {"values.yaml": yaml.safe_dump(compiled)},
    }
    commands = []
    applied = []
    stages = []
    authority = []

    def run(command, **kwargs):
        commands.append(command)
        if "apply" in command:
            applied.extend(yaml.safe_load_all(kwargs["input"]))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        assert "kustomize" in command
        return SimpleNamespace(returncode=0, stdout=yaml.safe_dump_all([cm, outer]), stderr="")

    monkeypatch.setattr(flux_ops.kubernetes_process, "run", run)
    monkeypatch.setattr(
        flux_ops, "_rendered_soperator_graph_contract", lambda _: _staged_contract()
    )
    monkeypatch.setattr(
        flux_ops, "_flux_wait_targets", lambda _: [SimpleNamespace(is_soperator_main=True)]
    )
    monkeypatch.setattr(
        flux_ops,
        "_run_kubectl_json_process",
        lambda *a, **kw: {
            "metadata": {"generation": 2},
            "status": {
                "observedGeneration": 2,
                "conditions": [{"type": "Ready", "status": "True"}],
            },
        },
    )
    monkeypatch.setattr(
        flux_ops,
        "_wait_for_soperator_release_stage",
        lambda contract, *a, **kw: stages.append(contract),
    )

    def apply():
        flux_ops.restore_fast_soperator_admission(
            _paths(tmp_path),
            extra_env={},
            assert_authority=lambda: authority.append(True),
            freeze_main_workload_authority=lambda identity: identity,
        )

    if not fast:
        with pytest.raises(ValueError, match="frozen fast"):
            apply()
        assert not applied
        return
    apply()
    assert [row["kind"] for row in applied] == ["ConfigMap", "HelmRelease"]
    assert applied[1]["spec"]["values"] == compiled
    assert applied[1]["spec"]["suspend"] is False
    assert all("patch" not in command for command in commands)
    assert len(stages) == 1
    assert all(row.get("isMain") for row in stages[0]["releases"])
    assert len(authority) >= 2


def test_fast_sources_batched_once_and_missing_or_foreign_inventory_fails(monkeypatch):
    calls = []
    item = {"kind": "HelmChart", "metadata": {"name": "chart", "namespace": "flux-system"}}

    def read(args, **kwargs):
        calls.append(args)
        return {"items": [item]}

    monkeypatch.setattr(flux_ops, "_kubectl_json", read)
    assert flux_ops._fast_readiness_sources({}, env={}) is None
    assert not calls
    contract = {"readiness": {"deploymentProfile": "fast-dev-test"}}
    assert flux_ops._fast_readiness_sources(contract, env={}) == {("HelmChart", "chart"): item}
    assert len(calls) == 1
    item["metadata"]["namespace"] = "foreign"
    assert flux_ops._fast_readiness_sources(contract, env={}) == {}
