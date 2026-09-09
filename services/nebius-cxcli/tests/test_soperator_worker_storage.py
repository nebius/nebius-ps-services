import copy
from pathlib import Path

import pytest
import yaml

from nebius_cxcli import soperator_config_materialization as materialization


@pytest.mark.parametrize("configured", [None, "80Gi", "120Gi"])
def test_render_worker_scratch_uses_native_field_and_preserves_allocations(configured):
    resources = {"cpu": "32", "memory": "16Gi", "gpu": 8}
    if configured is not None:
        resources["ephemeralStorage"] = configured
    original = copy.deepcopy(resources)
    worker = {"name": "worker", "slurmd": {"resources": resources}}
    payload = {
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "enabled": True,
                    "target": "mk8s",
                    "values": {"nodesets": [worker]},
                }
            ]
        }
    }
    materialization._materialize_soperator_render_only_values(payload)
    assert resources == {**original, "ephemeralStorage": configured or "55Gi"}
    assert "ephemeral-storage" not in resources
    saved = copy.deepcopy(payload)
    materialization._materialize_soperator_render_only_values(payload)
    assert payload == saved


def test_all_gpu_wizard_profiles_have_native_image_import_allowance():
    document = yaml.safe_load(
        Path(materialization.__file__).with_name("soperator_wizard.yaml").read_text()
    )
    allowances = {True: [], False: []}

    def walk(value):
        if isinstance(value, dict):
            if "slurmd" in value and "gpu" in value:
                allowances[value["gpu"]["enabled"]].append(
                    value["slurmd"]["resources"]["ephemeralStorage"]
                )
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(document)
    assert allowances[True] == ["55Gi"] * 3
    assert allowances[False] == ["10Gi"] * 3
