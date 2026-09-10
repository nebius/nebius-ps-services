import copy
from dataclasses import replace

import pytest
import yaml

from nebius_cxcli.soperator_checks_policy import checks_digest
from nebius_cxcli.soperator_install_cpu_mask_repair import (
    cpu_mask_repair_candidate,
    cpu_mask_reservation_handoff,
)
from nebius_cxcli.soperator_install_render_repair import (
    OUTER_FILE,
    VALUES_FILE,
    _file_hashes,
    _files,
)
from test_soperator_checks_execution import policy as policy
from test_soperator_install_runtime_repair import compiled as runtime_compiled
from test_soperator_release_reconciler import _paths


def compiled():
    from nebius_cxcli.soperator_worker_topology import h200_static

    before, values = runtime_compiled()
    values["nodesets"]["overrideValues"]["nodesets"][0]["nodeConfig"] = {
        "static": h200_static(32000)
    }
    values["nodesets"]["overrideValues"]["nodesets"][0]["slurmd"]["volumes"]["jailSubMounts"] = []
    values["nodesets"]["overrideValues"]["nodesets"][0]["slurmd"]["resources"][
        "ephemeralStorage"
    ] = "10Gi"
    for name in (VALUES_FILE, OUTER_FILE):
        document = yaml.safe_load(before[name])
        if name == VALUES_FILE:
            document["data"]["values.yaml"] = yaml.safe_dump(values, sort_keys=False)
        else:
            document["spec"]["values"] = values
        before[name] = yaml.safe_dump(document, sort_keys=False).encode()
    return before, values


@pytest.mark.parametrize("saved_successor", [False, True])
def test_cpu_mask_successor_routes_after_topology_and_resumes_directly(
    tmp_path, monkeypatch, saved_successor
):
    from nebius_cxcli import soperator_install_cpu_mask_repair as successor
    from nebius_cxcli import soperator_install_topology_repair as predecessor
    from nebius_cxcli.soperator_install_checks_repair import prepare_install_input_repair

    paths = _paths(tmp_path)
    paths.reports_dir.mkdir(exist_ok=True, parents=True)
    (paths.reports_dir / "soperator-install-topology-repair-gpu.json").write_text("{}")
    if saved_successor:
        (paths.reports_dir / "soperator-install-cpu-mask-repair-gpu.json").write_text("{}")
    ancestor = {"generation": "previous"}
    calls = []

    def old(**kwargs):
        assert not saved_successor
        return ancestor

    def new(**kwargs):
        calls.append(kwargs)
        return {"repair": "selected"}

    monkeypatch.setattr(predecessor, "prepare_install_topology_repair", old)
    monkeypatch.setattr(successor, "prepare_install_cpu_mask_repair", new)
    assert prepare_install_input_repair(paths=paths, target_ref="gpu") == {"repair": "selected"}
    assert len(calls) == 1
    assert calls[0].get("ancestor") == (None if saved_successor else ancestor)


def test_cpu_mask_candidate_changes_only_exact_native_runtime_bindings():
    before, values = compiled()
    after = cpu_mask_repair_candidate(before)
    assert {name for name in after if after[name] != before[name]} == {VALUES_FILE, OUTER_FILE}
    assert cpu_mask_repair_candidate(after) == after
    assert cpu_mask_repair_candidate(after, inverse=True) == before
    expected = copy.deepcopy(values)
    from nebius_cxcli.soperator_worker_topology import H200_PHYSICAL

    expected["nodesets"]["overrideValues"]["nodesets"][0]["nodeConfig"]["static"] = (
        H200_PHYSICAL + " Gres=gpu:8"
    )
    assert yaml.safe_load(yaml.safe_load(after[VALUES_FILE])["data"]["values.yaml"]) == expected


@pytest.mark.parametrize("mutation", [None, "previous", "replacement", "policy", "resource"])
def test_cpu_mask_handoff_proves_exact_delta_and_both_full_values_policies(
    tmp_path, policy, mutation
):
    before, values = compiled()
    after = cpu_mask_repair_candidate(before)
    paths = _paths(tmp_path)
    for name, data in after.items():
        (paths.flux_dir / name).write_bytes(data)
    after = _files(paths.flux_dir)
    before = cpu_mask_repair_candidate(after, inverse=True)
    new_values = yaml.safe_load(yaml.safe_load(after[VALUES_FILE])["data"]["values.yaml"])
    old = replace(policy, values_sha256=checks_digest(values))
    target = replace(policy, values_sha256=checks_digest(new_values))
    repair = {
        "previousFiles": _file_hashes(before),
        "replacementFiles": _file_hashes(after),
        "reservationHandoff": {"policy": old.sha256, "fingerprint": "retained"},
    }
    if mutation == "previous":
        repair["previousFiles"][VALUES_FILE] = "changed"
    elif mutation == "replacement":
        repair["replacementFiles"][OUTER_FILE] = "changed"
    elif mutation == "policy":
        repair["reservationHandoff"]["policy"] = "changed"
    elif mutation == "resource":
        (paths.flux_dir / VALUES_FILE).write_bytes(after[VALUES_FILE].replace(b"10Gi", b"56Gi"))
    if mutation:
        with pytest.raises(RuntimeError):
            cpu_mask_reservation_handoff(repair, paths=paths, policy=target)
    else:
        result = cpu_mask_reservation_handoff(repair, paths=paths, policy=target)
        assert result["policy"] == target.sha256 and result["predecessorPolicy"] == old.sha256
        assert result["fingerprint"] == "retained"
