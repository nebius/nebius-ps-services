import copy

import pytest
import yaml

from nebius_cxcli.soperator_install_checks_repair import collector_repair_candidate
from nebius_cxcli.soperator_install_render_repair import OUTER_FILE, VALUES_FILE
from nebius_cxcli.soperator_jail_logs_binding import JAIL_LOGS_RELEASE, jail_logs_binding_operations
from soperator_fixtures import sample_jail_logs_binding


@pytest.mark.parametrize("public", [True, False])
@pytest.mark.parametrize("slot", ["slot-a", "slot-b"])
def test_binding_uses_selected_slot_and_intersects_storage_placement(public, slot):
    values, docs = sample_jail_logs_binding()
    values["observability"]["publicEndpointEnabled"] = public
    docs[1]["spec"]["local"]["path"] = "/mnt/jail-store/rootfs/" + slot
    before = copy.deepcopy((values, docs))
    operations = jail_logs_binding_operations(values, docs)
    assert (values, docs) == before
    affinity = next(
        x["value"] for x in operations if x["op"] == "replace" and x["path"].endswith("affinity")
    )
    terms = affinity["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][
        "nodeSelectorTerms"
    ]
    assert terms == [
        {
            "matchExpressions": [
                {"key": "node-group", "operator": "In", "values": ["system"]},
                {"key": "jail", "operator": "In", "values": ["true"]},
            ]
        }
    ]
    assert {
        "op": "replace",
        "path": f"/spec/values/extraVolumes/{int(public)}/hostPath/path",
        "value": "/mnt/jail-store/rootfs/" + slot,
    } in operations
    assert {
        "op": "test",
        "path": f"/spec/values/extraVolumes/{int(public)}/name",
        "value": "jail",
    } in operations


@pytest.mark.parametrize(
    "mutation",
    [
        "filter",
        "duplicate-filter",
        "affinity",
        "claim",
        "duplicate-pv",
        "namespace",
        "volume-name",
        "protection",
        "non-local",
        "traversal",
        "storage-placement",
        "shared-override",
    ],
)
def test_binding_rejects_unproven_authority(mutation):
    values, docs = sample_jail_logs_binding()
    sc = values["slurmCluster"]["overrideValues"]
    if mutation == "filter":
        sc["k8sNodeFilters"] = []
    elif mutation == "duplicate-filter":
        sc["k8sNodeFilters"] *= 2
    elif mutation == "affinity":
        sc["k8sNodeFilters"][0]["affinity"] = {}
    elif mutation == "claim":
        sc["volumeSources"] = []
    elif mutation == "duplicate-pv":
        docs.append(copy.deepcopy(docs[1]))
    elif mutation == "namespace":
        docs[0]["metadata"]["namespace"] = "foreign"
    elif mutation == "volume-name":
        docs[0]["spec"]["volumeName"] = "foreign"
    elif mutation == "protection":
        docs[1]["metadata"]["labels"] = {}
    elif mutation == "non-local":
        docs[1]["spec"].pop("local")
    elif mutation == "traversal":
        docs[1]["spec"]["local"]["path"] = "/mnt/../etc"
    elif mutation == "storage-placement":
        docs[1]["spec"].pop("nodeAffinity")
    else:
        values["observability"]["opentelemetry"] = {"logs": {"overrideValues": {"custom": True}}}
    with pytest.raises(ValueError, match="Jail-log binding"):
        jail_logs_binding_operations(values, docs)


def test_collector_repair_preserves_all_other_files_values_and_patch_operations():
    values, docs = sample_jail_logs_binding()
    original_operations = [
        {"op": "replace", "path": "/metadata/name", "value": "cxcli-" + JAIL_LOGS_RELEASE}
    ]
    outer = {
        "spec": {
            "values": values,
            "postRenderers": [
                {
                    "kustomize": {
                        "patches": [
                            {
                                "target": {"name": JAIL_LOGS_RELEASE},
                                "patch": yaml.safe_dump(original_operations),
                            }
                        ]
                    }
                }
            ],
        }
    }
    files = {
        VALUES_FILE: yaml.safe_dump({"data": {"values.yaml": yaml.safe_dump(values)}}).encode(),
        OUTER_FILE: yaml.safe_dump(outer).encode(),
        "soperator-nebius-adapter.yaml": yaml.safe_dump_all(docs).encode(),
        "keep": b"all other authority",
    }
    before = copy.deepcopy(files)
    after = collector_repair_candidate(files)
    assert files == before
    assert {key for key in after if after[key] != files[key]} == {OUTER_FILE}
    assert collector_repair_candidate(after) == after
    result = yaml.safe_load(after[OUTER_FILE])
    assert result["spec"]["values"] == values
    patch = yaml.safe_load(result["spec"]["postRenderers"][0]["kustomize"]["patches"][0]["patch"])
    assert patch == original_operations + jail_logs_binding_operations(values, docs)
    patch[-1]["value"] = "/mnt/foreign"
    result["spec"]["postRenderers"][0]["kustomize"]["patches"][0]["patch"] = yaml.safe_dump(patch)
    after[OUTER_FILE] = yaml.safe_dump(result).encode()
    with pytest.raises(RuntimeError, match="custom collector binding"):
        collector_repair_candidate(after)


def test_binding_follows_active_claim_selection_with_both_slots_present():
    values, docs = sample_jail_logs_binding()
    passive = copy.deepcopy(docs)
    passive[0]["metadata"]["name"] = "passive-jail"
    passive[0]["spec"]["volumeName"] = "passive-jail-pv"
    passive[1]["metadata"]["name"] = "passive-jail-pv"
    passive[1]["spec"]["claimRef"]["name"] = "passive-jail"
    passive[1]["spec"]["local"]["path"] = "/mnt/jail-store/rootfs/slot-b"
    docs += passive
    values["slurmCluster"]["overrideValues"]["volumeSources"][0]["persistentVolumeClaim"][
        "claimName"
    ] = "passive-jail"
    operations = jail_logs_binding_operations(values, docs)
    assert (
        next(x["value"] for x in operations if x["path"].endswith("hostPath/path"))
        == "/mnt/jail-store/rootfs/slot-b"
    )
