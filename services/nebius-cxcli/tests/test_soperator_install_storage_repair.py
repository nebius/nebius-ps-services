import copy
from dataclasses import replace

import pytest
import yaml

from nebius_cxcli.soperator_checks_policy import checks_digest
from nebius_cxcli.soperator_install_render_repair import (
    OUTER_FILE,
    VALUES_FILE,
    _file_hashes,
    _files,
)
from nebius_cxcli.soperator_install_storage_repair import (
    storage_repair_candidate,
    storage_reservation_handoff,
)
from test_soperator_checks_execution import policy as policy
from test_soperator_install_runtime_repair import compiled as runtime_compiled
from test_soperator_release_reconciler import _paths


def compiled():
    before, values = runtime_compiled()
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


def test_storage_candidate_changes_only_exact_native_scratch_default():
    before, values = compiled()
    after = storage_repair_candidate(before)
    assert {name for name in after if after[name] != before[name]} == {VALUES_FILE, OUTER_FILE}
    assert storage_repair_candidate(after) == after
    assert storage_repair_candidate(after, inverse=True) == before
    expected = copy.deepcopy(values)
    expected["nodesets"]["overrideValues"]["nodesets"][0]["slurmd"]["resources"][
        "ephemeralStorage"
    ] = "55Gi"
    assert yaml.safe_load(yaml.safe_load(after[VALUES_FILE])["data"]["values.yaml"]) == expected


@pytest.mark.parametrize("mutation", [None, "previous", "replacement", "policy", "resource"])
def test_storage_handoff_proves_exact_delta_and_both_full_values_policies(
    tmp_path, policy, mutation
):
    before, values = compiled()
    after = storage_repair_candidate(before)
    paths = _paths(tmp_path)
    for name, data in after.items():
        (paths.flux_dir / name).write_bytes(data)
    after = _files(paths.flux_dir)
    before = storage_repair_candidate(after, inverse=True)
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
        (paths.flux_dir / VALUES_FILE).write_bytes(after[VALUES_FILE].replace(b"55Gi", b"56Gi"))
    if mutation:
        with pytest.raises(RuntimeError):
            storage_reservation_handoff(repair, paths=paths, policy=target)
    else:
        result = storage_reservation_handoff(repair, paths=paths, policy=target)
        assert result["policy"] == target.sha256 and result["predecessorPolicy"] == old.sha256
        assert result["fingerprint"] == "retained"
