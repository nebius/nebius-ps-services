from __future__ import annotations

import copy

import pytest

from nebius_cxcli.soperator_release_order import execution_release_graph


def graph(*, managed_certificates=True, declared_dependency=False):
    rows = [
        {"releaseName": "namespace", "stage": 0},
        {
            "releaseName": "profiles",
            "upstreamReleaseName": "soperator-fluxcd-security-profiles-operator",
            "stage": 0,
            "dependencies": ["certificates"] if declared_dependency else [],
        },
        {"releaseName": "consumer", "stage": 1, "dependencies": ["profiles"]},
        {"releaseName": "unrelated", "stage": 7},
    ]
    if managed_certificates:
        rows.append(
            {
                "releaseName": "certificates",
                "upstreamReleaseName": "soperator-fluxcd-cert-manager",
                "stage": 1,
                "dependencies": ["namespace"],
            }
        )
    return {"releases": rows, "sourceCommit": "frozen-source"}


@pytest.mark.parametrize("declared_dependency", [False, True])
def test_runtime_prerequisite_preserves_source_and_orders_transitive_consumers(
    declared_dependency,
):
    original = graph(declared_dependency=declared_dependency)
    before = copy.deepcopy(original)
    result = execution_release_graph(original)
    rows = {row["releaseName"]: row for row in result["releases"]}
    assert original == before
    assert result["sourceCommit"] == "frozen-source"
    assert rows["profiles"]["dependencies"] == ["certificates"]
    assert {name: row["stage"] for name, row in rows.items()} == {
        "namespace": 0,
        "certificates": 1,
        "profiles": 2,
        "consumer": 3,
        "unrelated": 7,
    }
    assert execution_release_graph(result) == result


def test_external_cert_manager_does_not_add_an_unmanaged_release():
    original = graph(managed_certificates=False)
    assert execution_release_graph(original) == original


@pytest.mark.parametrize("invalid", ["cycle", "unknown", "duplicate"])
def test_invalid_execution_dependencies_fail_before_any_release_opens(invalid):
    original = graph()
    if invalid == "cycle":
        original["releases"][-1]["dependencies"] = ["profiles"]
    elif invalid == "unknown":
        original["releases"][-1]["dependencies"] = ["missing"]
    else:
        original["releases"].append(copy.deepcopy(original["releases"][0]))
    with pytest.raises(ValueError, match="cycle|unknown dependencies|duplicate"):
        execution_release_graph(original)
