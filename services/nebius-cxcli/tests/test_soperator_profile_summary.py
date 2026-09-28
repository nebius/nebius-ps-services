"""Profile presentation is a read-only projection of existing evidence."""

import copy
from types import SimpleNamespace

import pytest

from nebius_cxcli.soperator_deployment_profile import (
    apply_deployment_profile,
    deployment_profile_summary,
    validate_profile_workers,
)
from nebius_cxcli.soperator_values import explicit_values


def fast_values():
    values = {
        "soperatorActiveChecks": {"overrideValues": {}},
        "slurmCluster": {"overrideValues": {}},
    }
    apply_deployment_profile(values, "fast-dev-test")
    return values


@pytest.mark.parametrize("profile", ["standard", "fast-dev-test"])
def test_saved_summary_is_intent_not_applied_evidence(profile):
    lines = deployment_profile_summary(
        {"deploymentProfile": profile}, target="cluster", stage="saved"
    )
    assert "Saved configuration (not rendered)" in lines[0]
    assert profile in lines[0]
    assert "passed" not in "\n".join(lines)
    if profile == "fast-dev-test":
        assert "will be disabled when rendered" in "\n".join(lines)
    else:
        assert "native diagnostic defaults and valid configured overrides preserved" in lines[1]


@pytest.mark.parametrize("stage", ["rendered", "deployment"])
def test_summary_uses_rendered_controls_without_mutation_or_compilation(monkeypatch, stage):
    from nebius_cxcli import soperator_checks_policy, soperator_release_source

    def forbidden(*args, **kwargs):
        pytest.fail("display must not acquire source or compile a policy")

    monkeypatch.setattr(soperator_checks_policy, "compile_checks_policy", forbidden)
    monkeypatch.setattr(soperator_release_source, "ensure_soperator_release_source", forbidden)
    values = fast_values()
    values["deploymentProfile"] = "standard"  # Authored intent cannot select frozen execution.
    before = copy.deepcopy(values)
    lines = deployment_profile_summary(values, target="cluster", stage=stage)
    assert "fast-dev-test" in lines[0]
    assert "16 active, 7 passive" in "\n".join(lines)
    assert "Explicitly recorded" not in "\n".join(lines)
    assert values == before


@pytest.mark.parametrize("supported", [False, True])
def test_standard_inventory_uses_existing_policy_and_preserves_unknown(supported):
    policy = SimpleNamespace(rules=(object(), object()), passive={"supported": supported})
    text = "\n".join(
        deployment_profile_summary({}, target="cluster", stage="deployment", policy=policy)
    )
    assert "2 active" in text
    assert ("unknown (unsupported passive inventory)" in text) is not supported
    if supported:
        assert "passive diagnostics: 0" in text
    rendered = "\n".join(deployment_profile_summary({}, target="cluster", stage="rendered"))
    assert "Effective enabled" not in rendered


def test_only_recorded_boolean_overrides_are_displayed():
    row = {
        "values": {
            "soperator-activechecks": {
                "checks": {
                    "cuda-samples": {"enabled": False},
                    "wait-for-topology": {"runAfterCreation": False, "script": "private-content"},
                }
            },
            "slurmScripts": {"builtIn": {"boot_disk_full.sh": {"enabled": False}}},
        },
        "values-explicit-paths": [
            "/soperator-activechecks/checks/wait-for-topology",
            "/slurmScripts/builtIn/boot_disk_full.sh/enabled",
        ],
    }
    text = "\n".join(
        deployment_profile_summary(
            row["values"], target="cluster", stage="saved", explicit=explicit_values(row)
        )
    )
    assert "wait-for-topology.runAfterCreation=false" in text
    assert "boot_disk_full.sh.enabled=false" in text
    assert "private-content" not in text
    assert "cuda-samples" not in text


@pytest.mark.parametrize(
    "profile,count,enabled",
    [
        ("fast-dev-test", 1, True),
        ("standard", 8, True),
        ("standard", 0, False),
    ],
)
def test_eligibility_accepts_supported_workers(profile, count, enabled):
    validate_profile_workers(
        {
            "deploymentProfile": profile,
            "nodesets": [
                {"gpu": {"enabled": enabled}, "slurmd": {"resources": {"gpu": count}}},
            ],
        }
    )


def test_eligibility_names_worker_and_preserves_intent():
    values = {
        "deploymentProfile": "standard",
        "nodesets": [
            {"name": "worker-one", "gpu": {"enabled": True}, "slurmd": {"resources": {"gpu": 1}}},
        ],
    }
    before = copy.deepcopy(values)
    with pytest.raises(ValueError, match="worker-one.*Standard.*revise"):
        validate_profile_workers(values)
    assert values == before


@pytest.mark.parametrize("action", ["noop", "install"])
def test_noop_summary_reuses_policy_and_prints_once_after_success(tmp_path, action):
    from io import StringIO

    from rich.console import Console

    from nebius_cxcli.deployment_cli import _CliDeploymentExecutor
    from nebius_cxcli.deployment_plan import DeploymentAction

    paths = SimpleNamespace(flux_dir=tmp_path, reports_dir=tmp_path)
    executor = _CliDeploymentExecutor({}, paths, {}, options=None, lease=None)
    executor.plan = SimpleNamespace(action=DeploymentAction(action), target_ref="cluster")
    stream = StringIO()
    calls = []
    values = fast_values()
    fail = True

    def compile_policy(source, rendered):
        calls.append(rendered)
        if fail:
            raise ValueError("unverified policy")
        return SimpleNamespace(readiness_contract=lambda: {"verified": True})

    executor.cli = SimpleNamespace(
        console=Console(file=stream, width=160),
        soperator_release_snapshot_path=lambda *args: tmp_path,
        load_soperator_release_snapshot=lambda path: object(),
        ensure_soperator_release_source=lambda snapshot: SimpleNamespace(source_dir=tmp_path),
        _rendered_soperator_upstream_values=lambda path: values,
        compile_checks_policy=compile_policy,
    )
    with pytest.raises(ValueError, match="unverified policy"):
        executor._readiness_contract(paths)
    assert not stream.getvalue()
    fail = False
    for _ in range(2):
        assert executor._readiness_contract(paths) == {"verified": True}
    assert len(calls) == 3  # Exactly the pre-existing per-invocation compilation.
    assert stream.getvalue().count("Frozen deployment inputs: fast-dev-test") == (action == "noop")
