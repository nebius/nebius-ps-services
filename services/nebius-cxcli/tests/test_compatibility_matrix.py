from __future__ import annotations

import copy
from dataclasses import replace
from datetime import date

import pytest

from nebius_cxcli.compatibility_adapters import helm_constraint
from nebius_cxcli.compatibility_matrix import assess, digest, load_matrix, validate_selection
from nebius_cxcli.compatibility_runtime import selected_inventory
from nebius_cxcli.component_sources import load_component_sources
from nebius_cxcli.config_model import to_dynamic_payload, to_runtime_payload
from nebius_cxcli.frozen_catalog import freeze_catalog, use_frozen_catalog


def subject(**overrides):
    return {
        "component_id": "nvidia-network-operator",
        "instance_id": "cluster",
        "distribution": "nvidia-upstream-network",
        "application_version": "25.7.0",
        "kubernetes_minor": "1.35",
        **overrides,
    }


def test_known_unsupported_blocks_but_distribution_gap_warns():
    assert not assess([subject()], admission=True)["admitted"]
    report = assess([subject(distribution="nebius-marketplace-network")], admission=True)
    assert report["admitted"]
    assert any(row["outcome"] == "warn" for row in report["rows"])
    assert not any(row["outcome"] == "pass" for row in report["rows"])


def test_unknown_adapter_is_not_a_support_warning():
    with pytest.raises(ValueError, match="Unknown required"):
        assess([subject(required_adapters=["execute-shell"])])


def test_required_receipt_pending_in_plan_blocks_admission():
    row = subject(distribution="unknown", required_adapters=["resolved_terraform_constraints"])
    planned = assess([row])
    assert not planned["admitted"]
    assert any(r["outcome"] == "pending" for r in planned["rows"])
    assert not assess([row], admission=True)["admitted"]
    proof = {
        "subject_sha256": digest(row),
        "evaluator": "resolved_terraform_constraints",
        "input_sha256": digest({"lock": "verified"}),
        "outcome": "pass",
    }
    assert assess([row], admission=True, receipts=[proof])["admitted"]
    with pytest.raises(ValueError, match="Ambiguous"):
        assess([row], receipts=[proof, proof])


def test_expiration_never_erases_negative_evidence():
    matrix = load_matrix()
    matrix["sources"]["nvidia-network-25-7"]["expires_on"] = "2020-01-01"
    assert not assess([subject()], matrix=matrix, admission=True, today=date(2026, 9, 16))[
        "admitted"
    ]
    report = assess([subject(kubernetes_minor="1.33")], matrix=matrix, admission=True)
    assert report["admitted"]
    assert any(row["reason"] == "Affirmative evidence expired" for row in report["rows"])


def test_duplicate_yaml_keys_rejected(tmp_path):
    path = tmp_path / "matrix.yaml"
    path.write_text("schema_version: 1\nschema_version: 2\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_matrix(path)


def test_config_selection_round_trip_and_closed_fields():
    payload = {
        "infra": {"components": [{"id": "mk8s", "instance_id": "cluster"}]},
        "apps": {"charts": []},
        "compatibility": {"targets": {"cluster": {"version_set": None}}},
    }
    validate_selection(payload)
    assert (
        to_dynamic_payload(to_runtime_payload(payload))["compatibility"] == payload["compatibility"]
    )
    payload["compatibility"]["targets"]["cluster"]["ignore"] = True
    with pytest.raises(ValueError, match="only version_set"):
        validate_selection(payload)


def test_registry_is_only_default_pin_owner_and_conflicts_are_explicit():
    catalog = load_component_sources()
    network = next(
        chart for chart in catalog.helm_charts if chart.name == "nvidia-network-operator"
    )
    assert network.version == "25.7.0"
    config = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster",
                    "enabled": True,
                    "inputs": {"cluster": {"k8s_version": "1.35"}},
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": network.name,
                    "instance_id": "cluster",
                    "enabled": True,
                    "repo": network.repo,
                    "version": "99.0.0",
                }
            ]
        },
    }
    with pytest.raises(ValueError, match="conflicts with version set"):
        selected_inventory(config)
    config["compatibility"] = {"targets": {"cluster": {"version_set": None}}}
    assert selected_inventory(config)[1]["chart_version"] == "99.0.0"


def test_frozen_catalog_drift_and_integrity(monkeypatch):
    import nebius_cxcli.component_sources as module

    config = {
        "infra": {"components": [{"id": "mk8s"}]},
        "apps": {"charts": [{"id": "nvidia-gpu-operator"}]},
    }
    frozen = freeze_catalog(config)
    current = load_component_sources()
    drifted = replace(current, tf_modules=(), helm_charts=())
    monkeypatch.setattr(module, "_load_sources_cached", lambda *args: drifted)
    monkeypatch.setattr(module, "reset_component_sources_cache", lambda: None)
    monkeypatch.setattr("nebius_cxcli.components.reset_component_sources_cache", lambda: None)
    with use_frozen_catalog(frozen):
        assert len(load_component_sources().tf_modules) == 1
        assert len(load_component_sources().helm_charts) == 1
        with pytest.raises(ValueError, match="cannot replace frozen"):
            load_matrix(__import__("pathlib").Path("untrusted.yaml"))
    assert not load_component_sources().tf_modules
    altered = copy.deepcopy(frozen)
    altered["matrix"]["reviewed_on"] = "2020-01-01"
    with pytest.raises(ValueError, match="integrity"), use_frozen_catalog(altered):
        pytest.fail("tampered snapshot admitted")


@pytest.mark.parametrize(
    "constraint,version,accepted",
    [
        (">=1.31.0", "1.36", True),
        (">=1.31.0 <1.36.0 || >=1.37.0", "1.35.3", True),
        ("1.31.0 - 1.35.9", "1.36.0", False),
        (">=1.35.0-0", "1.35.0-rc.1", True),
        # Helm normalizes Kubernetes suffixes before metadata constraint checking.
        (">=1.35.0", "1.35.0-rc.1", True),
        (">=1.35.1", "1.35.0-rc.1", False),
        ("this-is-not-semver", "1.35.0", False),
    ],
)
def test_native_helm_semver(constraint, version, accepted):
    if accepted:
        assert helm_constraint({"kubeVersion": constraint}, version)["outcome"] == "pass"
    else:
        with pytest.raises(ValueError, match="Helm rejected"):
            helm_constraint({"kubeVersion": constraint}, version)


@pytest.mark.parametrize("metadata", [{}, {"kubeVersion": None}, {"kubeVersion": ""}])
def test_absent_constraint_does_not_claim_support(metadata):
    assert helm_constraint(metadata, "1.36") == {"outcome": "not_declared", "constraint": None}


@pytest.mark.parametrize("constraint", [" ", "\t", False, 0, [], {}])
def test_malformed_constraint_remains_blocking(constraint):
    with pytest.raises(ValueError, match="missing/malformed"):
        helm_constraint({"kubeVersion": constraint}, "1.36")


def test_declared_constraint_requires_target_version():
    with pytest.raises(ValueError, match="missing/malformed"):
        helm_constraint({"kubeVersion": ">=1.31.0"}, "")


def test_bad_assertion_selector_cannot_erase_block():
    matrix = load_matrix()
    matrix["assertions"][2]["when"] = {"kubernetes_mnor": "1.35"}
    with pytest.raises(ValueError, match="Malformed compatibility fields"):
        assess([subject()], matrix=matrix)


def test_prerelease_cannot_inherit_stable_vendor_support():
    with pytest.raises(ValueError, match="non-stable"):
        assess(
            [
                subject(
                    component_id="nvidia-gpu-operator",
                    distribution="nvidia-upstream-gpu",
                    application_version="25.10.1-rc.1",
                )
            ]
        )


def test_kubernetes_patch_uses_minor_documented_support():
    report = assess([subject(kubernetes_minor="1.33.2")], admission=True)
    assert report["admitted"]
    assert any(row["outcome"] == "pass" for row in report["rows"])


def test_known_kind_cannot_drop_required_adapters():
    report = assess(
        [subject(distribution="unknown", kind="helm", required_adapters=[])], admission=True
    )
    assert not report["admitted"]
    assert any(
        row["check_id"] == "exact_artifact_kube_version" and row["outcome"] == "block"
        for row in report["rows"]
    )


def test_profile_and_explicit_set_resolve_before_catalog_hint(monkeypatch):
    from nebius_cxcli.compatibility_matrix import default_chart_version
    from nebius_cxcli.compatibility_runtime import materialize_selection

    matrix = load_matrix()
    name = matrix["selection"]["profile_defaults"]["mk8s"]
    other = copy.deepcopy(matrix["version_sets"][name])
    other["profiles"] = ["soperator"]
    other["pins"]["nvidia-network-operator"]["chart_version"] = "25.10.0"
    matrix["version_sets"]["other"] = other
    matrix["selection"]["profile_defaults"]["soperator"] = "other"
    source = matrix["distributions"]["nebius-marketplace-network"]["source"]
    assert default_chart_version("nvidia-network-operator", source=source, matrix=matrix) is None
    assert (
        default_chart_version(
            "nvidia-network-operator", source=source, matrix=matrix, profile="soperator"
        )
        == "25.10.0"
    )
    payload = {
        "infra": {"components": [{"id": "mk8s", "instance_id": "cluster", "enabled": True}]},
        "apps": {
            "charts": [
                {"id": "soperator", "instance_id": "cluster", "enabled": True, "version": "4.1.8"},
                {
                    "id": "nvidia-network-operator",
                    "instance_id": "cluster",
                    "enabled": True,
                    "repo": source,
                },
            ]
        },
    }
    monkeypatch.setattr("nebius_cxcli.compatibility_runtime.load_matrix", lambda: matrix)
    materialize_selection(payload)
    assert payload["apps"]["charts"][1]["version"] == "25.10.0"
    assert payload["compatibility"]["targets"]["cluster"] == {"version_set": "other"}
