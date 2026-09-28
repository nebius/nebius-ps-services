from __future__ import annotations

import base64
import copy
import json
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli.deployment_observation import verify_desired_target
from nebius_cxcli.deployment_state import DeploymentGeneration


@pytest.mark.parametrize("kind", ["PersistentVolume", "PersistentVolumeClaim"])
@pytest.mark.parametrize(
    "live_size,converged",
    [("2Ti", True), ("2199023255552", True), ("1Ti", False), ("2048G", False)],
)
def test_volume_capacity_comparison_uses_quantity_value(kind, live_size, converged):
    spec = (
        {"capacity": {"storage": "2048Gi"}}
        if kind == "PersistentVolume"
        else {"resources": {"requests": {"storage": "2048Gi"}}}
    )
    spec["volumeMode"] = "Filesystem"
    document = {
        "apiVersion": "v1",
        "kind": kind,
        "metadata": {"name": "volume", "namespace": "" if kind == "PersistentVolume" else "app"},
        "spec": spec,
    }
    generation = DeploymentGeneration(
        {"deploy": {"targets": [{"target_ref": "target", "flux_dir": "flux/target"}]}},
        {
            "flux/target/kustomization.yaml": base64.b64encode(
                b"resources: [volume.yaml]"
            ).decode(),
            "flux/target/volume.yaml": base64.b64encode(yaml.safe_dump(document).encode()).decode(),
        },
    )
    live = copy.deepcopy(document)
    live["metadata"]["uid"] = "u"
    capacity = (
        live["spec"]["capacity"]
        if kind == "PersistentVolume"
        else live["spec"]["resources"]["requests"]
    )
    capacity["storage"] = live_size
    fake = SimpleNamespace(
        GRAFANA_TARGET_KUBE_CONTEXT_ENV="context",
        _run_soperator_upgrade_kubectl=lambda *a, **kw: SimpleNamespace(stdout=json.dumps(live)),
    )
    kwargs = {"generation": generation, "target_ref": "target", "kube_env": {}}
    if converged:
        assert verify_desired_target(fake, **kwargs)["ready"]
        live["spec"]["volumeMode"] = "Block"
    with pytest.raises(RuntimeError, match="not converged"):
        verify_desired_target(fake, **kwargs)
