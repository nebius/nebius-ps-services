"""Worker defaults use the upstream Nebius preset budget, not tiny templates."""

import copy

import pytest

from nebius_cxcli.soperator_config_materialization import _soperator_fit_nodeset_resources_to_group
from nebius_cxcli.soperator_wizard import soperator_wizard_settings


def worker(*, gpu=True):
    return {
        "gpu": {"enabled": gpu},
        "slurmd": {"resources": {"ephemeralStorage": "55Gi"}},
        "munge": {"resources": {"cpu": "200m", "memory": "512Mi"}},
        "sssd": {"enabled": False, "resources": {"cpu": "200m", "memory": "512Mi"}},
    }


def fit(node, *, platform="gpu-h200-sxm", preset="8gpu-128vcpu-1600gb", **kwargs):
    group = {"platform": platform, "preset": preset, **kwargs.pop("group", {})}
    _soperator_fit_nodeset_resources_to_group(
        node, group_key="worker", inputs={"node_groups": {"worker": group}}, **kwargs
    )


@pytest.mark.parametrize("platform", ["gpu-h100-sxm", "gpu-h200-sxm"])
def test_large_gpu_default_and_idempotence(platform):
    node = worker()
    fit(node, platform=platform)
    assert node["slurmd"]["resources"] == {
        "cpu": "125950m",
        "memory": "1436Gi",
        "gpu": 8,
        "ephemeralStorage": "55Gi",
    }
    assert node["nodeConfig"]["static"] == (
        "Boards=1 SocketsPerBoard=2 CoresPerSocket=32 ThreadsPerCore=2 Gres=gpu:8"
    )
    again = copy.deepcopy(node)
    fit(node, platform=platform)
    assert node == again


def test_cpu_worker_and_sssd_use_same_budget_policy():
    node = worker(gpu=False)
    node["sssd"]["enabled"] = True
    fit(node, platform="cpu-d3", preset="16vcpu-64gb")
    assert node["slurmd"]["resources"]["cpu"] == "13950m"
    assert node["slurmd"]["resources"]["memory"] == "53Gi"


def test_explicit_resources_are_preserved_and_missing_field_is_derived():
    node = worker()
    node["slurmd"]["resources"].update(cpu="32", memory="16Gi")
    fit(node)
    assert node["slurmd"]["resources"]["cpu"] == "32"
    assert node["slurmd"]["resources"]["memory"] == "16Gi"
    del node["slurmd"]["resources"]["memory"]
    fit(node)
    assert node["slurmd"]["resources"]["cpu"] == "32"
    assert node["slurmd"]["resources"]["memory"] == "1436Gi"


@pytest.mark.parametrize(
    "field,value", [("cpu", "128"), ("memory", "1600Gi"), ("cpu", "0"), ("memory", "invalid")]
)
def test_invalid_or_oversized_explicit_requests_fail(field, value):
    node = worker()
    node["slurmd"]["resources"][field] = value
    with pytest.raises(ValueError, match="worker.*resource|worker.*capacity"):
        fit(node)


def test_observed_allocatable_is_a_bound_not_a_second_percentage():
    node = worker()
    fit(node, group={"allocatable": {"cpu": "127900m", "memory": "1610493980Ki"}})
    assert node["slurmd"]["resources"]["cpu"] == "125950m"
    assert node["slurmd"]["resources"]["memory"] == "1436Gi"
    with pytest.raises(ValueError, match="worker.*capacity"):
        fit(worker(), group={"allocatable": {"cpu": "120", "memory": "1000Gi"}})


@pytest.mark.parametrize("preset", ["unknown", "1vcpu-2gb"])
def test_missing_or_insufficient_preset_fails(preset):
    with pytest.raises(ValueError, match="worker.*preset|worker.*capacity"):
        fit(worker(gpu=False), platform="cpu-d3", preset=preset)


def test_registered_bootstrap_remains_small():
    node = worker(gpu=False)
    fit(node, install_mode="registered")
    assert node["slurmd"]["resources"]["cpu"] == "500m"
    assert node["slurmd"]["resources"]["memory"] == "1024Mi"


def test_managed_profiles_leave_worker_cpu_and_memory_for_derivation():
    settings = soperator_wizard_settings()
    seen = 0
    for profile in settings.nodesets.profiles.values():
        for node in profile["chart"]["values"].get("nodesets", []):
            resources = node["slurmd"]["resources"]
            assert "static" not in node["nodeConfig"]
            assert "gresConfig" not in node["nodeConfig"]
            assert "cpu" not in resources
            assert "memory" not in resources
            assert node["munge"]["resources"]["cpu"]
            assert node["sssd"]["resources"]["memory"]
            seen += 1
    assert seen >= 4


def test_explicit_values_still_account_for_resident_sidecars():
    node = worker()
    node["slurmd"]["resources"].update(cpu="127800m", memory="16Gi")
    with pytest.raises(ValueError, match="worker.*capacity"):
        fit(node, group={"allocatable": {"cpu": "127900m", "memory": "1610493980Ki"}})


def test_guided_sssd_reserves_resources_before_final_enabled_projection():
    node = worker()
    node["sssd"]["resources"].update(cpu="1200m", memory="2048Mi")
    fit(node, sssd_enabled=True)
    assert node["slurmd"]["resources"]["cpu"] == "124950m"
    assert node["slurmd"]["resources"]["memory"] == "1434Gi"


def test_guided_sssd_disable_takes_precedence_over_stale_nodeset():
    node = worker()
    node["sssd"].update(enabled=True, resources={"cpu": "1200m", "memory": "2048Mi"})
    fit(node, sssd_enabled=False)
    assert node["slurmd"]["resources"]["cpu"] == "125950m"
    assert node["slurmd"]["resources"]["memory"] == "1436Gi"


def test_generated_ownership_follows_new_preset_but_preserves_operator_memory():
    from nebius_cxcli.soperator_worker_defaults import (
        prepare_worker_defaults,
        remember_worker_defaults,
    )

    node = {"name": "worker", **worker()}
    row = {"values": {"nodesets": [node]}}
    before = prepare_worker_defaults(row)
    fit(node, platform="gpu-h100-sxm")
    remember_worker_defaults(row, before)
    assert set(row["worker-defaults"]["worker"]) == {"cpu", "memory", "static", "gresConfig"}
    node["slurmd"]["resources"]["memory"] = "32Gi"
    before = prepare_worker_defaults(row)
    fit(node, platform="gpu-h100-sxm", preset="1gpu-16vcpu-200gb")
    remember_worker_defaults(row, before)
    assert node["slurmd"]["resources"]["cpu"] == "13950m"
    assert node["slurmd"]["resources"]["memory"] == "32Gi"
    assert node["slurmd"]["resources"]["gpu"] == 1
    assert node["nodeConfig"]["static"] == (
        "Boards=1 SocketsPerBoard=1 CoresPerSocket=8 ThreadsPerCore=2 Gres=gpu:1"
    )
    assert node["nodeConfig"]["gresConfig"] == ["AutoDetect=off Name=gpu File=/dev/nvidia0"]
    assert set(row["worker-defaults"]["worker"]) == {"cpu", "static", "gresConfig"}


@pytest.mark.parametrize("count,preset", [(1, "1gpu-16vcpu-200gb"), (8, "8gpu-128vcpu-1600gb")])
def test_gpu_profile_devices_follow_selected_preset(count, preset):
    for profile in soperator_wizard_settings().nodesets.profiles.values():
        for template in profile["chart"]["values"].get("nodesets", []):
            if template.get("gpu", {}).get("enabled") is not True:
                continue
            node = copy.deepcopy(template)
            fit(node, platform="gpu-h100-sxm", preset=preset)
            device = "/dev/nvidia0" if count == 1 else "/dev/nvidia[0-7]"
            assert node["nodeConfig"]["gresConfig"] == [f"AutoDetect=off Name=gpu File={device}"]
            assert node["slurmd"]["resources"]["gpu"] == count


@pytest.mark.parametrize("tracked", [False, True])
def test_operator_gpu_devices_preserved_without_mutable_provenance(tracked):
    from nebius_cxcli.soperator_worker_defaults import (
        prepare_worker_defaults,
        remember_worker_defaults,
    )

    node = {"name": "worker", **worker()}
    row = {"values": {"nodesets": [node]}}
    before = prepare_worker_defaults(row)
    fit(node, platform="gpu-h100-sxm")
    remember_worker_defaults(row, before)
    if not tracked:
        row["worker-defaults"]["worker"].pop("gresConfig")
    explicit = ["AutoDetect=off Name=gpu Type=custom File=/dev/nvidia0"]
    node["nodeConfig"]["gresConfig"] = explicit.copy()
    before = prepare_worker_defaults(row)
    fit(node, platform="gpu-h100-sxm", preset="1gpu-16vcpu-200gb")
    remember_worker_defaults(row, before)
    assert node["nodeConfig"]["gresConfig"] == explicit
    assert "gresConfig" not in row.get("worker-defaults", {}).get("worker", {})


def test_real_wizard_preset_change_updates_generated_devices_after_yaml_roundtrip():
    import yaml

    from nebius_cxcli import cli
    from nebius_cxcli.components import component_entries, soperator_install_entry
    from nebius_cxcli.soperator_config_materialization import (
        _materialize_soperator_component_defaults,
    )

    payload = cli._starter_component_payload(
        client_name="example",
        tenant_id="tenant-example",
        project_id="project-example",
        region_id="eu-north1",
        email=None,
        selected_infra={"mk8s", "sfs"},
        selected_apps={"soperator"},
        infra_entries=component_entries("infra"),
        app_entries=(
            soperator_install_entry("4.1.9", chart_repo="oci://example.invalid/soperator"),
        ),
    )
    payload = yaml.safe_load(yaml.safe_dump(payload))
    inputs = next(row["inputs"] for row in payload["infra"]["components"] if row["id"] == "mk8s")
    inputs["node_group_defaults"]["gpu"].update(platform="gpu-h100-sxm", preset="1gpu-16vcpu-200gb")
    _materialize_soperator_component_defaults(payload)
    row = next(row for row in payload["apps"]["charts"] if row["id"] == "soperator")
    node = next(node for node in row["values"]["nodesets"] if node["name"] == "worker")
    assert node["slurmd"]["resources"]["gpu"] == 1
    assert node["nodeConfig"]["gresConfig"] == ["AutoDetect=off Name=gpu File=/dev/nvidia0"]
    before = copy.deepcopy(payload)
    _materialize_soperator_component_defaults(payload)
    assert payload == before


def test_in_place_device_edit_does_not_modify_recorded_generated_preimage():
    from nebius_cxcli.soperator_worker_defaults import (
        prepare_worker_defaults,
        remember_worker_defaults,
    )

    node = {"name": "worker", **worker()}
    row = {"values": {"nodesets": [node]}}
    before = prepare_worker_defaults(row)
    fit(node)
    remember_worker_defaults(row, before)
    generated = copy.deepcopy(row["worker-defaults"]["worker"]["gresConfig"])
    node["nodeConfig"]["gresConfig"][0] = "AutoDetect=off Name=gpu File=/dev/nvidia0"
    assert row["worker-defaults"]["worker"]["gresConfig"] == generated
    assert prepare_worker_defaults(row)["worker"]["gresConfig"] == node["nodeConfig"]["gresConfig"]


@pytest.mark.parametrize(
    "invalid",
    [
        [],
        {"worker": {"password": "invalid"}},
        {"worker": {"cpu": 8}},
        {"worker": {"gresConfig": "not-a-list"}},
        {"worker": {"gresConfig": []}},
        {"worker": {"gresConfig": [1]}},
        {"worker": {"gresConfig": ["x" * 513]}},
    ],
)
def test_generated_ownership_rejects_invalid_metadata(invalid):
    from nebius_cxcli.soperator_worker_defaults import validate_worker_defaults

    with pytest.raises(ValueError, match="worker-defaults"):
        validate_worker_defaults({"worker-defaults": invalid})


def test_unknown_preset_topology_requires_explicit_physical_description():
    node = worker()
    with pytest.raises(ValueError, match="verified physical CPU topology"):
        fit(node, platform="unverified-platform")
    node = worker()
    node["nodeConfig"] = {"static": "Boards=1 SocketsPerBoard=2 CoresPerSocket=32 ThreadsPerCore=2"}
    fit(node, platform="unverified-platform")
    assert node["slurmd"]["resources"]["cpu"] == "125950m"


def test_removing_default_provenance_pins_current_numeric_value():
    from nebius_cxcli.soperator_worker_defaults import (
        prepare_worker_defaults,
        remember_worker_defaults,
    )

    node = {"name": "worker", **worker()}
    row = {"values": {"nodesets": [node]}}
    before = prepare_worker_defaults(row)
    fit(node)
    remember_worker_defaults(row, before)
    del row["worker-defaults"]["worker"]["cpu"]
    before = prepare_worker_defaults(row)
    assert node["slurmd"]["resources"]["cpu"] == "125950m"
    fit(node)
    remember_worker_defaults(row, before)
    assert "cpu" not in row["worker-defaults"]["worker"]


def test_frozen_generation_does_not_rederive_owned_resources(monkeypatch):
    from nebius_cxcli import soperator_config_materialization as materialization

    node = {"name": "worker", **worker()}
    node["slurmd"]["resources"].update(cpu="32", memory="16Gi")
    node["nodeConfig"] = {"gresConfig": ["AutoDetect=off Name=gpu File=/dev/nvidia[0-7]"]}
    row = {
        "id": "soperator",
        "instance_id": "cluster",
        "enabled": True,
        "values": {"nodesets": [node]},
        "worker-defaults": {
            "worker": {"cpu": "32", "memory": "16Gi", **copy.deepcopy(node["nodeConfig"])}
        },
    }
    payload = {"apps": {"charts": [row]}}
    original = copy.deepcopy(payload)
    monkeypatch.setattr(
        materialization, "_materialize_soperator_generated_defaults", lambda _payload: False
    )
    with materialization.frozen_soperator_topology():
        assert materialization._materialize_soperator_component_defaults(payload) is False
    assert payload == original


def test_kubernetes_fractional_and_decimal_memory_quantities_are_preserved():
    node = worker()
    node["munge"]["resources"]["memory"] = "0.5Gi"
    node["slurmd"]["resources"]["memory"] = "1.5Gi"
    fit(node)
    assert node["slurmd"]["resources"]["memory"] == "1.5Gi"
    node = worker()
    node["munge"]["resources"]["memory"] = "512M"
    fit(node)
    assert node["slurmd"]["resources"]["memory"] == "1436Gi"
