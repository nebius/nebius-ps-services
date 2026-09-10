"""Managed H200 CPU-time quotas must not hide physical CPU IDs."""

import copy

import pytest

from nebius_cxcli.soperator_config_materialization import _soperator_fit_nodeset_resources_to_group

OLD = "Boards=1 SocketsPerBoard=1 CoresPerSocket=32 ThreadsPerCore=1 Gres=gpu:8"
EXPECTED = "Boards=1 SocketsPerBoard=2 CoresPerSocket=32 ThreadsPerCore=2 Gres=gpu:8"


def node():
    return {
        "gpu": {"enabled": True},
        "nodeConfig": {"static": OLD},
        "slurmd": {
            "resources": {"cpu": "32", "memory": "16Gi", "ephemeralStorage": "55Gi", "gpu": 8}
        },
    }


def materialize(value, platform="gpu-h200-sxm"):
    _soperator_fit_nodeset_resources_to_group(
        value,
        group_key="worker",
        inputs={"node_groups": {"worker": {"platform": platform, "preset": "8gpu-128vcpu-1600gb"}}},
    )


def test_h200_cpu_quota_does_not_exclude_physical_threads():
    value = node()
    resources = copy.deepcopy(value["slurmd"])
    materialize(value)
    assert value["nodeConfig"]["static"] == EXPECTED
    assert value["slurmd"] == resources
    again = copy.deepcopy(value)
    materialize(value)
    assert value == again


def test_unknown_platform_does_not_inherit_h200_topology():
    value = node()
    materialize(value, platform="unverified-platform")
    assert value["nodeConfig"]["static"] == OLD


def test_conflicting_h200_custom_topology_is_rejected():
    value = node()
    value["nodeConfig"]["static"] = (
        "Boards=1 SocketsPerBoard=4 CoresPerSocket=8 ThreadsPerCore=1 Gres=gpu:8"
    )
    with pytest.raises(ValueError, match="H200.*topology"):
        materialize(value)
