from __future__ import annotations

import copy
from types import SimpleNamespace as NS

import pytest

from nebius_cxcli import nat_gateway_wizard as wizard
from nebius_cxcli.nat_gateway_quota import plan_requirements
from nebius_cxcli.provider_options import OptionChoice, ProviderOptionLookup
from test_nat_gateway import config


def test_cpu_filter_uses_resources_not_platform_names(monkeypatch):
    lookup = ProviderOptionLookup()
    lookup._compute_platform_preset_cache = {
        ("project-test", "future-platform"): (NS(gpu_count=0, vcpu_count=4),),
        ("project-test", "cpu-misleading"): (NS(gpu_count=1, vcpu_count=8),),
    }
    inventory = (OptionChoice("future-platform", "CPU"), OptionChoice("cpu-misleading", "GPU"))
    monkeypatch.setattr(lookup, "_resolve_project_compute_platform_inventory", lambda _: inventory)
    choices = lookup.resolve(
        provider="compute_platforms",
        args={"project_id": "project-test", "cpu_only": True},
        payload={},
        field_path="inputs.platform",
    )
    assert [choice.value for choice in choices] == ["future-platform"]
    unfiltered = lookup.resolve(
        provider="compute_platforms",
        args={"project_id": "project-test"},
        payload={},
        field_path="inputs.platform",
    )
    assert len(unfiltered) == 2  # CPU-only cache cannot poison another component.


def test_cpu_only_choices_cannot_fall_back_to_unverified_plugins(monkeypatch):
    from nebius_cxcli import provider_options

    lookup = ProviderOptionLookup()
    monkeypatch.setattr(lookup, "_resolve_compute_platforms", lambda **kw: ())
    monkeypatch.setattr(
        provider_options,
        "_load_option_plugins",
        lambda *a: pytest.fail("Unverified plugin fallback"),
    )
    assert (
        lookup.resolve(
            provider="compute_platforms",
            args={"cpu_only": True},
            payload={},
            field_path="inputs.platform",
        )
        == []
    )


def test_plan_includes_shared_mk8s_worker_growth():
    template = {
        "resources": {"platform": "future-cpu", "preset": "4"},
        "boot_disk": {"type": "NETWORK_SSD", "size_gibibytes": 20},
        "network_interfaces": [{"public_ip_address": {}}],
    }
    plan = {
        "resource_changes": [
            {
                "type": "nebius_mk8s_v1_node_group",
                "address": "module.k8s.workers",
                "change": {
                    "actions": ["update"],
                    "before": {"fixed_node_count": 2, "template": template},
                    "after": {"fixed_node_count": 3, "template": template},
                },
            }
        ]
    }
    requirements = plan_requirements(config(), plan, sdk_factory=QuotaSDK)
    assert {r.quota_name: r.required for r in requirements} == {
        "compute.instance.count": 1,
        "compute.instance.non-gpu.vcpu": 4,
        "compute.disk.count": 1,
        "compute.disk.size.network-ssd": 20 * 1024**3,
        "vpc.ipv4-address.public.count": 1,
    }


class WizardSDK:
    def __init__(self, project):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def cpu_preset(self, *args):
        return {"vcpu_count": 4, "gpu_count": 0}

    def image_choices(self, *args):
        return [{"id": "image-test", "name": "Ubuntu", "minimum_gib": 25}]

    def image(self, *args, **kwargs):
        return NS()

    def network_cidrs(self, *args):
        return ["10.0.0.0/8"]

    def validate_subnet_cidr(self, *args):
        pass

    def route_policy(self, *args):
        return config()["infra"]["components"][0]["routing"]["policies"]["default"]

    def subnets(self, *args):
        return [
            NS(
                metadata=NS(id="subnet-work", labels={}),
                status=NS(route_table=NS(id="table-original")),
            )
        ]

    def network(self, *args):
        return NS(status=NS(default_route_table_id="table-original"))


class Lookup:
    def resolve(self, *, provider, **kwargs):
        value = {
            "compute_platforms": "future-platform",
            "compute_platform_presets": "6vcpu-24gb",
            "compute_boot_disk_types": "NETWORK_SSD",
            "project_networks": "network-test",
        }[provider]
        return [OptionChoice(value, value, metadata={"name": "network"})]

    def last_error(self):
        return None


def prompt(path, default, *, choices, **kwargs):
    if path.endswith("routing.targets"):
        return [choices[0].value], False
    if choices:
        return next((c.value for c in choices if c.value == default), choices[0].value), False
    return default, False


def test_wizard_live_defaults_image_minimum_and_quota_draft(monkeypatch):
    payload = config()
    original = copy.deepcopy(payload)
    emitted = []
    assessed = []
    monkeypatch.setattr(
        wizard, "assess_live_quotas", lambda candidate, **kw: assessed.append(candidate)
    )
    monkeypatch.setattr(wizard, "format_quota_report_lines", lambda *a, **kw: ["Quota unresolved"])
    result = wizard.configure_nat(
        payload,
        payload["infra"]["components"][0],
        prefix="nat",
        lookup=Lookup(),
        prompt=prompt,
        is_back=lambda _: False,
        emit=emitted.append,
        sdk_factory=WizardSDK,
    )
    assert result["inputs"]["platform"] == "future-platform"
    assert result["inputs"]["preset"] == "6vcpu-24gb"
    assert result["inputs"]["boot_disk_size_gib"] == 25
    assert result["routing"]["targets"][0]["delegated"] is True
    assert assessed and any("unavailable" in line for line in emitted)
    assert payload == original


def test_wizard_navigation_does_not_partially_change_component():
    payload = config()
    original = copy.deepcopy(payload)
    with pytest.raises(wizard.WizardNavigation):
        wizard.configure_nat(
            payload,
            payload["infra"]["components"][0],
            prefix="nat",
            lookup=Lookup(),
            prompt=lambda *a, **kw: ("BACK", False),
            is_back=lambda value: value == "BACK",
            emit=lambda _: None,
        )
    assert payload == original


def test_deployment_network_choice_preserves_terraform_ownership(monkeypatch):
    payload = config(owner="terraform")
    monkeypatch.setattr(wizard, "assess_live_quotas", lambda *a, **kw: None)
    monkeypatch.setattr(wizard, "format_quota_report_lines", lambda *a, **kw: [])
    observed_network_choices = []

    def choose(path, default, **kwargs):
        if path.endswith("inputs.network_id"):
            observed_network_choices.extend(c.value for c in kwargs["choices"])
        return prompt(path, default, **kwargs)

    result = wizard.configure_nat(
        payload,
        payload["infra"]["components"][0],
        prefix="nat",
        lookup=Lookup(),
        prompt=choose,
        is_back=lambda _: False,
        emit=lambda _: None,
        sdk_factory=WizardSDK,
    )
    assert observed_network_choices == ["planned:network"]
    assert result["routing"]["targets"][0]["owner"] == "terraform"
    assert result["bindings"]["inputs.network_id"]["source_instance"] == "network"


class QuotaSDK(WizardSDK):
    def preset_resources(self, platform, preset):
        return {"gpu_count": 0, "vcpu_count": int(preset)}

    def cpu_preset(self, platform, preset):
        return self.preset_resources(platform, preset)


def test_plan_quota_demand_noop_growth_and_replacement():
    before = {"resources": {"platform": "future-cpu", "preset": "4"}}
    after = {"resources": {"platform": "future-cpu", "preset": "8"}}

    def demand(actions):
        rows = plan_requirements(
            config(),
            {
                "resource_changes": [
                    {
                        "type": "nebius_compute_v1_instance",
                        "address": "module.nat.vm",
                        "change": {"actions": actions, "before": before, "after": after},
                    }
                ]
            },
            sdk_factory=QuotaSDK,
        )
        return {r.quota_name: r.required for r in rows}

    assert demand(["no-op"]) == {}
    assert demand(["update"]) == {"compute.instance.non-gpu.vcpu": 4}
    assert demand(["delete", "create"]) == {
        "compute.instance.count": 1,
        "compute.instance.non-gpu.vcpu": 8,
    }
    after["preemptible"] = {}
    assert demand(["delete", "create"]) == {
        "compute.instance.preemptible.count": 1,
        "compute.instance.non-gpu.vcpu": 8,
    }


def test_plan_counts_implicit_pools_and_new_network_routing():
    def change(kind, after):
        return {"type": kind, "address": kind, "change": {"actions": ["create"], "after": after}}

    resources = [
        change("nebius_vpc_v1_network", {}),
        change(
            "nebius_vpc_v1_subnet",
            {"ipv4_private_pools": {"pools": [{"cidrs": [{"cidr": "/28"}]}]}},
        ),
    ]
    rows = plan_requirements(config(), {"resource_changes": resources})
    assert {r.quota_name: r.required for r in rows} == {
        "vpc.network.count": 1,
        "vpc.route-table.count": 1,
        "vpc.route.count": 1,
        "vpc.subnet.count": 1,
        "vpc.pool.count": 1,
    }
