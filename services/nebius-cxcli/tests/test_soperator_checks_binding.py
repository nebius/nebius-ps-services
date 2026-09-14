import copy

import pytest
import yaml

from nebius_cxcli.soperator_checks_binding import (
    CHECKS_RELEASE,
    auxiliary_post_renderers,
    bind_auxiliary_cluster,
    bind_checks_jail,
    checks_post_renderers,
)


def test_jail_binding_preserves_extra_volumes_and_input():
    values = {
        "jobContainer": {
            "volumes": [
                {"name": "jail", "persistentVolumeClaim": {"claimName": "jail-pvc"}},
                {"name": "extra", "emptyDir": {}},
            ]
        },
        "checks": {"custom": {"enabled": False}},
    }
    old = copy.deepcopy(values)
    result = bind_checks_jail(values, "jail-rootfs-slot-b-pvc", [])
    assert values == old
    assert (
        result["jobContainer"]["volumes"][0]["persistentVolumeClaim"]["claimName"]
        == "jail-rootfs-slot-b-pvc"
    )
    assert result["jobContainer"]["volumes"][1] == old["jobContainer"]["volumes"][1]


@pytest.mark.parametrize(
    "volumes",
    [
        [],
        [{"name": "jail", "hostPath": {"path": "/"}}],
        [
            {"name": "jail", "persistentVolumeClaim": {"claimName": "foreign"}},
        ],
    ],
)
def test_jail_binding_rejects_non_authoritative_volumes(volumes):
    with pytest.raises(ValueError):
        bind_checks_jail({"jobContainer": {"volumes": volumes}}, "active-jail", [])


def test_auxiliary_patch_tests_upstream_claim_and_defers_only_in_operation():
    steady = checks_post_renderers("active-jail", [])
    quiet = checks_post_renderers("active-jail", [], suspended=True)

    def patches(render):
        return yaml.safe_load(render[0]["kustomize"]["patches"][0]["patch"])

    assert patches(steady)[-1]["value"] == "active-jail"
    assert patches(quiet)[:-2] == patches(steady)
    assert patches(quiet)[-1] == {"op": "replace", "path": "/spec/suspend", "value": True}


def test_auxiliary_cluster_binding_is_canonical_and_preserves_frozen_values():
    values = {
        "slurmCluster": {"overrideValues": {"clusterName": "lab"}},
        "soperatorActiveChecks": {
            "enabled": True,
            "overrideValues": bind_checks_jail({}, "active-jail", []),
        },
    }
    before = copy.deepcopy(values)
    outer = {
        "spec": {
            "postRenderers": [
                {
                    "kustomize": {
                        "patches": [
                            {
                                "target": {"name": CHECKS_RELEASE},
                                "patch": yaml.safe_dump(
                                    [
                                        {
                                            "op": "add",
                                            "path": "/spec/postRenderers",
                                            "value": checks_post_renderers("active-jail", []),
                                        }
                                    ]
                                ),
                            }
                        ]
                    }
                }
            ]
        }
    }
    bind_auxiliary_cluster(outer, values)
    bound = copy.deepcopy(outer)
    bind_auxiliary_cluster(outer, values)
    assert outer == bound and values == before
    child = yaml.safe_load(outer["spec"]["postRenderers"][0]["kustomize"]["patches"][0]["patch"])[
        0
    ]["value"]
    assert child == auxiliary_post_renderers(values)
    ops = yaml.safe_load(child[0]["kustomize"]["patches"][0]["patch"])
    assert [op["value"] for op in ops if op["op"] == "replace"] == [
        "active-jail",
        "lab-slurm-configs",
        "lab-munge",
    ]
    assert [op["value"] for op in ops if op["op"] == "test"] == [
        "jail",
        "jail-pvc",
        "slurm-configs",
        "soperator-slurm-configs",
        "munge-key",
        "soperator-munge",
    ]
    child[0]["kustomize"]["patches"][0]["patch"] = "[]"
    outer["spec"]["postRenderers"][0]["kustomize"]["patches"][0]["patch"] = yaml.safe_dump(
        [{"op": "add", "path": "/spec/postRenderers", "value": child}]
    )
    with pytest.raises(ValueError, match="differs"):
        bind_auxiliary_cluster(outer, values)
