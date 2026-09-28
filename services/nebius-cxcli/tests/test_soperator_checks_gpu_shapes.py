"""GPU allocations survive desired-value compilation and schedule restoration."""

import copy

import pytest

from nebius_cxcli.soperator_checks_phase import ChecksPhase, ChecksPhaseContext
from nebius_cxcli.soperator_checks_policy import SoperatorChecksPolicy, checks_digest
from test_soperator_upstream_adapter import _values, compile_upstream_soperator_values


def values_for(*counts):
    values = _values()
    template = values["nodesets"][0]
    values["nodesets"] = []
    for index, count in enumerate(counts):
        node = copy.deepcopy(template)
        node["name"] = f"worker-{index}"
        node["slurmd"]["resources"]["gpu"] = count
        values["nodesets"].append(node)
    return values


@pytest.mark.parametrize("count", [2, 8])
def test_gpu_allocation_survives_policy_schedule_restoration(count):
    values = values_for(count)
    before = copy.deepcopy(values)
    compiled, _ = compile_upstream_soperator_values(values)
    assert values == before
    policy = SoperatorChecksPolicy(
        source_sha256="sha256:" + "a" * 64,
        values_sha256=checks_digest(compiled),
        rules=(),
        check_values={},
    )
    for phase in ChecksPhase:
        effective = policy.effective_values(
            compiled,
            installing=True,
            context=ChecksPhaseContext(phase, "", fresh_install=True),
        )
        assert effective["soperatorActiveChecks"]["overrideValues"]["slurmJob"][
            "gpusPerNode"
        ] == str(count)


@pytest.mark.parametrize("allocation", [1, "1", "8"])
def test_explicit_fitting_allocation_and_other_settings_are_preserved(allocation):
    values = values_for(8)
    values["soperator-activechecks"] = {
        "slurmJob": {"gpusPerNode": allocation, "partition": "hidden"}
    }
    compiled, _ = compile_upstream_soperator_values(values)
    assert compiled["soperatorActiveChecks"]["overrideValues"]["slurmJob"] == {
        "gpusPerNode": allocation,
        "partition": "hidden",
    }


@pytest.mark.parametrize("allocation", [8, "8", 0, -1, True, 1.0, "1.0", None, ""])
def test_invalid_or_excessive_explicit_gpu_allocation_fails(allocation):
    values = values_for(2)
    values["soperator-activechecks"] = {"slurmJob": {"gpusPerNode": allocation}}
    with pytest.raises(ValueError, match="ActiveChecks.*GPU"):
        compile_upstream_soperator_values(values)


def test_cpu_nodes_do_not_change_gpu_allocation():
    values = values_for(2, 0)
    values["nodesets"][1]["gpu"]["enabled"] = False
    compiled, _ = compile_upstream_soperator_values(values)
    assert compiled["soperatorActiveChecks"]["overrideValues"]["slurmJob"]["gpusPerNode"] == "2"


def test_mixed_gpu_counts_need_explicit_fitting_allocation():
    values = values_for(2, 8)
    with pytest.raises(ValueError, match="ActiveChecks.*mixed GPU"):
        compile_upstream_soperator_values(values)
    values["soperator-activechecks"] = {"slurmJob": {"gpusPerNode": "1"}}
    compiled, _ = compile_upstream_soperator_values(values)
    assert compiled["soperatorActiveChecks"]["overrideValues"]["slurmJob"]["gpusPerNode"] == "1"


@pytest.mark.parametrize("count", [None, 0, -1, True, "1"])
def test_gpu_worker_requires_positive_integral_count(count):
    with pytest.raises(ValueError, match="ActiveChecks.*GPU"):
        compile_upstream_soperator_values(values_for(count))
