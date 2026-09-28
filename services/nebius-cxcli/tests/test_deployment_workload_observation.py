from __future__ import annotations

import base64
import copy
import json
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli.deployment_observation import verify_desired_target
from nebius_cxcli.deployment_state import DeploymentGeneration


def _generation(document):
    return DeploymentGeneration(
        {"deploy": {"targets": [{"target_ref": "target", "flux_dir": "flux/target"}]}},
        {
            "flux/target/kustomization.yaml": base64.b64encode(
                b"resources: [resource.yaml]"
            ).decode(),
            "flux/target/resource.yaml": base64.b64encode(
                yaml.safe_dump(document).encode()
            ).decode(),
        },
    )


@pytest.fixture
def workload():
    desired = {
        "apiVersion": "apps/v1",
        "kind": "DaemonSet",
        "metadata": {"name": "mount", "namespace": "app"},
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "mount",
                            "image": "example.invalid/mount:1",
                            "env": [
                                {"name": "OPTIONAL", "value": ""},
                                {
                                    "name": "NODE",
                                    "valueFrom": {"fieldRef": {"fieldPath": "spec.nodeName"}},
                                },
                            ],
                        }
                    ]
                }
            }
        },
    }
    live = copy.deepcopy(desired)
    live["metadata"]["uid"] = "u"
    container = live["spec"]["template"]["spec"]["containers"][0]
    container.update(terminationMessagePath="/dev/termination-log", resources={})
    container["env"][0].pop("value")
    container["env"][1]["valueFrom"]["fieldRef"]["apiVersion"] = "v1"
    fake = SimpleNamespace(
        GRAFANA_TARGET_KUBE_CONTEXT_ENV="context",
        _run_soperator_upgrade_kubectl=lambda *a, **kw: SimpleNamespace(stdout=json.dumps(live)),
    )
    return desired, live, container, fake


def test_workload_defaulted_list_members_converge(workload):
    desired, _, _, fake = workload
    assert verify_desired_target(
        fake, generation=_generation(desired), target_ref="target", kube_env={}
    )["ready"]


@pytest.mark.parametrize(
    "drift",
    ["image", "env-value", "env-source", "extra-container", "order", "retained-owned-field"],
)
def test_workload_owned_fields_and_list_inventory_remain_strict(workload, drift):
    desired, live, container, fake = workload
    previous = None
    if drift == "image":
        container["image"] = "example.invalid/mount:2"
    elif drift == "env-value":
        container["env"][0]["value"] = "changed"
    elif drift == "env-source":
        container["env"][0]["valueFrom"] = {"fieldRef": {"fieldPath": "metadata.name"}}
    elif drift == "extra-container":
        live["spec"]["template"]["spec"]["containers"].append({"name": "unexpected"})
    elif drift == "order":
        container["env"].reverse()
    else:
        old = copy.deepcopy(desired)
        old["spec"]["template"]["spec"]["containers"][0]["workingDir"] = "/owned"
        container["workingDir"] = "/owned"
        previous = _generation(old)
    with pytest.raises(RuntimeError, match="not converged"):
        verify_desired_target(
            fake,
            generation=_generation(desired),
            previous=previous,
            target_ref="target",
            kube_env={},
        )


def test_helm_values_remain_exact_inside_lists():
    desired = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": "app", "namespace": "app"},
        "spec": {"values": {"rows": [{"name": "owned"}]}},
    }
    live = copy.deepcopy(desired)
    live["metadata"]["uid"] = "u"
    live["spec"]["values"]["rows"][0]["unadmitted"] = True
    fake = SimpleNamespace(
        GRAFANA_TARGET_KUBE_CONTEXT_ENV="context",
        _run_soperator_upgrade_kubectl=lambda *a, **kw: SimpleNamespace(stdout=json.dumps(live)),
    )
    with pytest.raises(RuntimeError, match="not converged"):
        verify_desired_target(
            fake, generation=_generation(desired), target_ref="target", kube_env={}
        )
