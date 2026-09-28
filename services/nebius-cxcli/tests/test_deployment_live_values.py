import copy

import pytest

from nebius_cxcli.deployment_live_values import authored_values
from nebius_cxcli.soperator_adapter import (
    compile_upstream_soperator_values,
    render_soperator_adapter_documents,
    soperator_adapter_state_from_documents,
)
from test_soperator_upstream_adapter import _RELEASE, _values


@pytest.mark.parametrize("profile", ["standard", "fast-dev-test"])
def test_live_source_round_trip_preserves_custom_settings_and_physical_bindings(profile):
    old = _values()
    old["deploymentProfile"] = profile
    old["slurmNodes"]["controller"]["customInitContainers"] = [
        {"name": "custom-check", "image": "example.invalid/check:v1"}
    ]
    upstream, _ = compile_upstream_soperator_values(old, release=_RELEASE)
    docs, _ = render_soperator_adapter_documents(old, release=_RELEASE)
    adapter = soperator_adapter_state_from_documents(docs)
    desired = copy.deepcopy(old)
    desired["nodesets"] = []
    restored = authored_values(desired, upstream, adapter, release=_RELEASE)
    assert isinstance(restored["nodesets"], list) and restored["nodesets"]
    actual, _ = compile_upstream_soperator_values(restored, release=_RELEASE)
    assert actual == upstream
    documents, _ = render_soperator_adapter_documents(restored, release=_RELEASE)
    assert soperator_adapter_state_from_documents(documents) == adapter


def test_modified_generated_gate_is_rejected_instead_of_discarded():
    old = _values()
    upstream, _ = compile_upstream_soperator_values(old, release=_RELEASE)
    docs, _ = render_soperator_adapter_documents(old, release=_RELEASE)
    adapter = soperator_adapter_state_from_documents(docs)
    upstream["slurmCluster"]["overrideValues"]["slurmNodes"]["controller"]["customInitContainers"][
        0
    ]["image"] = "altered"
    restored = authored_values(old, upstream, adapter, release=_RELEASE)
    with pytest.raises(ValueError, match="collides"):
        compile_upstream_soperator_values(restored, release=_RELEASE)


def test_observed_source_does_not_inherit_new_operator_controls():
    old = _values()
    upstream, _ = compile_upstream_soperator_values(old, release=_RELEASE)
    docs, _ = render_soperator_adapter_documents(old, release=_RELEASE)
    adapter = soperator_adapter_state_from_documents(docs)
    desired = copy.deepcopy(old)
    desired["controllerManager"] = {"replicas": 99}
    desired["priorityClasses"] = {"arbitrary": "new"}
    restored = authored_values(desired, upstream, adapter, release=_RELEASE)
    actual, _ = compile_upstream_soperator_values(restored, release=_RELEASE)
    assert actual == upstream
    assert restored.get("controllerManager", {}).get("replicas") != 99
